"""[19] SQLite schema + read/write for the event layer.

Inputs:     Scene records (CONTRACTS §3b) — consumed by graph/event_build.py, which calls the
            write helpers here. Readers (`events_at`, `character_events_at`,
            `event_claims_at`) are called by synth/prose.py::generate_chronology and by the
            audit report; `shared_events_at` (Phase 21 part 1) is called by
            `site/bundle.py::build_relationship_bundles` for pair-scoped relationship pages;
            `group_events_at` (Phase 21 part 2) is called by `site/bundle.py::build_codex_bundles`
            for a faction's "key events" — the same co-presence idea generalized from a pair to
            an arbitrary member set. `events_in_volume` (Phase 21 part 3) is called by
            `site/bundle.py::build_timeline_bundles` for one volume's chapter-by-chapter recap.
Outputs:    data/04b_events/events.db (CONTRACTS §4b). Two tables:
            - `events`: one row per scene span that had at least one participant, state_change,
              or quote — empty spans are proof the model looked but found nothing narrative; they
              do not become event nodes.
            - `event_claims`: state_change and quote records anchored to an event (a `kind:
              "quote"` claim's `object` column holds the quote's addressee entity_id/surface
              form when one was resolved — see `graph/event_build.py`).
Invariants: - `events_at`/`character_events_at`/`event_claims_at`/`shared_events_at` ALL filter
              `vol_start <= vol` before returning, matching the spoiler-fence discipline of
              graph/temporal.py's `state_at`/`history_at`/`relations_at`. Nothing outside this
              module may query these tables directly.
            - `beat_summary` is the one un-gated field (same exception as §3b -- it is a
              genuine paraphrase, not a verbatim quote). Everything else (participants, location,
              event_claim quotes) comes verbatim from scene records that have already passed
              `wiki audit scenes`'s own verbatim-quote check.
            - `core_para_ids_json` carries forward the scene record's own `core_para_ids` (§3b) --
              the paragraphs `beat_summary` was actually paraphrased from. `event_claims.para_id`
              only exists for spans with a state_change/quote; a span with a beat_summary but no
              structured facts (e.g. Norah Arendt's chronology, Phase 21 part 4's evaluation-
              harness finding) previously left `synth/prose.py::generate_chronology`'s `evidence`
              list empty despite the prose being well-grounded. `character_events_at` callers
              should fold this into their own evidence list rather than relying on
              `event_claims` alone.
            - `events.db` is separate from `graph.db` so `wiki graph build`'s reset() and
              rollback semantics are completely undisturbed. `wiki events build --force` resets
              `events.db` only.
            - Write helpers are `INSERT OR REPLACE` by primary key (idempotent). `reset()` is
              called first by `event_build.build_events()` on a force run, same pattern as
              graph/store.py.
Contract:   docs/CONTRACTS.md §4b.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT PRIMARY KEY,
    vol               INTEGER NOT NULL,
    chapter_idx       INTEGER NOT NULL,
    span_index        INTEGER NOT NULL,
    scene_id          TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    location         TEXT,
    beat_summary     TEXT NOT NULL,
    core_para_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_para_ids_json TEXT NOT NULL DEFAULT '[]',
    vol_start        INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    source           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_claims (
    claim_id   TEXT PRIMARY KEY,
    event_id   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    subject    TEXT,
    predicate  TEXT,
    from_value TEXT,
    to_value   TEXT,
    object     TEXT,
    note       TEXT,
    speaker    TEXT,
    para_id    TEXT NOT NULL,
    quote      TEXT NOT NULL,
    vol        INTEGER NOT NULL,
    confidence REAL NOT NULL,
    source     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_vol_chapter   ON events(vol, chapter_idx);
CREATE INDEX IF NOT EXISTS idx_events_vol_start     ON events(vol_start);
CREATE INDEX IF NOT EXISTS idx_event_claims_event   ON event_claims(event_id);
CREATE INDEX IF NOT EXISTS idx_event_claims_subject ON event_claims(subject);
CREATE INDEX IF NOT EXISTS idx_event_claims_vol     ON event_claims(vol);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the events database with the schema applied."""
    target = db_path or paths.events_db()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # [34] events.db files written before `summary_para_ids_json` existed
    if "summary_para_ids_json" not in {r[1] for r in conn.execute("PRAGMA table_info(events)")}:
        conn.execute("ALTER TABLE events ADD COLUMN summary_para_ids_json TEXT NOT NULL DEFAULT '[]'")
    return conn


def reset(conn: sqlite3.Connection) -> None:
    """Empty every table. `event_build.build_events()` calls this before a force rebuild."""
    for table in ("events", "event_claims"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def write_events(conn: sqlite3.Connection, events: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO events "
        "(event_id, vol, chapter_idx, span_index, scene_id, participants_json, location, "
        "beat_summary, core_para_ids_json, summary_para_ids_json, vol_start, confidence, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                e["event_id"],
                e["vol"],
                e["chapter_idx"],
                e["span_index"],
                e["scene_id"],
                json.dumps(e.get("participants", []), ensure_ascii=False),
                e.get("location"),
                e["beat_summary"],
                json.dumps(e.get("core_para_ids", []), ensure_ascii=False),
                json.dumps(e.get("summary_para_ids", []), ensure_ascii=False),
                e["vol_start"],
                e["confidence"],
                e["source"],
            )
            for e in events
        ],
    )
    conn.commit()


def write_event_claims(conn: sqlite3.Connection, claims: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO event_claims "
        "(claim_id, event_id, kind, subject, predicate, from_value, to_value, object, note, "
        "speaker, para_id, quote, vol, confidence, source) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                c["claim_id"],
                c["event_id"],
                c["kind"],
                c.get("subject"),
                c.get("predicate"),
                c.get("from_value"),
                c.get("to_value"),
                c.get("object"),
                c.get("note"),
                c.get("speaker"),
                c["para_id"],
                c["quote"],
                c["vol"],
                c["confidence"],
                c["source"],
            )
            for c in claims
        ],
    )
    conn.commit()


def events_at(conn: sqlite3.Connection, vol: int) -> list[sqlite3.Row]:
    """All event rows visible at cutoff `vol` -- `vol_start <= vol`, in reading order."""
    return conn.execute(
        "SELECT * FROM events WHERE vol_start <= ? ORDER BY vol, chapter_idx, span_index",
        (vol,),
    ).fetchall()


def character_events_at(
    conn: sqlite3.Connection, entity_id: str, vol: int
) -> list[sqlite3.Row]:
    """Event rows where `entity_id` appears in `participants_json`, visible at cutoff `vol`.

    Used by `synth/prose.py::generate_chronology` to build the character's narrative arc.
    Searches for '"<entity_id>"' (with JSON quotes) to avoid false matches where one id is
    a prefix of another. Returns rows in reading order (vol, chapter_idx, span_index)."""
    target = f'"{entity_id}"'
    return conn.execute(
        "SELECT * FROM events WHERE vol_start <= ? AND participants_json LIKE ? "
        "ORDER BY vol, chapter_idx, span_index",
        (vol, f"%{target}%"),
    ).fetchall()


def shared_events_at(conn: sqlite3.Connection, a: str, b: str, vol: int) -> list[sqlite3.Row]:
    """Event rows where BOTH `a` and `b` appear in `participants_json`, visible at cutoff `vol` —
    the "shared scenes" for Phase 21 relationship pages. Same LIKE-on-quoted-id technique and
    cutoff fence as `character_events_at`, just with a second LIKE clause instead of a second
    call, so a pair page inherits that function's exact spoiler discipline rather than a
    freshly-written one."""
    return conn.execute(
        "SELECT * FROM events WHERE vol_start <= ? AND participants_json LIKE ? "
        "AND participants_json LIKE ? ORDER BY vol, chapter_idx, span_index",
        (vol, f'%"{a}"%', f'%"{b}"%'),
    ).fetchall()


def events_in_volume(conn: sqlite3.Connection, vol: int) -> list[sqlite3.Row]:
    """Event rows whose OWN `vol` equals `vol` -- one volume's worth of narrative beats, in
    reading order (Phase 21 part 3, per-volume timeline pages). Distinct from `events_at`, which
    returns every event with `vol_start <= vol` (cumulative through that cutoff); `vol_start`
    always equals `vol` for every event (CONTRACTS §4b.1 -- an event is anchored to its own
    volume, never forward-dated), so a row selected here can never fail the `vol_start <= vol`
    fence either: `vol == V` trivially implies `vol_start == V <= V`. Safe to call for any `vol`
    up to the reader's own cutoff without a separate spoiler check."""
    return conn.execute(
        "SELECT * FROM events WHERE vol = ? ORDER BY chapter_idx, span_index", (vol,)
    ).fetchall()


def group_events_at(
    conn: sqlite3.Connection, entity_ids: list[str], vol: int, *, min_participants: int = 2
) -> list[sqlite3.Row]:
    """Event rows where at least `min_participants` of `entity_ids` co-appear in
    `participants_json`, visible at cutoff `vol` — the faction-page analogue of `shared_events_at`
    (Phase 21 part 2), generalized from a fixed pair to an arbitrary member set (a faction's
    current roster). Filters through `events_at` (already `vol_start <= vol`-fenced) rather than a
    fresh query, so this inherits that function's exact spoiler discipline. The caller
    cross-references `participants_json` against `entity_ids` itself to learn WHICH members were
    present; this function only decides inclusion. Returns `[]` without querying when
    `entity_ids` is too small to ever satisfy `min_participants` — the same short-circuit
    `_shared_scenes_between` gets for free from needing exactly two ids."""
    if len(entity_ids) < min_participants:
        return []
    wanted = set(entity_ids)
    out: list[sqlite3.Row] = []
    for row in events_at(conn, vol):
        present = [p for p in json.loads(row["participants_json"]) if p in wanted]
        if len(present) >= min_participants:
            out.append(row)
    return out


def located_events_at(conn: sqlite3.Connection, entity_id: str, vol: int) -> list[sqlite3.Row]:
    """[30] Event rows whose `location` IS this entity, visible at cutoff `vol`.

    The scene-level counterpart of `group_events_at`: where that asks "which of these people were
    together", this asks "what happened here". 19 of the 53 v1-2 events carry a resolved location
    and nothing read the column, so every one of the 13 place entries in the codex shipped with no
    events at all. Filters through `events_at`, so it inherits that function's cutoff fence."""
    return [row for row in events_at(conn, vol) if row["location"] == entity_id]


def event_claims_at(
    conn: sqlite3.Connection, event_id: str, vol: int
) -> list[sqlite3.Row]:
    """Event claim rows for one event, visible at cutoff `vol`."""
    return conn.execute(
        "SELECT * FROM event_claims WHERE event_id = ? AND vol <= ?",
        (event_id, vol),
    ).fetchall()


def make_event_id(vol: int, chapter_idx: int, span_index: int) -> str:
    """'ev_v01c03s002' -- parallel to extract/scene_schema.py::make_scene_id."""
    return f"ev_v{vol:02d}c{chapter_idx:02d}s{span_index:03d}"