"""[6] Claim-set-hash cache: collapses the 13-cutoffs x N-characters multiplier (CONTRACTS §5.1).

Inputs:     Nothing but `data/05_pages/<entity_id>/v{NN}.json`, already written by an earlier
            `wiki synthesize` call in the same or an earlier run.
Outputs:    `previous_page(entity_id, upto_vol)` -> the most recently written page file for this
            entity at any cutoff strictly below `upto_vol`, or `None` if none exists yet.
            `unchanged(prev, claim_set_hash)` -> True when that file's `claim_set_hash` already
            matches this cutoff's, i.e. nothing about the character changed between the two
            cutoffs and this cutoff needs neither a fresh LLM call nor a fresh file.
Invariants: - Reads only files `wiki synthesize` itself wrote; never touches graph.db. The
            expensive-to-avoid work is BOTH the LLM call (which `llm/cache.py` would technically
            also dedupe, since an unchanged claim set produces a byte-identical prompt) AND the
            page-file write CONTRACTS §5.1 says to skip outright ("the SPA bundle points cutoff N
            at the N-1 file") — this module is what makes the second half of that true, since
            `llm/cache.py` has no opinion on whether a file should exist.
            - Cutoffs are always processed in ascending order within one `wiki synthesize`
            invocation (cli.py), so a fresh run only ever needs to look at the immediately
            preceding cutoff it just wrote in-memory — `previous_page` on disk is what makes a
            RESUMED run (e.g. `--upto 8` after an earlier `--upto 5`) correct too, by finding
            cutoff 5's file instead of assuming cutoff 7 exists.
Contract:   docs/CONTRACTS.md §5.1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import paths


def previous_page_path(entity_id: str, upto_vol: int) -> Path | None:
    """Locate the nearest real page below the cutoff, including across skipped cutoffs."""
    for vol in range(upto_vol - 1, 0, -1):
        candidate = paths.page_json(entity_id, vol)
        if candidate.is_file():
            return candidate
    return None


def previous_page(entity_id: str, upto_vol: int) -> dict[str, Any] | None:
    """Read the nearest existing page at a cutoff strictly below `upto_vol`."""
    candidate = previous_page_path(entity_id, upto_vol)
    return json.loads(candidate.read_text(encoding="utf-8")) if candidate else None


def unchanged(prev: dict[str, Any] | None, claim_set_hash: str) -> bool:
    """True when `prev` (from `previous_page`) already covers this exact claim set — the cutoff
    can be skipped: no LLM call, no new file."""
    return prev is not None and prev.get("claim_set_hash") == claim_set_hash
