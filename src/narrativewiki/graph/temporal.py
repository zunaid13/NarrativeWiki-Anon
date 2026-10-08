"""[4] Interval assignment and the two spoiler-safe read queries.

Inputs:     Per-(subject, predicate) fact sequences. For `single: true` attributes (config/
            extraction.yaml), the sequence must already be resolved to ONE value per first_vol —
            graph/contradictions.py resolves same-volume ties before calling in here. Everything
            else (relations, non-single attributes, traits) is handed in as raw observations;
            there is nothing to arbitrate because more than one value being true at once is
            expected, not a conflict.
Outputs:    Interval dicts matching CONTRACTS §4 `intervals` columns (`claim_ids` as a plain list —
            graph/store.py JSON-encodes it); `state_at`/`history_at`/`relations_at`/
            `relation_history_at`/`all_edges_at`,
            the only sanctioned ways to read the `intervals`/`edges` tables; and `evidence_at`, the
            only sanctioned way to turn a claim id list back into quoted evidence text.
Invariants: - A `single: true` predicate supersedes: each value's interval closes the moment the
            next distinct value's first_vol begins, so a rank promotion renders as history, not a
            contradiction (CLAUDE.md §2, CONTRACTS §4.2).
            - A relation predicate group declared mutually exclusive (config `conflicts_with`,
            Phase 17) supersedes the same way, via `assign_relation_supersession` — the one
            exception to "relation intervals never close" below, scoped to conflict-group members
            only; resolved by graph/contradictions.py::_resolve_relation_group.
            - Every other predicate is multi-valued: distinct values coexist as separate,
            permanently-open intervals. Duplicate observations of the SAME value (e.g. the same
            nickname mentioned again in a later volume) collapse to the earliest first_vol seen —
            first_vol always means "first evidence", never "most recent".
            - `state_at`/`history_at`/`relations_at`/`relation_history_at`/`all_edges_at` all
              filter `vol_start <= vol`
            before anything else, so the spoiler boundary cannot be forgotten at a call site
            (CONTRACTS §4.1). Nothing outside this module may query `intervals` or `edges`
            directly (Phase 5, synth/assemble.py and graph/ppr.py, extends this discipline to
            `edges` — it is just as spoiler-sensitive as `intervals`, even though today's known
            gap means no edge ever actually closes).
            - A claim's `evidence` list can itself span multiple volumes: extract/claims.py merges
            repeated observations of the SAME fact into one claim_id, so a claim whose `first_vol`
            is 1 can carry a v03 quote that confirms it again later. `first_vol <= upto_vol` only
            clears the CLAIM (and the interval built from it) — it says nothing about which of
            that claim's individual quotes are safe to print. `evidence_at` filters each quote by
            its OWN para_id volume for exactly this reason; a caller that reads `claims.evidence_json`
            any other way (as Phase 5's `wiki explain` originally did, real-data-tested and fixed
            in the same commit that added this function) reproduces the leak CLAUDE.md §1 forbids.
Contract:   docs/CONTRACTS.md §4, §4.1.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any


_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMBER_UNIT_WORDS = {"years", "year", "yrs", "yr", "old", "of", "age"}


def _normalize_number_or_range(text: str) -> str:
    """`fmt="number_or_range"`: fold spelled-out numbers to digits and drop trailing unit words,
    so `'25'`, `'twenty-five'`, `'twenty-five years old'` and `'twenty-five years'` all become
    `'25'` (the real-data AGE case: Lawrence carried all four as distinct values before this
    existed — CLAUDE.md's ban on special-casing predicates means this has to work off `format`,
    not the string "AGE"). Also folds a spelled-out range ("twenty-five to thirty") to `'25-30'`,
    same as an already-numeric one ("17-19"). A token this can't place (an unknown word, not a
    number and not a recognized unit) is simply skipped rather than guessed at — if nothing
    numeric survives, the plain casefolded text is returned unchanged, so an attribute that
    happens to declare this format but holds genuinely non-numeric text degrades to ordinary
    identity normalization instead of erroring.
    """
    tokens = re.sub(r"[-\s]+", " ", text).split()
    numeric_tokens: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _TENS and i + 1 < len(tokens) and tokens[i + 1] in _ONES and _ONES[tokens[i + 1]] < 10:
            numeric_tokens.append(str(_TENS[tok] + _ONES[tokens[i + 1]]))
            i += 2
            continue
        if tok in _TENS:
            numeric_tokens.append(str(_TENS[tok]))
        elif tok in _ONES:
            numeric_tokens.append(str(_ONES[tok]))
        elif tok.isdigit():
            numeric_tokens.append(str(int(tok)))
        # else: not a number word, not a digit -- either a unit word (dropped) or unknown
        # (dropped rather than guessed at, per the docstring above).
        i += 1
    if not numeric_tokens:
        return text
    return "-".join(numeric_tokens)


def normalize_value(value: str, fmt: str | None = None) -> str:
    """Case/whitespace-insensitive identity key for 'is this the same value again'.

    The extractor is not guaranteed to reproduce identical casing for the same fact across
    volumes ("Traveling merchant" in v1, "traveling merchant" in v2) — both graph/contradictions.py
    (same-value vs. narrative-change) and this module's own merge below must treat those as one
    value, using this normalization, or a re-stated fact renders as a spurious supersession with a
    one-volume-wide history entry that says nothing actually changed.

    `fmt` (Phase 22 A2): `config/extraction.yaml` `attributes.*.format`, previously unread by
    anything. Only `"number_or_range"` currently changes behaviour (see
    `_normalize_number_or_range`); every other value of `fmt`, including `None`, leaves the
    plain casefold-and-strip identity unchanged.
    """
    text = value.strip().casefold()
    if fmt == "number_or_range":
        return _normalize_number_or_range(text)
    return text


def make_interval_id(subject: str, predicate: str, object_or_value: str, vol_start: int) -> str:
    digest = hashlib.sha1(
        f"{subject}|{predicate}|{object_or_value}|{vol_start}".encode("utf-8")
    ).hexdigest()
    return f"i_{digest[:8]}"


def assign_single_valued(
    subject: str, predicate: str, ordered_values: list[dict[str, Any]], fmt: str | None = None
) -> list[dict[str, Any]]:
    """Build supersession intervals for a `single: true` attribute.

    `ordered_values`: one entry per distinct first_vol — `{"value", "qualifier", "first_vol",
    "claim_ids"}` — not necessarily pre-sorted. Consecutive entries whose value is identical under
    `normalize_value` are merged defensively (their claim_ids combine into one interval, keeping
    the first-seen casing) even though graph/contradictions.py should not produce that case itself.
    `fmt` (Phase 22 A2, `attributes.*.format`) is passed straight through to `normalize_value` so
    this merge also catches phrasing differences `normalize_value`'s bare casefold cannot -- e.g.
    AGE's `'twenty-five years'` (v2) merging into `'twenty-five years old'` (v1) once both fold to
    `'25'`, rather than rendering as two intervals joined by a `superseded_by` link that implies a
    narrative change nothing in the text supports.
    """
    ordered = sorted(ordered_values, key=lambda e: e["first_vol"])
    merged: list[dict[str, Any]] = []
    for entry in ordered:
        if merged and normalize_value(merged[-1]["value"], fmt) == normalize_value(entry["value"], fmt):
            merged[-1]["claim_ids"] = [*merged[-1]["claim_ids"], *entry["claim_ids"]]
            merged[-1]["confidence"] = max(merged[-1].get("confidence", 0.0), entry.get("confidence", 0.0))
            continue
        merged.append({**entry, "claim_ids": list(entry["claim_ids"])})

    intervals: list[dict[str, Any]] = []
    for i, entry in enumerate(merged):
        vol_start = entry["first_vol"]
        has_next = i + 1 < len(merged)
        vol_end = merged[i + 1]["first_vol"] - 1 if has_next else None
        superseded_by = (
            make_interval_id(subject, predicate, merged[i + 1]["value"], merged[i + 1]["first_vol"])
            if has_next
            else None
        )
        intervals.append(
            {
                "interval_id": make_interval_id(subject, predicate, entry["value"], vol_start),
                "subject": subject,
                "predicate": predicate,
                "object": None,
                "value": entry["value"],
                "qualifier": entry.get("qualifier"),
                "vol_start": vol_start,
                "vol_end": vol_end,
                "claim_ids": entry["claim_ids"],
                "superseded_by": superseded_by,
                # Phase 23 B1: the winning claim's own confidence (arbitration already picked the
                # single true value for this volume; there is no "max across claim_ids" to take,
                # unlike assign_multi_valued where every value coexists). Missing/absent (existing
                # callers that never threaded confidence through, e.g. some test fixtures) reads
                # as 0.0, not a crash -- synth/assemble.py's downstream gate treats 0.0 as "below
                # threshold", the safe default direction for un-instrumented callers.
                "confidence": entry.get("confidence", 0.0),
                "evidence_count": len(entry["claim_ids"]),
            }
        )
    return intervals


def assign_relation_supersession(
    subject: str, obj: str, ordered_predicates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build supersession intervals for a group of mutually-exclusive relation predicates on one
    (subject, object) pair (Phase 17 — config/extraction.yaml `relations.*.conflicts_with`, e.g.
    FRIEND_OF/ENEMY_OF). Structurally `assign_single_valued` with `predicate` varying per entry
    instead of fixed and `object`/`value` following the relation-interval convention
    `assign_multi_valued` already established (`object` carries the target, `value` stays null) —
    every other relation predicate keeps the permanently-open intervals `assign_multi_valued`
    already produces; only conflict-group members ever close.

    `ordered_predicates`: one entry per distinct first_vol at which the winning predicate for
    this pair changed — `{"predicate", "qualifier", "first_vol", "claim_ids"}` — resolved by
    graph/contradictions.py::_resolve_relation_group before this is called (same-volume ties
    already arbitrated away, so at most one predicate per first_vol here).
    """
    # [32] Consecutive winners with the SAME predicate are one relationship continuing, not a
    # change: Bookworm's Effa SPOUSE_OF Gunther ("husband" in v1, "married" in v2) closed at v1 and
    # the v2 page printed the marriage as "(ended v1)". Only a different predicate supersedes.
    ordered: list[dict[str, Any]] = []
    for entry in sorted(ordered_predicates, key=lambda e: e["first_vol"]):
        if ordered and ordered[-1]["predicate"] == entry["predicate"]:
            prev = ordered[-1]
            prev["claim_ids"] = list(prev["claim_ids"]) + list(entry["claim_ids"])
            prev["confidence"] = max(prev.get("confidence", 0.0), entry.get("confidence", 0.0))
            continue
        ordered.append(dict(entry))
    intervals: list[dict[str, Any]] = []
    for i, entry in enumerate(ordered):
        vol_start = entry["first_vol"]
        has_next = i + 1 < len(ordered)
        vol_end = ordered[i + 1]["first_vol"] - 1 if has_next else None
        superseded_by = (
            make_interval_id(subject, ordered[i + 1]["predicate"], obj, ordered[i + 1]["first_vol"])
            if has_next
            else None
        )
        intervals.append(
            {
                "interval_id": make_interval_id(subject, entry["predicate"], obj, vol_start),
                "subject": subject,
                "predicate": entry["predicate"],
                "object": obj,
                "value": None,
                "qualifier": entry.get("qualifier"),
                "vol_start": vol_start,
                "vol_end": vol_end,
                "claim_ids": list(entry["claim_ids"]),
                "superseded_by": superseded_by,
                "confidence": entry.get("confidence", 0.0),  # Phase 23 B1
                "evidence_count": len(entry["claim_ids"]),
            }
        )
    return intervals


def assign_multi_valued(
    subject: str,
    predicate: str,
    observations: list[dict[str, Any]],
    *,
    is_relation: bool,
    fmt: str | None = None,
) -> list[dict[str, Any]]:
    """Build permanently-open intervals, one per distinct value, for anything that is not a
    `single: true` attribute. `observations`: `{"value_or_object", "qualifier", "first_vol",
    "claim_id"}`, any number of entries per distinct value — duplicates collapse to the earliest
    first_vol, per the module invariant.

    A relation's `value_or_object` is already an entity_id (always exact) and is bucketed
    verbatim, never through `normalize_value`. An attribute/trait's is free text -- Phase 22 A2:
    bucketing used to be exact-string, so e.g. "Traveling merchant"/"traveling merchant" as TITLE
    values would have rendered as two rows instead of one (the same casing bug
    `assign_single_valued` already guards against for `single: true` attributes). Bucketed by
    `normalize_value(value, fmt)`; each bucket keeps the raw phrasing of whichever observation has
    the earliest `first_vol` as its display value, the same "first-seen wins" precedent
    `assign_single_valued` sets.
    """
    by_value: dict[str, dict[str, Any]] = {}
    for obs in observations:
        raw = obs["value_or_object"]
        key = raw if is_relation else normalize_value(raw, fmt)
        bucket = by_value.get(key)
        if bucket is None:
            bucket = {
                "display": raw, "first_vol": obs["first_vol"], "qualifier": None, "qualifier_vol": None,
                "claim_ids": [], "confidence": 0.0,
            }
            by_value[key] = bucket
        elif obs["first_vol"] < bucket["first_vol"]:
            bucket["display"] = raw
            bucket["first_vol"] = obs["first_vol"]
        # The qualifier is printed from vol_start on, so it is the earliest one, never the first one
        # in input order: Anne's Diana FRIEND_OF interval opened at v1 ("bosom friend") but carried
        # v2's "close friends who spend time together at Echo Lodge" into the v01 wiki.
        if obs.get("qualifier") and (bucket["qualifier"] is None or obs["first_vol"] < bucket["qualifier_vol"]):
            bucket["qualifier"], bucket["qualifier_vol"] = obs["qualifier"], obs["first_vol"]
        bucket["claim_ids"].append(obs["claim_id"])
        # Phase 23 B1: every observation of this SAME value is repeat corroboration of one fact
        # (unlike assign_single_valued/assign_relation_supersession, where distinct entries are
        # distinct competing claims already arbitrated down to one winner) -- mirrors the
        # existing claim-merge philosophy (extract/claims.py::_merge_claim,
        # `confidence = max(existing, new)`) one layer up, at interval-build time.
        bucket["confidence"] = max(bucket["confidence"], obs.get("confidence", 0.0))

    intervals: list[dict[str, Any]] = []
    for bucket in by_value.values():
        value = bucket["display"]
        intervals.append(
            {
                "interval_id": make_interval_id(subject, predicate, value, bucket["first_vol"]),
                "subject": subject,
                "predicate": predicate,
                "object": value if is_relation else None,
                "value": None if is_relation else value,
                # A qualifier first seen after vol_start would be printed before its volume: drop it.
                "qualifier": bucket["qualifier"] if bucket["qualifier_vol"] == bucket["first_vol"] else None,
                "vol_start": bucket["first_vol"],
                "vol_end": None,
                "claim_ids": bucket["claim_ids"],
                "superseded_by": None,
                "confidence": bucket["confidence"],
                "evidence_count": len(bucket["claim_ids"]),
            }
        )
    return intervals


def state_at(conn: sqlite3.Connection, entity_id: str, vol: int) -> list[sqlite3.Row]:
    """CONTRACTS §4.1: intervals true at `vol`, for a reader who has read up to volume `vol`.

    An interval whose `vol_end` is in the future relative to `vol` is still included (it has not
    closed yet, as far as this reader knows) — but its `vol_end` value itself must never be
    surfaced verbatim by a renderer at this cutoff, since the number reveals *when* the change
    happens. That scrub is synth/assemble.py's job (Phase 5), not this query's: this query's
    contract is only "which rows are true", not "which fields of those rows are safe to print".
    """
    return conn.execute(
        "SELECT * FROM intervals WHERE subject = ? AND vol_start <= ? "
        "AND (vol_end IS NULL OR vol_end >= ?) ORDER BY predicate, vol_start",
        (entity_id, vol, vol),
    ).fetchall()


def history_at(conn: sqlite3.Connection, entity_id: str, vol: int) -> list[sqlite3.Row]:
    """CONTRACTS §4.1: intervals already superseded by `vol` — the "previously..." expandable
    section on a page. Still filters `vol_start <= vol` first: an interval that both opens and
    closes after the cutoff must not appear in history just because it happens to be closed *now*
    in the full graph."""
    return conn.execute(
        "SELECT * FROM intervals WHERE subject = ? AND vol_start <= ? "
        "AND vol_end IS NOT NULL AND vol_end < ? ORDER BY predicate, vol_start",
        (entity_id, vol, vol),
    ).fetchall()


def relations_at(conn: sqlite3.Connection, entity_id: str, vol: int) -> list[sqlite3.Row]:
    """Current relation intervals touching `entity_id`, visible at cutoff `vol` — for Phase 5's
    "relationships"/"affiliations" page fields, which need each interval's `claim_ids` for
    evidence in a way the denormalised `edges` table does not carry.

    A relation interval is one where `object IS NOT NULL` (graph/temporal.py's own convention:
    `assign_multi_valued` puts the object there and leaves `value` NULL for a relation, the
    reverse of an attribute/trait interval). Unlike `state_at`, this checks `subject = ? OR
    object = ?`: graph/contradictions.py::_canonicalize_relation stores a symmetric or inverse
    pair on ONE side by predicate/subject-object sort order, not by which entity's page is being
    built, so `(shin, FRIEND_OF, raiden)` must be found when building Raiden's page too, even
    though Raiden is the `object`, not the `subject`, of the stored row. Still filters
    `vol_start <= vol` first, same discipline as `state_at`, and excludes intervals already
    closed before the cutoff. Use `relation_history_at` when a consumer explicitly needs them.
    """
    return conn.execute(
        "SELECT * FROM intervals WHERE object IS NOT NULL AND (subject = ? OR object = ?) "
        "AND vol_start <= ? AND (vol_end IS NULL OR vol_end >= ?) ORDER BY predicate, vol_start",
        (entity_id, entity_id, vol, vol),
    ).fetchall()


def relation_history_at(conn: sqlite3.Connection, entity_id: str, vol: int) -> list[sqlite3.Row]:
    """Closed relation intervals touching either side of ``entity_id`` at cutoff ``vol``."""
    return conn.execute(
        "SELECT * FROM intervals WHERE object IS NOT NULL AND (subject = ? OR object = ?) "
        "AND vol_start <= ? AND vol_end IS NOT NULL AND vol_end < ? ORDER BY predicate, vol_start",
        (entity_id, entity_id, vol, vol),
    ).fetchall()


def relations_between(conn: sqlite3.Connection, a: str, b: str, vol: int) -> list[sqlite3.Row]:
    """Relation intervals between exactly `a` and `b` (either storage direction), still true (or
    not yet revealed as closed) at cutoff `vol` — `state_at`'s "still true, vol_end scrub is the
    caller's job" semantics (CONTRACTS §4.1), scoped to one pair instead of one entity. Since
    `graph/contradictions.py::_canonicalize_relation` stores subject/object so they always match
    the CANONICAL predicate's own direction (never scrambled relative to it), a row's `subject`/
    `object` here can be rendered directly — no per-viewer flip like `synth/assemble.py::
    _relationship_entries` needs, because a pair page names both parties explicitly. Used by
    `site/bundle.py::build_relationship_bundles` (Phase 21)."""
    return conn.execute(
        "SELECT * FROM intervals WHERE object IS NOT NULL "
        "AND ((subject = ? AND object = ?) OR (subject = ? AND object = ?)) "
        "AND vol_start <= ? AND (vol_end IS NULL OR vol_end >= ?) "
        "ORDER BY predicate, vol_start",
        (a, b, b, a, vol, vol),
    ).fetchall()


def relation_history_between(conn: sqlite3.Connection, a: str, b: str, vol: int) -> list[sqlite3.Row]:
    """Relation intervals between `a` and `b` already superseded by `vol` — `history_at`'s
    counterpart, scoped to one pair (the pair-specific form of `relation_history_at`). Only ever
    non-empty for `conflicts_with` predicate groups (Phase 17 `assign_relation_supersession`,
    e.g. FRIEND_OF -> ENEMY_OF) — every other relation predicate stays permanently open, so this
    returns `[]` for the overwhelming majority of pairs."""
    return conn.execute(
        "SELECT * FROM intervals WHERE object IS NOT NULL "
        "AND ((subject = ? AND object = ?) OR (subject = ? AND object = ?)) "
        "AND vol_start <= ? AND vol_end IS NOT NULL AND vol_end < ? "
        "ORDER BY predicate, vol_start",
        (a, b, b, a, vol, vol),
    ).fetchall()


def all_edges_at(conn: sqlite3.Connection, vol: int) -> list[sqlite3.Row]:
    """Every relation edge visible at cutoff `vol`, across the whole graph — the volume-filtered
    subgraph graph/ppr.py runs Personalized PageRank over (CONTRACTS §4: `edges` is described as
    "relation intervals, for PPR", a denormalised mirror of the relation rows in `intervals` kept
    specifically for this whole-graph read, so ppr.py does not have to call `relations_at` once
    per entity to reconstruct the graph). Filters `vol_start <= vol` first, same discipline as
    `state_at`/`relations_at` — a relation discovered after the cutoff must not bias PPR toward
    entities the reader could not yet know are connected; edges already closed before `vol` are
    excluded from the current subgraph.
    """
    return conn.execute(
        "SELECT * FROM edges WHERE vol_start <= ? AND (vol_end IS NULL OR vol_end >= ?) "
        "ORDER BY subject, predicate, object",
        (vol, vol),
    ).fetchall()


def evidence_at(conn: sqlite3.Connection, claim_ids: list[str], vol: int) -> list[dict[str, Any]]:
    """Quoted evidence for `claim_ids`, filtered to `vol` — the only sanctioned way to turn a
    claim id list (as embedded in an interval's `claim_ids_json`) back into printable quotes.

    Reads `claims`, not `intervals`/`edges` — `claims` is provenance/audit and has no cutoff of
    its own (graph/store.py's module docstring) — but a single claim's `evidence` array can still
    span multiple volumes (see module docstring), so this filters each quote by its own para_id
    volume rather than trusting the claim's `first_vol`. A claim that has already cleared
    `first_vol <= vol` to be selected by `state_at`/`history_at`/`relations_at`/
    `relation_history_at` always has at
    least one surviving quote here — `first_vol` is defined as the earliest evidence volume.

    Returns `[{"claim_id", "para_id", "quote", "confidence"}, ...]`, in `claim_ids` order.
    """
    results: list[dict[str, Any]] = []
    for claim_id in claim_ids:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if row is None:
            continue
        for ev in json.loads(row["evidence_json"]):
            ev_vol = int(ev["para_id"].split(":", 1)[0][1:])
            if ev_vol <= vol:
                results.append(
                    {
                        "claim_id": claim_id,
                        "para_id": ev["para_id"],
                        "quote": ev["quote"],
                        "confidence": row["confidence"],
                    }
                )
    return results
