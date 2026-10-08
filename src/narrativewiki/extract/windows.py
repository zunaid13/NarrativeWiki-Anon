"""[3] Mention offsets -> deduped evidence windows under a token budget.

Inputs:     One entity's mention `para_id`s in one volume (from `data/02_entities/mentions.jsonl`),
            and the full ordered paragraph record list for that volume (CONTRACTS §1 — reading
            order, one record per line of `data/01_parsed/v{NN}.jsonl`).
Outputs:    `Window` records: a contiguous paragraph span, formatted as `[para_id] text` lines so
            the LLM (and `extract/claims.py`) can cite exactly which paragraph a quote came from.
Invariants: - A window never spans an `img.ornament` hard scene break (CONTRACTS §1.2) — extending
              context across a POV change would hand the model an evidence passage about someone
              else's scene. This is `extraction.window.never_cross_hard_break`.
            - Mention clusters close enough that their context would overlap are merged into one
              window (`extraction.window.merge_overlapping`), so a paragraph is never sent twice.
            - Each window stays under `extraction.window.max_tokens_per_request` (estimated via
              `llm.budget.estimate_tokens`), trimming outward context first and never touching the
              paragraphs that actually contain a mention.
Contract:   docs/CONTRACTS.md §1 (paragraph shape) and §2.2 (mention shape). This module produces
            no file of its own — its output is consumed in-process by `extract/claims.py`.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..llm.budget import estimate_tokens


@dataclass(frozen=True)
class Window:
    entity_id: str
    vol: int
    para_ids: tuple[str, ...]
    text: str
    mention_count: int


def _hard_break_before(records: list[dict[str, Any]], idx: int) -> bool:
    return records[idx].get("scene_break_before") == "hard"


def _hard_break_between(records: list[dict[str, Any]], a: int, b: int, enforce: bool) -> bool:
    """True when expanding/merging across positions a..b would cross a hard break, i.e. any
    paragraph strictly after `a` up to and including `b` has one immediately before it."""
    if not enforce:
        return False
    return any(_hard_break_before(records, i) for i in range(a + 1, b + 1))


def _expand(records: list[dict[str, Any]], pos: int, delta: int, enforce: bool) -> int:
    """Walk `pos` outward by `abs(delta)` paragraphs (negative = backward), stopping at the
    volume boundary or at a hard break, whichever comes first."""
    step = -1 if delta < 0 else 1
    cur = pos
    for _ in range(abs(delta)):
        nxt = cur + step
        if nxt < 0 or nxt >= len(records):
            break
        boundary_idx = cur if step < 0 else nxt
        if enforce and _hard_break_before(records, boundary_idx):
            break
        cur = nxt
    return cur


def _format_window(records: list[dict[str, Any]], lo: int, hi: int) -> str:
    return "\n".join(f'[{r["para_id"]}] {r["text"]}' for r in records[lo : hi + 1])


def _shrink_to_budget(
    records: list[dict[str, Any]], lo: int, hi: int, core_lo: int, core_hi: int, max_tokens: int
) -> tuple[int, int]:
    """Trim outward context (never the core mention paragraphs) until the window fits the token
    budget, or there is no more context left to trim. A core that alone exceeds budget is left
    as-is — see docs/handover/PHASE_3.md for why this is a documented gap, not a silent one."""
    while (lo < core_lo or hi > core_hi) and estimate_tokens(_format_window(records, lo, hi)) > max_tokens:
        if hi > core_hi:
            hi -= 1
        else:
            lo += 1
    return lo, hi


def allocate_window_caps(
    mention_counts: dict[str, int], base_cap: int, floor: int = 3
) -> dict[str, int]:
    """Turn the old flat `max_windows_per_entity_per_volume` cap into a shared per-volume pool
    split by sqrt(mention_count) (Phase 22 B3). The flat cap only ever bound the handful of
    characters dense enough to exceed it and did nothing for anyone else -- `windows.py:133-136`
    before this change. `base_cap` sizes the pool as `base_cap * len(mention_counts)`, i.e. the
    same total order of magnitude as if every entity had hit the old flat cap, just reallocated
    toward the entities with real evidence density. `floor` keeps a low-mention entity from being
    squeezed below what a flat share would have given it just because a couple of protagonists
    dominate the sqrt sum -- it is a cap, not a promise of that many windows, since `build_windows`
    still only produces as many as the entity actually has clusters for."""
    entities = {e: n for e, n in mention_counts.items() if n > 0}
    if not entities:
        return {}
    total_pool = base_cap * len(entities)
    weights = {e: math.sqrt(n) for e, n in entities.items()}
    weight_sum = sum(weights.values())
    return {e: max(floor, round(total_pool * w / weight_sum)) for e, w in weights.items()}


def build_windows(
    entity_id: str,
    mention_para_ids: list[str],
    records: list[dict[str, Any]],
    window_cfg: dict[str, Any],
    max_windows: int | None = None,
    drops: Counter[str] | None = None,
) -> list[Window]:
    """One entity's mentions in one volume -> deduped, token-bounded evidence windows.

    `records` is the full ordered paragraph list for the volume (CONTRACTS §1); `mention_para_ids`
    is this entity's mention `para_id`s in that volume, in any order and with duplicates allowed.
    `max_windows`, when given, overrides `window_cfg`'s flat
    `max_windows_per_entity_per_volume` -- the per-entity share from `allocate_window_caps`
    (Phase 22 B3). `drops`, when given, is incremented with `window_cap_truncated` for each window
    the cap drops, so the caller can see and report a truncation that used to be invisible.
    """
    if not mention_para_ids or not records:
        return []

    pos_by_id = {r["para_id"]: i for i, r in enumerate(records)}
    positions = sorted({pos_by_id[pid] for pid in mention_para_ids if pid in pos_by_id})
    if not positions:
        return []

    before = int(window_cfg.get("context_paragraphs_before", 2))
    after = int(window_cfg.get("context_paragraphs_after", 2))
    enforce = bool(window_cfg.get("never_cross_hard_break", True))
    max_tokens = int(window_cfg.get("max_tokens_per_request", 6000))
    if max_windows is None:
        max_windows = int(window_cfg.get("max_windows_per_entity_per_volume", 40))

    # Cluster mention positions whose expanded context would touch or overlap.
    gap = before + after + 1
    clusters: list[list[int]] = []
    for p in positions:
        if clusters and p - clusters[-1][-1] <= gap and not _hard_break_between(records, clusters[-1][-1], p, enforce):
            clusters[-1].append(p)
        else:
            clusters.append([p])

    # [34] C35: a cluster whose core alone exceeds the budget was kept as ONE window (the gap the
    # Phase 3 handover documents): Anne's merged entity had windows up to 77,699 tokens, a chapter
    # per extraction call, and claims for the merged protagonists fell 23-30%. Such a cluster is now
    # tiled into consecutive sub-clusters whose windows fit; every mention paragraph stays in one.
    line_tokens = {}

    def span_tokens(lo: int, hi: int) -> int:
        for i in range(lo, hi + 1):
            if i not in line_tokens:
                line_tokens[i] = estimate_tokens(_format_window(records, i, i))
        return sum(line_tokens[i] for i in range(lo, hi + 1))

    tiled: list[list[int]] = []
    for cluster in clusters:
        current = [cluster[0]]
        for p in cluster[1:]:
            if span_tokens(current[0], p) <= max_tokens:   # the core fits; `_shrink_to_budget` trims context
                current.append(p)
            else:
                tiled.append(current)
                current = [p]
        tiled.append(current)
    clusters = tiled

    windows: list[Window] = []
    for cluster in clusters:
        core_lo, core_hi = cluster[0], cluster[-1]
        lo = _expand(records, core_lo, -before, enforce)
        hi = _expand(records, core_hi, after, enforce)
        lo, hi = _shrink_to_budget(records, lo, hi, core_lo, core_hi, max_tokens)
        windows.append(
            Window(
                entity_id=entity_id,
                vol=records[lo]["vol"],
                para_ids=tuple(r["para_id"] for r in records[lo : hi + 1]),
                text=_format_window(records, lo, hi),
                mention_count=len(cluster),
            )
        )

    if len(windows) > max_windows:
        if drops is not None:
            drops["window_cap_truncated"] += len(windows) - max_windows
        windows = sorted(windows, key=lambda w: -w.mention_count)[:max_windows]
        windows.sort(key=lambda w: w.para_ids[0])

    return windows


PASSAGE_BREAK = "\n\n--- new passage, not continuous with the text above ---\n\n"


def pack_windows(
    windows: list[Window],
    budget_tokens: int,
    overhead_tokens: int = 0,
    max_per_request: int = 1,
) -> list[Window]:
    """Group already-built windows into as few requests as the budget allows.

    Phase 26 part C. `claim_extract` was one call per (character, window) -- 361 calls for two
    volumes, median 3.6k in / 216 out, of which ~2.2k of every input was the same system prompt
    re-sent. OpenRouter's free tier is capped on CALLS (1000/day), not tokens, so the cheapest
    possible optimisation is to stop leaving most of each request empty.

    A packed window is several disjoint passages in one request, joined by `PASSAGE_BREAK` and
    still labeled `[para_id]` per paragraph, so every downstream check (`normalize_para_id`,
    the verbatim-quote test, `passes_evidence_adequacy`) works unchanged -- each fact still cites
    one real paragraph the model was actually shown.

    `max_per_request` bounds the batch independently of `budget_tokens` because the real limit is
    usually the OUTPUT cap, not the input: facts scale with the number of passages, and a batch
    that overruns `max_tokens` truncates its JSON and costs a repair retry -- which would spend
    the very calls this is saving.

    A single window larger than the budget is passed through untouched; `claims.py`'s own
    context-overflow splitter is what handles that case, and only when the profile declares a
    context size to split against.
    """
    if budget_tokens <= 0 or max_per_request <= 1:
        return list(windows)

    packed: list[Window] = []
    batch: list[Window] = []
    used = overhead_tokens

    def flush() -> None:
        if not batch:
            return
        if len(batch) == 1:
            packed.append(batch[0])
        else:
            packed.append(
                Window(
                    entity_id=batch[0].entity_id,
                    vol=batch[0].vol,
                    para_ids=tuple(pid for w in batch for pid in w.para_ids),
                    text=PASSAGE_BREAK.join(w.text for w in batch),
                    mention_count=sum(w.mention_count for w in batch),
                )
            )
        batch.clear()

    for window in windows:
        tokens = estimate_tokens(window.text)
        if batch and (used + tokens > budget_tokens or len(batch) >= max_per_request):
            flush()
            used = overhead_tokens
        batch.append(window)
        used += tokens
    flush()
    return packed
