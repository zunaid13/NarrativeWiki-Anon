"""[4] SQLite schema + read/write for the temporal knowledge graph.

Inputs:     Entities (gazetteer), claims (CONTRACTS §3), resolved intervals/edges
            (graph/contradictions.py via graph/temporal.py), mention records (CONTRACTS §2.2).
Outputs:    data/04_graph/graph.db (CONTRACTS §4). One connection per call; callers own the
            transaction (each write_* function commits its own batch).
Invariants: - `intervals` is the only table a renderer may read from directly, and only through
            graph/temporal.py's `state_at`/`history_at` (CONTRACTS §4.1) — `claims` is
            provenance/audit only.
            - Every write function is `INSERT OR REPLACE` by primary key, so re-running against
            unchanged input is idempotent. `graph build` still calls `reset()` first every run
            (see cli.py) because intervals/edges are a full recompute from claims, not an
            incremental merge — a stale row from a claim that no longer exists must not survive
            a rerun, and OR REPLACE alone cannot delete a row that is no longer produced.
Contract:   docs/CONTRACTS.md §4.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id     TEXT PRIMARY KEY,
    canonical     TEXT NOT NULL,
    type          TEXT NOT NULL,
    first_vol     INTEGER NOT NULL,
    importance    REAL NOT NULL,
    aliases_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    subject       TEXT NOT NULL,
    predicate     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    object        TEXT,
    value         TEXT,
    qualifier     TEXT,
    first_vol     INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    polarity      TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intervals (
    interval_id    TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    predicate      TEXT NOT NULL,
    object         TEXT,
    value          TEXT,
    qualifier      TEXT,
    vol_start      INTEGER NOT NULL,
    vol_end        INTEGER,
    claim_ids_json TEXT NOT NULL,
    superseded_by  TEXT,
    confidence     REAL NOT NULL DEFAULT 0.0,
    evidence_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS edges (
    subject    TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    object     TEXT NOT NULL,
    vol_start  INTEGER NOT NULL,
    vol_end    INTEGER,
    PRIMARY KEY (subject, predicate, object, vol_start)
);

CREATE TABLE IF NOT EXISTS mention_counts (
    entity_id  TEXT NOT NULL,
    vol        INTEGER NOT NULL,
    count      INTEGER NOT NULL,
    PRIMARY KEY (entity_id, vol)
);

-- Phase 23 B1: `wiki verify`'s per-fact verdict, keyed by the interval it was verified against.
-- Written by graph/verify.py; read by synth/assemble.py (withhold an "unsupported" fact at
-- render time) and audit/reports.py (FAIL when the unsupported rate is too high). `target_kind`
-- is always "interval" today; left open for a future non-interval verification target rather
-- than baked into the primary key as an assumption.
CREATE TABLE IF NOT EXISTS verdicts (
    target_id   TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    rationale   TEXT,
    model       TEXT,
    run_id      TEXT,
    support_score REAL,
    support_threshold REAL,
    context_sha256 TEXT,
    evidence_cutoff INTEGER,
    model_revision TEXT,
    PRIMARY KEY (target_id, target_kind)
);

CREATE INDEX IF NOT EXISTS idx_claims_subject    ON claims(subject);
CREATE INDEX IF NOT EXISTS idx_intervals_subject ON intervals(subject, vol_start);
CREATE INDEX IF NOT EXISTS idx_edges_subject     ON edges(subject);
CREATE INDEX IF NOT EXISTS idx_edges_object      ON edges(object);
"""

# Phase 23 B1: columns added to `intervals` after its original release. `CREATE TABLE IF NOT
# EXISTS` never alters an existing table, so a graph.db written by a pre-Phase-23 run needs an
# explicit migration or these columns simply don't exist on it — `connect()` applies this
# unconditionally (each ALTER is itself guarded by a table_info check, so it is idempotent and
# safe to run against a database that already has the columns, e.g. every run after the first).
_INTERVALS_MIGRATIONS = (
    ("confidence", "REAL NOT NULL DEFAULT 0.0"),
    ("evidence_count", "INTEGER NOT NULL DEFAULT 0"),
)

_VERDICT_MIGRATIONS = (
    ("support_score", "REAL"), ("support_threshold", "REAL"),
    ("context_sha256", "TEXT"), ("evidence_cutoff", "INTEGER"), ("model_revision", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(intervals)")}
    for column, decl in _INTERVALS_MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE intervals ADD COLUMN {column} {decl}")
    existing_verdicts = {row[1] for row in conn.execute("PRAGMA table_info(verdicts)")}
    for column, decl in _VERDICT_MIGRATIONS:
        if column not in existing_verdicts:
            conn.execute(f"ALTER TABLE verdicts ADD COLUMN {column} {decl}")
    conn.commit()


def connect(db_path: Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the graph, applying schema only for writers. Read-only opens never create/migrate."""
    target = db_path or paths.graph_db()
    if read_only:
        conn = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    """Empty every rebuilt table. `graph build` calls this before every write — see module
    docstring.

    `verdicts` is deliberately NOT emptied (2026-09-24). It was, and the cost was measured: a
    rebuild that changed one relation re-ran verification over everything and re-rolled 12
    reader-visible gates, several of them facts whose own evidence had not moved, taking the
    emitted wiki from 34 relation rows to 23 (MEASUREMENTS §28). A verdict now survives the
    rebuild and `wiki verify` decides per character whether it is still valid, by comparing
    `verify.fact_context_sha256`. Call `prune_orphan_verdicts` after `write_intervals` so a verdict
    still cannot outlive the interval it judged."""
    for table in ("entities", "claims", "intervals", "edges", "mention_counts"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def prune_orphan_verdicts(conn: sqlite3.Connection) -> int:
    """Drop verdicts whose interval no longer exists, and report how many went.

    `reset()` no longer clears `verdicts`, so this is what keeps the invariant its docstring used
    to get for free: no verdict outlives the interval_id it was verified against. Runs after
    `write_intervals`, when the rebuilt interval set is known."""
    cur = conn.execute(
        "DELETE FROM verdicts WHERE target_kind = 'interval' "
        "AND target_id NOT IN (SELECT interval_id FROM intervals)"
    )
    conn.commit()
    return cur.rowcount


def write_entities(conn: sqlite3.Connection, entities: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO entities "
        "(entity_id, canonical, type, first_vol, importance, aliases_json) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                e["entity_id"],
                e["canonical"],
                e["type"],
                e["first_vol"],
                e["importance"],
                json.dumps(e.get("aliases", []), ensure_ascii=False),
            )
            for e in entities
        ],
    )
    conn.commit()


def write_claims(conn: sqlite3.Connection, claims: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO claims "
        "(claim_id, subject, predicate, kind, object, value, qualifier, first_vol, confidence, "
        "polarity, evidence_json, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                c["claim_id"],
                c["subject"],
                c["predicate"],
                c["kind"],
                c.get("object"),
                c.get("value"),
                c.get("qualifier"),
                c["first_vol"],
                c["confidence"],
                c["polarity"],
                json.dumps(c["evidence"], ensure_ascii=False),
                c["source"],
            )
            for c in claims
        ],
    )
    conn.commit()


def write_intervals(conn: sqlite3.Connection, intervals: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO intervals "
        "(interval_id, subject, predicate, object, value, qualifier, vol_start, vol_end, "
        "claim_ids_json, superseded_by, confidence, evidence_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                iv["interval_id"],
                iv["subject"],
                iv["predicate"],
                iv.get("object"),
                iv.get("value"),
                iv.get("qualifier"),
                iv["vol_start"],
                iv.get("vol_end"),
                json.dumps(iv["claim_ids"], ensure_ascii=False),
                iv.get("superseded_by"),
                iv.get("confidence", 0.0),
                iv.get("evidence_count", len(iv["claim_ids"])),
            )
            for iv in intervals
        ],
    )
    conn.commit()


def write_edges(conn: sqlite3.Connection, edges: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO edges (subject, predicate, object, vol_start, vol_end) VALUES (?, ?, ?, ?, ?)",
        [(e["subject"], e["predicate"], e["object"], e["vol_start"], e.get("vol_end")) for e in edges],
    )
    conn.commit()


def mention_counts_from_records(mentions: Iterable[dict[str, Any]]) -> dict[tuple[str, int], int]:
    """CONTRACTS §2.2 mention records -> `(entity_id, vol) -> count`, for `write_mention_counts`."""
    counts: dict[tuple[str, int], int] = {}
    for m in mentions:
        key = (m["entity_id"], m["vol"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_mention_counts(conn: sqlite3.Connection, counts: dict[tuple[str, int], int]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO mention_counts (entity_id, vol, count) VALUES (?, ?, ?)",
        [(entity_id, vol, count) for (entity_id, vol), count in counts.items()],
    )
    conn.commit()


def write_verdicts(conn: sqlite3.Connection, verdicts: Iterable[dict[str, Any]]) -> None:
    """Phase 23 B1. Each dict: `{"target_id", "target_kind", "verdict", "rationale", "model",
    "run_id"}`. `target_kind` is always `"interval"` today (see SCHEMA's comment). Written by
    `wiki verify` (graph/verify.py) after `graph build` has already (re)written `intervals` for
    this run. A verdict can never outlive the interval_id it was verified against, but since
    2026-09-24 that is enforced by `prune_orphan_verdicts` rather than by `reset()` wiping the
    table -- see `reset()` for why. `context_sha256` and `evidence_cutoff` are what make a verdict
    reusable on the next rebuild; a row written without them is re-verified every time."""
    conn.executemany(
        "INSERT OR REPLACE INTO verdicts (target_id, target_kind, verdict, rationale, model, run_id, "
        "support_score, support_threshold, context_sha256, evidence_cutoff, model_revision) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                v["target_id"],
                v.get("target_kind", "interval"),
                v["verdict"],
                v.get("rationale"),
                v.get("model"),
                v.get("run_id"),
                v.get("support_score"), v.get("support_threshold"), v.get("context_sha256"),
                v.get("evidence_cutoff"), v.get("model_revision"),
            )
            for v in verdicts
        ],
    )
    conn.commit()


def read_verdicts(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """`interval_id -> {"verdict", "rationale", "model", "run_id"}`, for every `target_kind ==
    "interval"` row. The whole table at once — small (one row per rendered fact per character),
    and every reader (synth/assemble.py's per-page gate, audit/reports.py's rate check) wants
    it as a lookup, not a per-interval query."""
    rows = conn.execute(
        "SELECT * FROM verdicts WHERE target_kind = 'interval'"
    ).fetchall()
    return {
        row["target_id"]: {
            "verdict": row["verdict"], "rationale": row["rationale"],
            "model": row["model"], "run_id": row["run_id"],
            **{key: row[key] for key, _ in _VERDICT_MIGRATIONS if key in row.keys() and row[key] is not None},
        }
        for row in rows
    }
