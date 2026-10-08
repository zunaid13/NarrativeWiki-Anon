"""Ollama adapter — the local workhorse, where roughly 95% of this pipeline's tokens go.

Inputs:     Profile with provider=ollama, base_url (default http://localhost:11434).
Outputs:    Completion with real prompt_eval_count / eval_count token counts; `embed()` returns
            an Embedding (batch of vectors) via `/api/embed` (Phase 17 — value canonicalization).
Invariants: - Non-streaming. A 14B model on a 6k-token window can take minutes; the client's
              request_timeout_seconds (default 300) covers it.
            - json_mode uses Ollama's grammar-constrained `format: json`, which is far more
              reliable than asking a 14B model to behave.
Contract:   llm/providers/base.py
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import Profile
from .base import Completion, Embedding, Health, ProviderError

# Ollama option keys. Anything else in profile.options (e.g. max_tokens, borrowed from an API
# profile) is dropped rather than sent, since Ollama rejects unknown keys inside `options`.
_OPTION_KEYS = {
    "num_ctx",
    "num_predict",
    "temperature",
    "top_p",
    "top_k",
    "repeat_penalty",
    "seed",
    "stop",
    "num_gpu",
    "num_thread",
    "min_p",
}


def _options(profile: Profile, overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**profile.options, **(overrides or {})}
    return {k: v for k, v in merged.items() if k in _OPTION_KEYS}


def complete(
    profile: Profile,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    timeout: float = 300.0,
    options: dict[str, Any] | None = None,
) -> Completion:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "stream": False,
        "options": _options(profile, options),
    }
    if json_mode:
        payload["format"] = "json"

    url = f"{str(profile.base_url).rstrip('/')}/api/chat"
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"Cannot reach Ollama at {profile.base_url}. Is `ollama serve` running? "
            f"({exc})"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise ProviderError(
            f"Ollama timed out after {timeout:.0f}s on {profile.model}. Either the prompt is "
            f"too long for num_ctx, or the model fell back to CPU — check `nvidia-smi`."
        ) from exc

    if response.status_code == 404:
        raise ProviderError(
            f"Ollama does not have the model {profile.model!r}. "
            f"Run: ollama pull {profile.model}"
        )
    if response.status_code >= 400:
        raise ProviderError(f"Ollama returned {response.status_code}: {response.text[:400]}")

    data = response.json()
    if "error" in data:
        raise ProviderError(f"Ollama error: {data['error']}")

    return Completion(
        text=data.get("message", {}).get("content", ""),
        in_tokens=int(data.get("prompt_eval_count", 0)),
        out_tokens=int(data.get("eval_count", 0)),
    )


def embed(profile: Profile, texts: list[str], timeout: float = 300.0) -> Embedding:
    """Batch embedding via Ollama's `/api/embed` (bge-m3 as of Phase 17). One HTTP call for the
    whole batch — Ollama's `input` field accepts a list, so the local `embed` profile never
    incurs the per-request overhead a large near-duplicate cluster job would otherwise pay."""
    payload = {"model": profile.model, "input": texts}
    url = f"{str(profile.base_url).rstrip('/')}/api/embed"
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"Cannot reach Ollama at {profile.base_url}. Is `ollama serve` running? ({exc})"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise ProviderError(f"Ollama timed out after {timeout:.0f}s embedding with {profile.model}.") from exc

    if response.status_code == 404:
        raise ProviderError(
            f"Ollama does not have the model {profile.model!r}. Run: ollama pull {profile.model}"
        )
    if response.status_code >= 400:
        raise ProviderError(f"Ollama returned {response.status_code}: {response.text[:400]}")

    data = response.json()
    if "error" in data:
        raise ProviderError(f"Ollama error: {data['error']}")

    return Embedding(
        vectors=data.get("embeddings", []),
        in_tokens=int(data.get("prompt_eval_count", 0)),
    )


def health(profile: Profile, timeout: float = 5.0) -> Health:
    """Probe the server and confirm the configured model is actually pulled.

    `wiki doctor` calls this so a missing model is a five-second discovery rather than
    something you learn three hours into an extraction run.
    """
    url = f"{str(profile.base_url).rstrip('/')}/api/tags"
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return Health(False, f"unreachable at {profile.base_url} ({type(exc).__name__})")

    installed = [m.get("name", "") for m in response.json().get("models", [])]
    if profile.model in installed:
        return Health(True, f"{profile.model} ready")

    # Ollama reports "qwen2.5:14b-instruct-q4_K_M"; tolerate a missing :latest suffix.
    base = profile.model.split(":")[0]
    near = [m for m in installed if m.split(":")[0] == base]
    if near:
        return Health(
            False,
            f"{profile.model} not pulled; found {', '.join(near)}. "
            f"Run: ollama pull {profile.model}",
        )
    return Health(False, f"{profile.model} not pulled. Run: ollama pull {profile.model}")
