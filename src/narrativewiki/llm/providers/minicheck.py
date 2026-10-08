"""Local MiniCheck-FT5 inference, reached only through llm/client.py.

The upstream FT5 scorer compares decoder logits for labels 0 and 1 (token IDs 3/209).
We use that same forward pass, with a pinned checkpoint in models.yaml. Documents are
split without tokenizer truncation; maximum chunk support is reported alongside every
chunk score. This is a classifier score, not a generated or calibrated confidence.
Optional torch/transformers imports never affect ordinary chat-provider runs.
"""
from __future__ import annotations

import importlib.util
import json
import math
from functools import lru_cache
from pathlib import Path

from ... import paths
from .base import Completion, Health, ProviderError


@lru_cache(maxsize=2)
def _load(model_id: str, revision: str, device: str, cache_dir: str):
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, cache_dir=cache_dir)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_id, revision=revision, cache_dir=cache_dir,
        ).to(device).eval()
        return torch, tokenizer, model
    except Exception as exc:
        raise ProviderError(f"MiniCheck load failed: {exc}. Install the project's nli extra.") from exc


def _chunks(document, claim, tokenizer, max_tokens):
    """Preserve all words and the full hypothesis; refuse an indivisible oversized input."""
    pending = [document]
    while pending:
        chunk = pending.pop()
        text = "predict: " + tokenizer.eos_token.join([chunk, claim])
        ids = tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
        if len(ids) <= max_tokens:
            yield chunk, text
            continue
        pieces = chunk.splitlines()
        if len(pieces) < 2:
            pieces = chunk.split()
            separator = " "
        else:
            separator = "\n"
        if len(pieces) < 2:
            raise ProviderError("MiniCheck input cannot fit without truncating the hypothesis or evidence")
        midpoint = len(pieces) // 2
        pending.extend([separator.join(pieces[midpoint:]), separator.join(pieces[:midpoint])])


def complete(profile, prompt, *, system=None, json_mode=False, timeout=300, options=None):
    if system:
        raise ProviderError("MiniCheck accepts document/hypothesis pairs, not chat instructions")
    cfg = {**profile.options, **(options or {})}
    revision = cfg.get("revision")
    if not revision:
        raise ProviderError("MiniCheck requires a pinned checkpoint revision in models.yaml")
    try:
        payload = json.loads(prompt)
        documents, claim = payload["documents"], payload["claim"]
        if not isinstance(claim, str) or not claim.strip() or not documents or not isinstance(documents, list):
            raise ValueError("nonempty documents and claim required")
        if any(not isinstance(doc, str) or not doc.strip() for doc in documents):
            raise ValueError("documents must be nonempty strings")
        max_tokens = int(cfg.get("max_input_tokens", 2048))
        if max_tokens < 1:
            raise ValueError("max_input_tokens must be positive")
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"Invalid MiniCheck request: {exc}") from exc
    cache_dir = str(paths.PROJECT_ROOT / cfg.get("cache_dir", "data/cache/models"))
    torch, tokenizer, model = _load(profile.model, revision, cfg.get("device", "cpu"), cache_dir)
    scores, chunks, in_tokens = [], [], 0
    try:
        for doc_index, document in enumerate(documents):
            for chunk, text in _chunks(document, claim, tokenizer, max_tokens):
                inputs = tokenizer(text, return_tensors="pt", truncation=False)
                inputs = {k: v.to(model.device) for k, v in inputs.items()}
                in_tokens += int(inputs["input_ids"].numel())
                decoder = torch.zeros((1, 1), dtype=torch.long, device=model.device)
                with torch.inference_mode():
                    logits = model(**inputs, decoder_input_ids=decoder).logits[0, 0]
                    score = float(torch.softmax(logits[[3, 209]].float(), dim=-1)[1].item())
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("nonfinite or out-of-range classifier score")
                scores.append(score)
                chunks.append({"document_index": doc_index, "text": chunk, "score": score})
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"MiniCheck inference failed: {exc}") from exc
    return Completion(json.dumps({"score": max(scores), "chunks": chunks}), in_tokens, len(scores))


def health(profile):
    missing = [name for name in ("torch", "transformers", "sentencepiece")
               if importlib.util.find_spec(name) is None]
    return Health(not missing, "Missing nli dependencies: " + ", ".join(missing) if missing
                  else "Local NLI dependencies available; checkpoint load not probed")
