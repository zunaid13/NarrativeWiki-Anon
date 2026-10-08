"""[1] RawVolume -> the paragraph records and manifest entry CONTRACTS.md section 1 defines.

Inputs:     `epub.RawVolume` (one parsed EPUB) plus its source filename.
Outputs:    A list of paragraph-record dicts (one JSONL line each) and one manifest-entry dict.
Invariants: `para_id` is `v{vol:02d}:c{chapter_idx:02d}:p{seq:04d}` — stable across reruns because
            it is derived purely from position, never from content, so caches and diffs downstream
            stay meaningful.
Contract:   docs/CONTRACTS.md section 1 and section 1.3.
"""

from __future__ import annotations

from typing import Any

from .epub import RawVolume
from .normalize import count_words


def build_paragraph_records(rv: RawVolume) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for chapter in rv.chapters:
        for seq, para in enumerate(chapter.paragraphs):
            records.append(
                {
                    "para_id": f"v{rv.vol:02d}:c{chapter.chapter_idx:02d}:p{seq:04d}",
                    "vol": rv.vol,
                    "chapter_idx": chapter.chapter_idx,
                    "chapter_id": chapter.chapter_id,
                    "chapter_title": chapter.chapter_title,
                    "chapter_kind": chapter.chapter_kind,
                    "seq": seq,
                    "text": para.text,
                    "print_page": para.print_page,
                    "speech": para.speech,
                    "is_monologue": para.is_monologue,
                    "scene_break_before": para.scene_break_before,
                    "n_words": count_words(para.text),
                }
            )
    return records


def chapter_paragraphs(records: list[dict[str, Any]], chapter_idx: int) -> list[dict[str, Any]]:
    """One chapter's paragraph records, in order. `build_paragraph_records` already emits records
    globally ordered by (chapter_idx, seq), so this is a plain filter, not a sort — centralizes
    what every current caller (`cli.py::_chapter_count` and friends) otherwise inlines ad hoc."""
    return [r for r in records if r["chapter_idx"] == chapter_idx]


def build_manifest_entry(rv: RawVolume, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "vol": rv.vol,
        "title": rv.title,
        "isbn": rv.isbn,
        "pub_date": rv.pub_date,
        "n_chapters": len(rv.chapters),
        "n_paragraphs": len(records),
        "n_words": sum(r["n_words"] for r in records),
        "source_file": rv.source_file,
    }
