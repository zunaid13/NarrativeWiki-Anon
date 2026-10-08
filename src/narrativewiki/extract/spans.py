"""[18] One chapter's paragraph records -> overlapping, coverage-complete chapter-major spans.

Inputs:     One chapter's paragraph records, in order (`ingest/segment.py::chapter_paragraphs`),
            plus `extraction.scene` config (`span_target_tokens`, `span_overlap_tokens`,
            `max_tokens_per_request`).
Outputs:    `Span` records: a paragraph range formatted as `[para_id] text` lines (like
            `extract/windows.py::Window`), with an inline `[SCENE BREAK]` marker line wherever a
            hard scene break falls inside it.
Invariants: - `core_para_ids` across every span of one chapter PARTITION the chapter exactly — no
              gap, no duplicate. This is the literal "100% coverage by construction" guarantee
              Phase 18 exists to provide (docs/vision/PHASE_17.md's Phase 18 bullet); every test
              here must protect it.
            - Unlike `windows.py`, a span MAY cross a hard scene break — refusing paragraphs would
              reopen the coverage gap this module exists to close. A hard break inside a span is
              marked inline in `text` instead, so the model can still describe two beats/POVs
              separately rather than blending them.
            - A span never drops a CORE paragraph to fit the token budget — only the leading
              overlap/context is trimmed. A single core paragraph run that alone exceeds
              `max_tokens_per_request` is emitted oversized rather than truncated, the same
              documented posture `windows.py::_shrink_to_budget` already takes for evidence
              windows (see docs/handover/PHASE_3.md).
Contract:   docs/CONTRACTS.md §3b. This module produces no file of its own — its output is
            consumed in-process by `extract/scenes.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..llm.budget import estimate_tokens


@dataclass(frozen=True)
class Span:
    vol: int
    chapter_idx: int
    span_index: int
    para_ids: tuple[str, ...]
    core_para_ids: tuple[str, ...]
    text: str


def _format_span(records: list[dict[str, Any]], lo: int, hi: int) -> str:
    lines: list[str] = []
    for i in range(lo, hi + 1):
        r = records[i]
        if i > lo and r.get("scene_break_before") == "hard":
            lines.append("[SCENE BREAK]")
        lines.append(f'[{r["para_id"]}] {r["text"]}')
    return "\n".join(lines)


def _core_groups(records: list[dict[str, Any]], span_target_tokens: int) -> list[tuple[int, int]]:
    """Greedily partition the chapter into (lo, hi) index ranges, each kept under
    `span_target_tokens` where possible. Always includes at least one paragraph per group — a
    paragraph that alone exceeds the target still gets its own group rather than being split."""
    n = len(records)
    groups: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = start
        text_accum = records[start]["text"]
        while end + 1 < n:
            candidate_text = text_accum + "\n" + records[end + 1]["text"]
            if estimate_tokens(candidate_text) > span_target_tokens:
                break
            end += 1
            text_accum = candidate_text
        groups.append((start, end))
        start = end + 1
    return groups


def build_chapter_spans(
    vol: int,
    chapter_idx: int,
    chapter_records: list[dict[str, Any]],
    span_cfg: dict[str, Any],
) -> list[Span]:
    """One chapter's paragraph records -> overlapping spans. `chapter_records` must already be in
    reading order (`ingest/segment.py::chapter_paragraphs` guarantees this)."""
    if not chapter_records:
        return []

    span_target = int(span_cfg.get("span_target_tokens", 4000))
    overlap_target = int(span_cfg.get("span_overlap_tokens", 500))
    max_tokens = int(span_cfg.get("max_tokens_per_request", 6000))

    spans: list[Span] = []
    for span_index, (core_lo, core_hi) in enumerate(_core_groups(chapter_records, span_target)):
        overlap_lo = core_lo
        if span_index > 0:
            accum = 0
            i = core_lo - 1
            while i >= 0:
                t = estimate_tokens(chapter_records[i]["text"])
                if accum + t > overlap_target:
                    break
                accum += t
                overlap_lo = i
                i -= 1

        lo, hi = overlap_lo, core_hi
        text = _format_span(chapter_records, lo, hi)
        while lo < core_lo and estimate_tokens(text) > max_tokens:
            lo += 1
            text = _format_span(chapter_records, lo, hi)

        spans.append(
            Span(
                vol=vol,
                chapter_idx=chapter_idx,
                span_index=span_index,
                para_ids=tuple(r["para_id"] for r in chapter_records[lo : hi + 1]),
                core_para_ids=tuple(r["para_id"] for r in chapter_records[core_lo : core_hi + 1]),
                text=text,
            )
        )

    return spans
