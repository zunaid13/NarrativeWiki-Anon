"""[24] F>t — the future-fact set a Leak@t-style probe scores against. No annotation, no LLM call.

Inputs:     `graph.db`'s `claims`/`intervals` tables via `graph/store.py::connect`.
Outputs:    `future_claims_at(conn, upto_vol)` / `future_intervals_at(conn, upto_vol)` -- every
            claim/interval with `first_vol > upto_vol`, i.e. everything a page rendered at
            `upto_vol` must not reveal.
Invariants: This is deliberately the complement of what `graph/temporal.py` selects, not a new
            query family -- `docs/vision/PHASE_24.md`'s point that N future-fact sets fall out of
            one annotation pass, because the same `claims` table is re-scoped per cutoff rather
            than re-collected.
Contract:   docs/vision/PHASE_24.md; docs/archive/GUIDE_TO_PUBLISHING.md §2.2 ("Leak@t").
"""

from __future__ import annotations

import sqlite3
from typing import Any


def future_claims_at(conn: sqlite3.Connection, upto_vol: int) -> list[dict[str, Any]]:
    """Every claim first evidenced strictly after `upto_vol` -- `F>t` at the claim level."""
    rows = conn.execute(
        "SELECT * FROM claims WHERE first_vol > ? ORDER BY claim_id", (upto_vol,)
    ).fetchall()
    return [dict(row) for row in rows]


def future_intervals_at(conn: sqlite3.Connection, upto_vol: int) -> list[dict[str, Any]]:
    """Every interval that opens strictly after `upto_vol` -- `F>t` at the interval level, the
    granularity a rendered page actually reflects (`graph/temporal.py::state_at`'s counterpart)."""
    rows = conn.execute(
        "SELECT * FROM intervals WHERE vol_start > ? ORDER BY interval_id", (upto_vol,)
    ).fetchall()
    return [dict(row) for row in rows]
