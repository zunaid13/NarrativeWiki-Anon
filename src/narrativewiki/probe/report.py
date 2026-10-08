"""[24] JSONL output for every probe measurement, plus the `wiki audit probe` report.

Inputs:     Any measurement dict returned by a `probe/channels/*.py::measure_*` function --
            each must carry at least `channel`, `probe`, `series`, `upto_vol`.
Outputs:    `write_jsonl(rows, path)` writes one JSON object per line; `run_index_channel(...)`
            and `run_leak_channel(...)` return measurement rows across a cutoff range.
            Leak rows include claim and page witnesses (CONTRACTS §9).
Invariants: This module never computes a measurement itself -- Part 0.3 of the plan is "no paper
            can be written from HTML"; every number a probe reports must trace to a row here.
Contract:   docs/vision/PHASE_24.md; CLAUDE.md §0.3 ("machine-readable metrics").
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .. import paths
from .channels import index as index_channel
from .channels import leak as leak_channel
from .channels import disclosure as disclosure_channel


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def probe_report_path(run_id: str) -> Path:
    """data/<series>/_runs/<run_id>/probe/index.jsonl -- alongside audit/*.json (Part 0.3)."""
    return paths.run_dir(run_id) / "probe" / "index.jsonl"


def run_index_channel(series_id: str, series_config: dict[str, Any], cutoffs: list[int]) -> list[dict[str, Any]]:
    """Both `channels/index.py` probes, across every cutoff in `cutoffs`. Skips a cutoff (with a
    row noting why) rather than raising, so one missing volume does not kill the whole sweep."""
    rows: list[dict[str, Any]] = []
    for t in cutoffs:
        try:
            rows.append(index_channel.measure_candidate_mining(series_id, series_config, t))
        except (OSError, ValueError) as exc:
            rows.append({"channel": "L_build", "probe": "candidate_mining", "series": series_id, "upto_vol": t, "error": str(exc)})
        try:
            rows.append(index_channel.measure_vocabulary_exposure(series_id, t))
        except (OSError, ValueError) as exc:
            rows.append({"channel": "L_build", "probe": "vocabulary_exposure", "series": series_id, "upto_vol": t, "error": str(exc)})
    return rows


def run_leak_channel(series_id: str, cutoffs: list[int]) -> list[dict[str, Any]]:
    """Measure future-fact artifact leakage per cutoff, recording missing-input error rows."""
    rows: list[dict[str, Any]] = []
    for t in cutoffs:
        try:
            rows.append(leak_channel.measure_future_fact_leak(series_id, t))
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            rows.append({"channel": "L_query", "probe": "future_fact_leak", "series": series_id,
                         "upto_vol": t, "error": str(exc)})
    return rows


def run_disclosure_channel(series_id: str, cutoffs: list[int]) -> list[dict[str, Any]]:
    """Measure surface occurrence, preserving failed cutoffs in the canonical run ledger."""
    rows: list[dict[str, Any]] = []
    for t in cutoffs:
        try:
            rows.append(disclosure_channel.measure_surface_disclosure(series_id, t))
        except (OSError, ValueError) as exc:
            rows.append({"channel": "L_build", "probe": "first_occurrence", "series": series_id,
                         "upto_vol": t, "error": str(exc)})
    return rows
