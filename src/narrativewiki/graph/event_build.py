"""[19] Build the event layer from Phase 18 scene records.

Inputs:     `data/02b_scenes/v{NN}.jsonl` scene records (CONTRACTS §3b) — one or more files.
            No LLM call; pure deterministic transform.
Outputs:    `data/04b_events/events.db` (CONTRACTS §4b), populated by the write helpers in
            `graph/events.py`. Returns (n_events, n_event_claims) for the CLI to print.
Invariants: - Only scene records with at least one participant OR at least one state_change OR
              at least one quote become event rows. An empty span (no participants, no structured
              facts) is already recorded in data/02b_scenes/ as proof the model looked and found
              nothing -- it does not become a narrative event node.
            - Event claim ids are deterministic: sha1 of (event_id, kind, para_id, quote[:40])
              -- stable across reruns, never a UUID. Same philosophy as claims.py's claim_id.
            - A force=True rebuild calls reset() first so stale rows from a previous run that
              are no longer produced (e.g. after re-running `wiki scenes`) do not survive. A
              non-force run is INSERT OR REPLACE, idempotent for unchanged input.
            - Missing or empty scene files are silently skipped (not every volume in a
              --volumes range may have been scene-extracted yet). The caller's CLI prints a
              warning if an expected file is absent.
Contract:   docs/CONTRACTS.md §4b.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .events import (
    make_event_id,
    reset,
    write_event_claims,
    write_events,
)


def _make_claim_id(event_id: str, kind: str, para_id: str, quote: str) -> str:
    digest = hashlib.sha1(
        f"{event_id}|{kind}|{para_id}|{quote[:40]}".encode("utf-8")
    ).hexdigest()
    return f"ec_{digest[:8]}"


def _scene_to_event(scene: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one scene record to an event dict, or return None if there is nothing to anchor.

    A scene with no participants AND no state_changes AND no quotes has no narrative content
    to anchor -- it is proof the model looked and found nothing, which is already recorded in
    data/02b_scenes/. We do not turn those into event nodes."""
    has_content = (
        bool(scene.get("participants"))
        or bool(scene.get("state_changes"))
        or bool(scene.get("quotes"))
    )
    if not has_content:
        return None

    return {
        "event_id": make_event_id(scene["vol"], scene["chapter_idx"], scene["span_index"]),
        "vol": scene["vol"],
        "chapter_idx": scene["chapter_idx"],
        "span_index": scene["span_index"],
        "scene_id": scene["scene_id"],
        "participants": scene.get("participants", []),
        "location": scene.get("location"),
        "beat_summary": scene.get("beat_summary", ""),
        "core_para_ids": scene.get("core_para_ids", []),
        "summary_para_ids": scene.get("summary_para_ids", []),
        "vol_start": scene["vol"],  # event is anchored to its own volume
        "confidence": scene.get("confidence", 0.7),
        "source": scene.get("source", ""),
    }


def _scene_to_event_claims(
    event_id: str, scene: dict[str, Any]
) -> list[dict[str, Any]]:
    """Convert a scene record's state_changes and quotes to event_claims rows."""
    vol = scene["vol"]
    confidence = scene.get("confidence", 0.7)
    source = scene.get("source", "")
    claims: list[dict[str, Any]] = []

    for sc in scene.get("state_changes", []):
        ev = sc.get("evidence", [{}])[0]
        para_id = ev.get("para_id", "")
        quote = ev.get("quote", "")
        if not para_id or not quote:
            continue
        claim_id = _make_claim_id(event_id, "state_change", para_id, quote)
        claims.append({
            "claim_id": claim_id,
            "event_id": event_id,
            "kind": "state_change",
            "subject": sc.get("subject"),
            "predicate": sc.get("predicate"),
            "from_value": sc.get("from_value"),
            "to_value": sc.get("to_value"),
            "object": sc.get("object"),
            "note": sc.get("note", ""),
            "speaker": None,
            "para_id": para_id,
            "quote": quote,
            "vol": vol,
            "confidence": confidence,
            "source": source,
        })

    for q in scene.get("quotes", []):
        para_id = q.get("para_id", "")
        quote = q.get("quote", "")
        if not para_id or not quote:
            continue
        claim_id = _make_claim_id(event_id, "quote", para_id, quote)
        claims.append({
            "claim_id": claim_id,
            "event_id": event_id,
            "kind": "quote",
            "subject": None,
            "predicate": None,
            "from_value": None,
            "to_value": None,
            "object": q.get("addressee"),
            "note": None,
            "speaker": q.get("speaker"),
            "para_id": para_id,
            "quote": quote,
            "vol": vol,
            "confidence": confidence,
            "source": source,
        })

    return claims


def build_events(
    scene_files: list[Path],
    conn: sqlite3.Connection,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """Read scene files and populate events.db. Returns (n_events, n_event_claims).

    `force=True` calls reset() first so stale rows from a previous build do not survive.
    `force=False` is INSERT OR REPLACE -- idempotent when input is unchanged."""
    if force:
        reset(conn)

    all_events: list[dict[str, Any]] = []
    all_claims: list[dict[str, Any]] = []

    for path in sorted(scene_files):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            scene = json.loads(line)
            event = _scene_to_event(scene)
            if event is None:
                continue
            claims = _scene_to_event_claims(event["event_id"], scene)
            all_events.append(event)
            all_claims.extend(claims)

    write_events(conn, all_events)
    write_event_claims(conn, all_claims)
    return len(all_events), len(all_claims)