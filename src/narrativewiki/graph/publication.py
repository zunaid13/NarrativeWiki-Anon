"""Shared reader-facing claim semantics for publication consumers.

This module is intentionally small while the broader publication projection in plan 0007 is
built incrementally. It centralizes semantics that must agree across character pages and pair
pages without changing the graph schema or mutating stored claims.

All helpers are cutoff-aware: a later claim may refine an interval's polarity, but it cannot
affect what a reader at an earlier cutoff sees.
"""

from __future__ import annotations

import sqlite3


_POLARITY_PREFIX = {"presumed": "presumed ", "denied": "not "}
UNTRACEABLE_EVIDENCE_NOTICE = "source evidence unavailable"


def claim_polarity(conn: sqlite3.Connection, claim_ids: list[str], cutoff: int) -> str:
    """Return the most recent polarity whose own claim is visible at ``cutoff``.

    Intervals can merge observations from several volumes. Reading the first claim would leave
    a stale ``presumed`` label in place; reading the last claim without filtering could leak a
    later confirmation. Missing provenance retains the legacy ``asserted`` fallback for now.
    """
    if not claim_ids:
        return "asserted"
    placeholders = ",".join("?" for _ in claim_ids)
    rows = conn.execute(
        f"SELECT polarity, first_vol FROM claims WHERE claim_id IN ({placeholders}) AND first_vol <= ?",
        (*claim_ids, cutoff),
    ).fetchall()
    if not rows:
        return "asserted"
    return max(rows, key=lambda row: row["first_vol"])["polarity"]


def polarity_prefixed(value: str, polarity: str | None) -> str:
    """Render uncertainty or denial without collapsing it into an asserted value."""
    return f"{_POLARITY_PREFIX.get(polarity or 'asserted', '')}{value}"


def evidence_notice(para_ids: list[str] | None) -> str | None:
    """Return the one public warning used when an assertion has no traceable source pointer.

    Story uncertainty belongs in ``polarity`` and is not an evidence failure. Extractor
    uncertainty is already admitted by the configured confidence floor. This helper handles the
    remaining reader-facing case: a shipped assertion whose source occurrence cannot be traced.

    ``None`` means *the assembler recorded no evidence field at all* — a page artifact predating
    citation-carrying assembly — and stays silent: telling a reader "source evidence unavailable"
    on every row of a stale bundle is a louder lie than the omission it replaces. Only a recorded
    empty list, meaning assembly looked and found nothing traceable, earns the notice.
    """
    if para_ids is None:
        return None
    return None if para_ids else UNTRACEABLE_EVIDENCE_NOTICE
