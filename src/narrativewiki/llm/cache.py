"""Disk cache for model responses, keyed by the exact request.

Inputs:     provider, model, system prompt, user prompt, options.
Outputs:    Cached completion text, or None on a miss.
Invariants: - The key covers everything that can change the answer. A prompt edit is a new key,
              so a tweak to a late-stage prompt costs nothing for stages already done.
            - Deleting data/cache/llm/ is always safe; it only costs a rerun.
            - Since Phase 10 (provenance), each stored entry also carries the exact system
              prompt, user prompt, stage, model, and the timestamp it was FIRST written, not
              just the response -- so a cache hit years later can still be traced back to what
              was asked (see `docs/CONTRACTS.md` §7). `get()` stays backwards-compatible with
              entries written before this: those fields are simply absent/empty on read, never a
              KeyError, so no existing cache entry is invalidated by this change.
Contract:   Internal to llm/. Nothing outside llm/ touches the cache directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths


# [33] Vertex Flex PayGo: same model, same answer, different queue (llm/providers/google.py).
# `flex_on_429` retries a Standard 429 through Flex: same model, same prompt, other queue.
_TRANSPORT_OPTIONS = frozenset({"shared_request_type", "flex_on_429"})


def make_key(
    provider: str, model: str, system: str | None, prompt: str, options: dict[str, Any]
) -> str:
    """Stable hash of everything that determines the response.

    Options are serialised with sorted keys so an unordered dict cannot produce two keys for
    one logical request — the classic silent cache-miss bug. Transport-only options (how a
    request is queued, not what it asks) are left out, so switching them re-bills nothing.
    """
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "system": system or "",
            "prompt": prompt,
            "options": {k: v for k, v in options.items() if k not in _TRANSPORT_OPTIONS},
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CachedResponse:
    text: str
    in_tokens: int
    out_tokens: int
    # Provenance fields (Phase 10). Empty/None on an entry written before this change -- never
    # required, so an old cache is still a valid, readable cache, just without this extra trace.
    stage: str = ""
    provider: str = ""
    model: str = ""
    system: str = ""
    prompt: str = ""
    first_seen_ts: str | None = None


class LLMCache:
    """Sharded JSON file cache. Sharding by the first two hex chars keeps directory listings
    manageable — a full 13-volume run produces tens of thousands of entries."""

    def __init__(self, directory: Path | None = None, enabled: bool = True) -> None:
        self.dir = directory or paths.LLM_CACHE_DIR
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A truncated entry from an interrupted write. Treat as a miss and let it be
            # overwritten rather than crashing a multi-hour run.
            self.misses += 1
            return None
        self.hits += 1
        return CachedResponse(
            text=data["text"],
            in_tokens=int(data.get("in_tokens", 0)),
            out_tokens=int(data.get("out_tokens", 0)),
            stage=data.get("stage", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            system=data.get("system", ""),
            prompt=data.get("prompt", ""),
            first_seen_ts=data.get("first_seen_ts"),
        )

    def put(self, key: str, response: CachedResponse) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique per writer: stages now run their calls in parallel (llm/parallel.py), and a
        # fixed ".tmp" name meant two threads writing the same key interleaved into one file.
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "text": response.text,
                    "in_tokens": response.in_tokens,
                    "out_tokens": response.out_tokens,
                    "stage": response.stage,
                    "provider": response.provider,
                    "model": response.model,
                    "system": response.system,
                    "prompt": response.prompt,
                    "first_seen_ts": response.first_seen_ts
                    or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)  # atomic, so an interrupt cannot leave a half-written entry

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> str:
        return f"cache {self.hits} hit / {self.misses} miss ({self.hit_rate:.0%})"
