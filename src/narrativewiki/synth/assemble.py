"""[5, 20] Builds the CONTRACTS §5 page model from graph rows. No LLM involved (CLAUDE.md file map).

Inputs:     `data/04_graph/graph.db`, read only through `graph/temporal.py`'s sanctioned queries
            (`state_at`/`history_at`/`relations_at`) and `graph/ppr.py::personalized_pagerank`;
            `config/extraction.yaml`'s `attributes`/`relations`/`conflicts` blocks
            (`Settings.attributes`/`.relations`/`.conflicts_config`); one gazetteer entity dict
            for the subject.
Outputs:    `assemble_page()` -> a dict matching CONTRACTS §5, with `prose` left as one `None`
            entry per `settings.page_outline` `kind: prose` section (Phase 13) and `generated_by`
            left `None` — Phase 6's `synth/prose.py` fills those in, and Phase 6's `synthesize`
            command is what actually writes `data/05_pages/<id>/v<NN>.json` to disk (this module
            only builds the dict). Phase 20 adds `"quotes": None` placeholder for any `kind:
            quotes` section; `build_quotes_section()` fills it in at synthesize time.
            `trait_values_at()` is a second export for Phase 6's prompt input; it is NOT part of
            the public page JSON — see the field-naming note below. `fact_set_for_verification()`
            (Phase 22 C3) is a third such export, for `graph/verify.py`'s per-character
            verification call.
Invariants: - CLAUDE.md §1: every field here is built from the ALREADY-cutoff-filtered rows
            `state_at`/`history_at`/`relations_at` return. Nothing is computed from the unfiltered
            graph and redacted after.
            - A currently-open interval whose `vol_end` is non-NULL (queued to close later, per
            `state_at`'s own docstring) never surfaces that `vol_end` number, its
            `superseded_by` id, or even the fact that it closes: a "changes later" marker on a v1
            page tells the reader a later volume changes it (docs/vision/PHASE_32.md, req. 9).
            - Field keys are `predicate.lower()`, generic over whatever `config/extraction.yaml`
            defines — that file's own header says "Adding a predicate or attribute here is all
            that is needed — rendering is generic," and this module honours that literally rather
            than hard-coding "titles"/"nicknames" as CONTRACTS §5's illustrative example spells
            them (CONTRACTS.md was updated in the same commit as this module to match).
            - Only `attributes.<PRED>.single: true` predicates render as one scalar field with
            `since_vol`/`history`; everything else is a flat list with no history, matching
            `assign_multi_valued` never producing a `vol_end` to begin with (Phase 4's handover).
            Every superseded value stays in `history` and is rendered with the volumes it held
            (Phase 32: the old `conflicts.quiet_change` hid it for AGE/RANK/TITLE/OCCUPATION, so a
            v2 page erased what v1 said).
            - `AFFILIATED_WITH` is the one relation predicate identified by name rather than by a
            config flag — it renders into `fields["affiliations"]`; every other relation predicate
            renders into `fields["relationships"]`. `relations_at` may return this entity as either
            `subject` or `object` (canonical storage direction is independent of whose page is
            being built — Phase 4's handover); a non-symmetric predicate's label flips to its
            configured `inverse` when this entity is the `object`.
            - Traits (PERSONALITY/MOTIVATION/SKILL/FEAR/BACKGROUND) are never a page field — they
            feed prose (extraction.yaml's own note) — so they are read by `trait_values_at()` only,
            never folded into `assemble_page()`'s `fields`.
            - Phase 20: `build_quotes_section(events_conn, entity_id, upto_vol, section)` is a
            deterministic (no LLM) export that selects top-N verbatim quotes for the character
            from events.db quote claims. Spoiler-fenced to `upto_vol` via the caller's existing
            `character_events_at` + `event_claims_at` spoiler discipline.
            - Phase 22 A5 (fixes U3-U7, S5, S6), all still deterministic/no-new-evidence:
              * U6: a `single: true` attribute with zero rows and a configured `default`
                (`config/extraction.yaml` `attributes.*.default`) materializes as a scalar field
                marked `inferred: true` -- never fabricated as a graph interval, never affecting
                `claim_set_hash`.
              * U7: `_claim_polarity` now takes `upto_vol` and picks the polarity of the
                MOST-RECENT claim among an interval's `claim_ids` whose OWN `first_vol <= upto_vol`
                (previously: `claim_ids[0]` unconditionally, which could read a not-yet-visible
                claim's polarity -- a spoiler leak, since a merged interval's claim_ids run oldest
                to newest but are not all guaranteed <= this cutoff) or reveal a stale "presumed"
                after a later-but-still-visible claim confirmed the fact. Every scalar field also
                gains `inferred: false` (or `true`, U6) for shape consistency.
              * U3: every scalar/list attribute value and every relationship entry gains
                `show_vol` -- true only when its `since_vol` is later than the entity's own
                `first_vol` (a real reveal), false for anything known since the character's first
                appearance (a "(since v1)" on day one carries no information). Rendering is the
                caller's job (site/okf.py, app.js); history/expandable spans are unaffected.
              * U4: a relationship entry's `label` is now always the config `display` text (never
                overridden by free-text `qualifier`); `qualifier` is demoted to `note` (`None` when
                absent) and combined into `blurb` via the predicate's optional `relations.*.blurb`
                template (`.format(label=, note=)`) or the generic "{label} -- {note}" fallback.
                Also fixes the sibling bug: explicit `relation_history_at` rows let affiliations
                show a former membership as `current: false`, while the `relationships` branch
                previously discarded that closed/`vol_end` computation entirely, rendering a superseded relation
                (e.g. a `conflicts_with`-closed FRIEND_OF) as if still live. Relationship entries
                now carry `current`/`vol_end` (scrubbed the same way affiliations already are)
                exactly like affiliations do.
              * U5: `mentions_of` is now `[{"entity_id", "note"}, ...]`, filtered to drop anyone
                already surfaced in `fields.affiliations`/`fields.relationships` (the PPR chip row
                was 75% duplicative on the audited page), and annotated via
                `graph/temporal.py::relations_between` (a relation not otherwise rendered) or,
                failing that, `graph/events.py::shared_events_at` (a shared-scene count) --
                `note` stays `None` when neither explains the connection (a genuine multi-hop PPR
                result). Needs `events_conn`, now a required `assemble_page` parameter (`None`
                when events.db does not exist yet, same optionality every events.db caller uses).
              * S6: `page["lead"]` (via `_build_lead`) is a short "status · primary affiliation"
                summary string, built only from `fields` already assembled here plus
                `entities_by_id` (now a required `assemble_page` parameter) for the affiliation's
                canonical name; `None` when neither is known yet. Paired with `config/
                extraction.yaml`'s new `page_outline` `lead` section (`kind: lead`, `order: -1`).
            - Phase 23 E1/E2/E5 (page restructuring, all still deterministic):
              * E2: an attribute predicate claimed by a `source: "attributes"` prose section
                (`config/extraction.yaml` `page_outline`, e.g. `appearance`'s APPEARANCE) is
                excluded from `_build_attribute_fields`'s generic infobox loop -- `synth/prose.py`
                renders it as a paragraph instead, so it must not also render as a bullet.
              * E5: `page["aliases"]` (dropped from the JSON page model since Phase 6, though
                `site/okf.py`'s Markdown front matter always had it from `entity` directly) is now
                a first-class page field, mirroring `entity.get("aliases", [])` exactly.
              * S5: `has_min_evidence()` and `find_name_collisions()` are two new deterministic
                checks `wiki synthesize` calls before writing a page -- the former gates on
                `config/extraction.yaml` `page_gate.min_claims` (reusing `_claim_set_hash`'s own
                interval-id query, factored out as `_visible_interval_ids`); the latter is a
                gazetteer-wide scan (not cutoff-scoped) that flags two DIFFERENTLY-typed entities
                sharing one canonical name (the audit's real example: Saint Ruvinheigen is both a
                character and the city he is named for) -- `entities/alias.py` already guarantees
                distinct `entity_id`s via a numeric suffix, so this is a same-*name* collision, not
                an id collision; detection only, printed as a build-time warning.
Contract:   docs/CONTRACTS.md §5.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from typing import Any

from ..entities.gazetteer import aliases_at
from ..graph import ppr, publication, store, temporal

_AFFILIATION_PREDICATE = "AFFILIATED_WITH"


def _decode_claim_ids(row: sqlite3.Row) -> list[str]:
    return json.loads(row["claim_ids_json"])


def _claim_polarity(conn: sqlite3.Connection, claim_ids: list[str], upto_vol: int) -> str:
    """`intervals` carries no `polarity` column (CONTRACTS §4's schema) — it lives on `claims`.

    Phase 22 A5 (U7): an interval's `claim_ids` can span multiple original observations merged
    defensively by `graph/temporal.py::assign_single_valued` (consecutive same-value entries),
    in oldest-to-newest order — but "oldest" is not "safe", and reading `claim_ids[0]`
    unconditionally (the pre-A5 behaviour) has two bugs: it can freeze a stale `presumed` polarity
    forever even after a later claim (still visible at this cutoff) confirmed the fact, AND —
    the spoiler-relevant half — a merge can pull in a claim whose OWN `first_vol` is *after*
    this cutoff (the merge only respects the group's earliest `first_vol` for `vol_start`,
    `graph/temporal.py`'s own docstring), so `claim_ids[0]` is not even guaranteed to be the
    unsafe one. This selects, among the claims whose `first_vol <= upto_vol`, the one with the
    LATEST `first_vol` — the most up-to-date polarity a reader at this cutoff could actually
    know — and never reads a not-yet-visible claim's polarity at all.
    """
    return publication.claim_polarity(conn, claim_ids, upto_vol)


def polarity_prefixed(value: str, polarity: str | None) -> str:
    """Phase 22 A5 (U7): "dead" reads as "presumed dead"/"not dead" when the underlying claim's
    polarity says so, instead of collapsing to indistinguishable from a plain assertion —
    `config/extraction.yaml`'s own `polarity:` block says this collapse "must never happen".
    Shared by `_build_lead` below; the implementation lives in `graph/publication.py` so bundle
    consumers use exactly the same semantics. `site/okf.py` still mirrors the final text helper."""
    return publication.polarity_prefixed(value, polarity)


def _show_vol(since_vol: int | None, entity_first_vol: int | None) -> bool:
    """Phase 22 A5 (U3): true only when `since_vol` is a genuine reveal -- later than the
    entity's own `first_vol` (its first appearance). A value known since the character's very
    first appearance carries no information in a "(since v1)" marker; a value that changed or
    was newly revealed later does. Unknown baseline (`entity_first_vol is None`, a caller that
    never passed the entity's own `first_vol`) defaults to showing the marker -- conservative,
    never hides information for lack of a comparison point."""
    if since_vol is None:
        return False
    if entity_first_vol is None:
        return True
    return since_vol > entity_first_vol


def _scalar_field(
    conn: sqlite3.Connection,
    current_rows: list[sqlite3.Row],
    history_rows: list[sqlite3.Row],
    upto_vol: int,
    entity_first_vol: int | None,
) -> dict[str, Any]:
    """Phase 22 A5: `upto_vol` threads into `_claim_polarity` (U7 — spoiler-safe "most recent
    visible claim" polarity, not `claim_ids[0]`); `entity_first_vol` drives `show_vol` (U3 — only
    a genuine reveal gets a "(since vN)" marker); `inferred` is always present (`False` here —
    `_build_attribute_fields` is the only place that sets it `True`, for a `default:`-materialized
    field with no rows at all)."""
    def evidence_for(row: sqlite3.Row) -> list[str]:
        return sorted(
            {e["para_id"] for e in temporal.evidence_at(conn, _decode_claim_ids(row), upto_vol)}
        )

    history = [
        {
            "value": row["value"],
            "vols": [row["vol_start"], row["vol_end"]],  # both bounds already <= cutoff: safe
            "polarity": _claim_polarity(conn, _decode_claim_ids(row), upto_vol),
            "evidence": evidence_for(row),
        }
        for row in sorted(history_rows, key=lambda r: r["vol_start"])
    ]
    if not current_rows:
        # Structurally shouldn't happen — assign_single_valued always leaves the last entry
        # open — but a subject can legitimately have only history (e.g. STATUS resolved as
        # `flag`/`keep_both` upstream leaves a provisional value; guard rather than crash).
        return {
            "value": None, "since_vol": None, "show_vol": False, "history": history,
            "inferred": False, "evidence": [],
        }
    row = current_rows[0]
    return {
        "value": row["value"],
        "since_vol": row["vol_start"],
        "show_vol": _show_vol(row["vol_start"], entity_first_vol),
        "polarity": _claim_polarity(conn, _decode_claim_ids(row), upto_vol),
        "evidence": evidence_for(row),
        "history": history,
        "inferred": False,
    }


def _list_field(
    conn: sqlite3.Connection,
    current_rows: list[sqlite3.Row],
    upto_vol: int,
    entity_first_vol: int | None,
) -> list[dict[str, Any]]:
    return [
        {
            "value": row["value"],
            "since_vol": row["vol_start"],
            "show_vol": _show_vol(row["vol_start"], entity_first_vol),
            "evidence": sorted(
                {e["para_id"] for e in temporal.evidence_at(conn, _decode_claim_ids(row), upto_vol)}
            ),
        }
        for row in sorted(current_rows, key=lambda r: (r["vol_start"], r["value"]))
    ]


def _is_renderable(row: sqlite3.Row, verdicts: dict[str, dict[str, Any]]) -> bool:
    """Phase 23 B2. `wiki verify` (graph/verify.py) may flag an interval as `unsupported` -- its
    own cited evidence does not actually hold up the fact. A flagged interval is withheld here,
    at render time, never deleted: the claim/interval/rationale stay fully queryable via `wiki
    explain`/`wiki trace`, and an empty `verdicts` table (no verify run yet, or every fact passed)
    makes this a pure no-op -- `verdicts.get(row["interval_id"])` is `None`, so every row renders
    exactly as before. CLAUDE.md's zero-evidence rule applies here too: a withheld fact simply
    does not appear, it is never rendered hedged/"unconfirmed"."""
    verdict = verdicts.get(row["interval_id"])
    return verdict is None or verdict.get("verdict") != "unsupported"


def _build_attribute_fields(
    conn: sqlite3.Connection,
    entity_id: str,
    upto_vol: int,
    attributes_cfg: dict[str, Any],
    entity_first_vol: int | None,
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    verdicts = verdicts or {}
    current_by_pred: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in temporal.state_at(conn, entity_id, upto_vol):
        if row["object"] is not None:  # a relation where this entity is the subject; skip here
            continue
        if row["predicate"] in attributes_cfg and _is_renderable(row, verdicts):
            current_by_pred[row["predicate"]].append(row)

    history_by_pred: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in temporal.history_at(conn, entity_id, upto_vol):
        if row["object"] is not None:
            continue
        if row["predicate"] in attributes_cfg and _is_renderable(row, verdicts):
            history_by_pred[row["predicate"]].append(row)

    fields: dict[str, Any] = {}
    for predicate, cfg in attributes_cfg.items():
        current_rows = current_by_pred.get(predicate, [])
        history_rows = history_by_pred.get(predicate, [])
        key = predicate.lower()
        if not current_rows and not history_rows:
            # Phase 22 A5 (U6): `attributes.*.default` materializes a scalar field with no
            # backing evidence at all -- marked `inferred: true` so it never masquerades as a
            # claim (never touches claim_set_hash, never gets a claim_ids-based polarity/history).
            default = cfg.get("default")
            if default is not None and cfg.get("single"):
                fields[key] = {
                    "value": default, "since_vol": None, "show_vol": False,
                    "polarity": "asserted", "history": [], "inferred": True,
                }
            continue
        fields[key] = (
            _scalar_field(conn, current_rows, history_rows, upto_vol, entity_first_vol)
            if cfg.get("single")
            else _list_field(conn, current_rows, upto_vol, entity_first_vol)
        )
    return fields


def _relation_blurb(display: str, note: str | None, cfg: dict[str, Any]) -> str:
    """Phase 22 A5 (U4): the combined label+qualifier text a relationship card renders.
    `config/extraction.yaml` `relations.*.blurb` (optional) is a `.format(label=, note=)`
    template for a predicate that wants bespoke phrasing; every other predicate falls back to
    the generic "{label} — {note}" (or bare label with no qualifier)."""
    template = cfg.get("blurb")
    if template:
        return str(template).format(label=display, note=note or "")
    return f"{display} — {note}" if note else display


# Phase 26: qualifiers that describe being ACTED ON BY an organization rather than belonging to
# it. `config/extraction.yaml`'s AFFILIATED_WITH note already states the rule -- "Being held
# captive by, imprisoned by, indebted to, or opposed by an organization is NOT affiliation" --
# but nothing enforced it after extraction, and the live v1-2 graph produced four
# AFFILIATED_WITH edges for Holo, all non-membership: Medio Company "held captive", Milone
# Company "seeking sanctuary", Remelio Company "visitor", and The Church "member" (she is hunted
# by them). This is a render-layer guard over free text, not a fix; the real fix is extraction
# obeying its own instruction, which needs a re-run (Phase 26 part B).
_NON_MEMBERSHIP_ROLE_MARKERS = frozenset(
    """captive captured prisoner imprisoned hostage held abducted seized
    sanctuary refuge asylum fugitive hunted pursued wanted target victim
    visitor guest customer client patron debtor indebted debt owes
    opposed opponent enemy adversary rival threatened suspect suspected""".split()
)


# ENEMY_OF/RIVAL_OF -- the same pair site/bundle.py::_members_and_related already routes to a
# codex entry's `adversaries` bucket rather than its `members` bucket (Phase 22 A3).
_ADVERSARIAL_PREDICATES = frozenset({"ENEMY_OF", "RIVAL_OF"})


def _is_membership_role(qualifier: str | None) -> bool:
    """Does this AFFILIATED_WITH qualifier describe genuine membership?

    An absent qualifier is treated as membership -- that is the predicate's own default
    meaning, and the marker list only exists to catch text that contradicts it.
    """
    if not qualifier:
        return True
    words = {word.strip(".,;:'\"") for word in qualifier.lower().split()}
    return not (words & _NON_MEMBERSHIP_ROLE_MARKERS)


def _relationship_entries(
    conn: sqlite3.Connection, entity_id: str, upto_vol: int, relations_cfg: dict[str, Any], entity_first_vol: int | None,
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    verdicts = verdicts or {}
    affiliations: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    rows = [
        row
        for row in (
            *temporal.relations_at(conn, entity_id, upto_vol),
            *temporal.relation_history_at(conn, entity_id, upto_vol),
        )
        if _is_renderable(row, verdicts)
    ]
    # Phase 26: an organisation this character is in open conflict with is not one they belong
    # to, whatever the AFFILIATED_WITH qualifier happens to say. This is what catches the case
    # no keyword list can: Holo's strongest Church claim is `ENEMY_OF` at confidence 1.0 ("the
    # Church sought to end her life to preserve order"), yet a separate AFFILIATED_WITH carried
    # the qualifier "member" off a co-location reading ("shocked to see Holo walking out of the
    # worship hall with the members of the Church"), and that word alone headlined her page.
    adversaries = {
        (r["object"] if r["subject"] == entity_id else r["subject"])
        for r in rows
        if r["predicate"] in _ADVERSARIAL_PREDICATES
    }

    for row in rows:
        # A relation the text explicitly DENIES is not a relation. `_build_lead` and the
        # attribute fields have consulted `_claim_polarity` since Phase 22 A5 (U7); this loop
        # never did, so a `polarity: "denied"` claim rendered indistinguishably from an
        # asserted one -- Holo's page shipped "Saint Ruvinheigen — Killed" off
        # "I only bit him ... I did not kill him", and the same sink produced Phase 23's
        # false SPOUSE_OF. Dropping (rather than `polarity_prefixed`-ing) is the right call
        # here and only here: an attribute reads informatively as "not dead", but a
        # relationship list entry has no room for a negation -- absence IS the denial.
        polarity = _claim_polarity(conn, _decode_claim_ids(row), upto_vol)
        if polarity == "denied":
            continue
        predicate = row["predicate"]
        cfg = relations_cfg.get(predicate, {})
        other = row["object"] if row["subject"] == entity_id else row["subject"]

        if row["subject"] == entity_id or cfg.get("symmetric"):
            shown_predicate = predicate
            object_side_label = None
        else:
            shown_predicate = cfg.get("inverse", predicate)
            # Phase 26: a DIRECTIONAL predicate with no declared `inverse` (KILLED, SAVED)
            # falls back to itself above, so the object's own page rendered the subject's
            # wording -- Liebert's page shipped "Holo — Killed" beside "Status: alive".
            # `object_display` supplies the passive form without adding a second predicate
            # to the extractor's vocabulary (extract/claims.py::_vocab_lines enumerates
            # every relations key into the prompt).
            object_side_label = cfg.get("object_display") if shown_predicate == predicate else None
        shown_cfg = relations_cfg.get(shown_predicate, {})
        # "presumed" still renders, marked -- `config/extraction.yaml`'s polarity block says
        # collapsing it into a plain assertion "must never happen".
        display = polarity_prefixed(
            object_side_label or shown_cfg.get("display", shown_predicate), polarity
        )

        vol_end = row["vol_end"]
        closed = vol_end is not None and vol_end < upto_vol
        shown_vol_end = vol_end if closed else None  # scrub a not-yet-revealed close, same as fields

        if predicate == _AFFILIATION_PREDICATE:
            evidence = sorted(
                {e["para_id"] for e in temporal.evidence_at(conn, _decode_claim_ids(row), upto_vol)}
            )
            affiliations.append(
                {
                    "entity_id": other,
                    "role": row["qualifier"],
                    "vols": [row["vol_start"], shown_vol_end],
                    "current": not closed,
                    "membership": _is_membership_role(row["qualifier"])
                    and other not in adversaries,
                    "evidence": evidence,
                }
            )
        else:
            # Phase 22 A5 (U4): `label` is always the config display text now -- a free-text
            # qualifier no longer overrides it, only demotes to `note`/`blurb`. Also fixes the
            # sibling bug: a superseded relation (conflicts_with-closed, e.g. FRIEND_OF -> ENEMY_OF)
            # used to render with no indication it had ended; `current`/`vol_end` give it the
            # same closed/live signal affiliations already had.
            note = row["qualifier"] or None
            relationships.append(
                {
                    "entity_id": other,
                    "predicate": shown_predicate,
                    "label": display,
                    "note": note,
                    "blurb": _relation_blurb(display, note, shown_cfg),
                    "since_vol": row["vol_start"],
                    "show_vol": _show_vol(row["vol_start"], entity_first_vol),
                    "current": not closed,
                    "vol_end": shown_vol_end,
                    # Phase 26: the para_ids this relation rests on, so the renderer can cite
                    # it. `evidence_at` already filters each quote by its own para_id volume,
                    # so this cannot leak a later volume's citation onto an earlier cutoff.
                    "evidence": sorted(
                        {e["para_id"] for e in temporal.evidence_at(conn, _decode_claim_ids(row), upto_vol)}
                    ),
                }
            )

    affiliations.sort(key=lambda a: (a["vols"][0], a["entity_id"]))
    relationships.sort(key=lambda r: (r["since_vol"], r["entity_id"]))
    return affiliations, relationships


def _visible_interval_ids(conn: sqlite3.Connection, entity_id: str, upto_vol: int) -> set[str]:
    """The full visible-interval id set behind one entity's page at one cutoff — `state_at` +
    `history_at` + current/relation-history reads, deduped (a relation interval appears in both
    subject-side state/history and the symmetric relation reads). Shared by `_claim_set_hash`
    (CONTRACTS §5.1's cache key) and `has_min_evidence` (Phase 22 A5, S5)."""
    return {
        row["interval_id"]
        for row in (
            *temporal.state_at(conn, entity_id, upto_vol),
            *temporal.history_at(conn, entity_id, upto_vol),
            *temporal.relations_at(conn, entity_id, upto_vol),
            *temporal.relation_history_at(conn, entity_id, upto_vol),
        )
    }


def _claim_set_hash(conn: sqlite3.Connection, entity_id: str, upto_vol: int) -> str:
    """CONTRACTS §5.1: sha256 of the sorted visible interval id set — the cache key that
    collapses repeat cutoffs to one generation."""
    ids = _visible_interval_ids(conn, entity_id, upto_vol)
    return hashlib.sha256("|".join(sorted(ids)).encode("utf-8")).hexdigest()


def has_min_evidence(conn: sqlite3.Connection, entity_id: str, upto_vol: int, min_claims: int) -> bool:
    """Phase 22 A5 (S5): does this entity have at least `min_claims` distinct real (non-U6-
    inferred) intervals visible at this cutoff? `config/extraction.yaml` `page_gate.min_claims`
    is the configured floor; `wiki synthesize` calls this BEFORE `assemble_page` and skips
    writing a page entirely when it is `False` — a crash-truncated run left 5 of 14 characters
    with zero real evidence (the audit's finding), and a page with nothing on it is worse than no
    page. Reuses `_claim_set_hash`'s own query (`_visible_interval_ids`) rather than counting
    `page["fields"]`, since U6's `default:`-materialized fields would otherwise make every
    character look like it has evidence."""
    return len(_visible_interval_ids(conn, entity_id, upto_vol)) >= min_claims


def find_name_collisions(all_entities: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Phase 22 A5 (S5): entities of DIFFERENT types sharing one canonical name — not an
    `entity_id` collision (`entities/alias.py` already guarantees distinct ids via a numeric
    suffix), but a NAME collision, silently ambiguous everywhere a bare display name is shown
    (roster, search, pills, links) — the audit's real example: Saint Ruvinheigen is both a
    character and the city he is named for. Not cutoff-scoped (a gazetteer-wide, one-time check,
    not a per-page one); `wiki synthesize`/`wiki site build` print a warning listing what this
    returns. Detection only — disambiguating every display site is future work.

    Returns `(canonical, sorted [entity_id, ...])` pairs, sorted by canonical name.
    """
    ids_by_name: dict[str, set[str]] = defaultdict(set)
    types_by_name: dict[str, set[str]] = defaultdict(set)
    for e in all_entities:
        ids_by_name[e["canonical"]].add(e["entity_id"])
        types_by_name[e["canonical"]].add(e["type"])
    return sorted(
        (name, sorted(ids))
        for name, ids in ids_by_name.items()
        if len(ids) > 1 and len(types_by_name[name]) > 1
    )


def _annotated_mentions(
    conn: sqlite3.Connection,
    events_conn: sqlite3.Connection | None,
    entity_id: str,
    upto_vol: int,
    related: list[dict[str, Any]],
    already_shown: set[str],
    relations_cfg: dict[str, Any],
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Phase 22 A5 (U5): `related` (PPR's ranked, seed-excluded neighbours) filtered against
    what `fields.affiliations`/`fields.relationships` already show — those already explain
    themselves, so repeating them here (75% of the audited page's chips) is pure duplication —
    then annotated with WHY the remaining ones are connected: a relation `relations_between`
    finds that for some reason isn't already surfaced (defensive; every relation predicate is
    rendered into affiliations/relationships today, so this should rarely fire), or failing
    that a `shared_events_at` scene count. `note` stays `None` for a genuine multi-hop-only PPR
    result — nothing here can explain a two-edge connection with one line.

    Phase 30: that "defensive, should rarely fire" relation branch fires in exactly one case, and
    it is the one case where it must not. A relationship `wiki verify` withheld is absent from
    `fields.relationships`, so the other entity is absent from `already_shown` too, so this
    function reached the relation branch and annotated it with the rejected predicate's display
    text. Saint Metrogius's page model carried `{"entity_id": "kraft-lawrence", "note": "Mentor
    of"}` — the verifier's own rationale for withholding that interval is that the evidence shows
    Lawrence follows Metrogius's path, not that Metrogius taught him. `verdicts` closes it, the
    same gate `_build_attribute_fields` and `_relationship_entries` already apply."""
    from ..graph.events import shared_events_at

    mentions: list[dict[str, Any]] = []
    for r in related:
        other = r["entity_id"]
        if other in already_shown:
            continue
        note = None
        rel_rows = [
            r for r in temporal.relations_between(conn, entity_id, other, upto_vol)
            if _is_renderable(r, verdicts or {})
            and _claim_polarity(conn, _decode_claim_ids(r), upto_vol) != "denied"
        ]
        if rel_rows:
            row = rel_rows[0]
            predicate = row["predicate"]
            cfg = relations_cfg.get(predicate, {})
            shown_predicate = predicate if row["subject"] == entity_id or cfg.get("symmetric") else cfg.get("inverse", predicate)
            note = relations_cfg.get(shown_predicate, {}).get("display", shown_predicate)
        elif events_conn is not None:
            n = len(shared_events_at(events_conn, entity_id, other, upto_vol))
            if n:
                note = f"{n} shared scene" if n == 1 else f"{n} shared scenes"
        mentions.append({"entity_id": other, "note": note})
    return mentions


def _build_lead(
    fields: dict[str, Any], entities_by_id: dict[str, dict[str, Any]], status_default: str | None = None,
) -> str | None:
    """Phase 22 A5 (S6): a compact "status · primary affiliation" summary string for the new
    `lead` page_outline section — the two facts a reader scanning the page wants first, and
    exactly the two U6/U7 made newly reliable (a default STATUS, a polarity-aware value). Built
    only from `fields` already assembled above (no new evidence read); `entities_by_id` supplies
    the affiliation's canonical name (a bare `entity_id` would defeat the point of a lead line).
    `None` when neither is known yet, so the section renders nothing rather than an empty strip.
    """
    parts: list[str] = []
    status = fields.get("status")
    # An `inferred` value is a config default with zero evidence behind it (U6). The Overview
    # line can carry it because it renders the "_(assumed)_" marker beside it; the lead is a bare
    # scan line with nowhere to put that marker, so it stated as fact what nothing in the text
    # supports -- 8 of 14 v1-2 pages led with "alive", including both saints, one of whom Holo
    # bit centuries before the story starts. Same call this function already makes for a
    # mis-picked affiliation: no lead line beats a wrong one. Not specific to STATUS or to any
    # series -- any attribute carrying `attributes.*.default` is skipped here, and a series that
    # wants a different default (or none) sets it in its own extraction overlay.
    # [30] Nor the default itself: a lead line reading "alive" tells a reader nothing, and it
    # appeared only on pages whose STATUS happened to be claimed (Lutz, not Myne) -- a status
    # earns the headline when it is news (deceased, missing), which is what a lead is for.
    if (status and status.get("value") and not status.get("inferred")
            and str(status["value"]).strip().lower() != str(status_default or "").strip().lower()):
        parts.append(polarity_prefixed(status["value"], status.get("polarity")))
    # Phase 26: only a genuine membership may headline the page, and among those the earliest
    # current one. Before this, the pick was "first entry in a list sorted by (vol_start,
    # entity_id)" -- i.e. alphabetical within the earliest volume -- so Holo's page led with
    # "alive · Medio Company", the company that captured her, purely because `medio-` sorts
    # before `milone-`. A lead line naming the wrong organisation is worse than no lead line,
    # so when nothing qualifies the status stands alone.
    affiliations = [a for a in (fields.get("affiliations") or []) if a.get("membership", True)]
    primary = next(
        (a for a in affiliations if a.get("current")), affiliations[0] if affiliations else None
    )
    if primary:
        parts.append(entities_by_id.get(primary["entity_id"], {}).get("canonical", primary["entity_id"]))
    return " · ".join(parts) if parts else None


def assemble_page(
    conn: sqlite3.Connection,
    events_conn: sqlite3.Connection | None,
    entity: dict[str, Any],
    upto_vol: int,
    settings: Any,
    entities_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The CONTRACTS §5 page model for one character at one volume cutoff. `entity` is this
    subject's gazetteer entry (needs `entity_id`/`canonical`; `first_vol`, if present, drives
    U3's `show_vol`). `events_conn` (Phase 22 A5, U5 — `None` when events.db does not exist yet)
    feeds `mentions_of`'s shared-scene annotation; `entities_by_id` (Phase 22 A5, S6) feeds the
    new `lead` field's affiliation name.

    Phase 20: the returned dict includes a `\"quotes\"` key (initialised to `None`) when the
    `page_outline` contains a `kind: quotes` section. `build_quotes_section()` fills it in
    at synthesize time — it is deterministic and requires events.db."""
    entity_id = entity["entity_id"]
    entity_first_vol = entity.get("first_vol")
    verdicts = store.read_verdicts(conn)  # Phase 23 B2 -- {} when wiki verify has not run
    # Phase 23 E2: an attribute predicate claimed by a `source: "attributes"` prose section (the
    # `appearance` section's APPEARANCE) is excluded from the generic infobox fields loop below --
    # it now renders as prose instead, and would otherwise double-render as both a bullet and a
    # paragraph. Keyed off `settings.page_outline` so this stays generic over whatever a per-series
    # overlay declares, matching every other rule in this module.
    prose_attribute_predicates = frozenset(
        p
        for s in settings.page_outline
        if s["kind"] == "prose" and s.get("source") == "attributes"
        for p in s.get("traits", ())
    )
    attributes_cfg = {
        k: v for k, v in settings.attributes.items() if k not in prose_attribute_predicates
    }
    fields = _build_attribute_fields(
        conn, entity_id, upto_vol, attributes_cfg, entity_first_vol, verdicts
    )
    affiliations, relationships = _relationship_entries(
        conn, entity_id, upto_vol, settings.relations, entity_first_vol, verdicts
    )
    # Phase 26: a non-membership AFFILIATED_WITH edge leaves `fields["affiliations"]` entirely
    # rather than staying in it behind a flag. Rendering it under a different heading was not
    # enough: `eval/gold.py::_relationship_entry_keywords` adds {affiliated, affiliation,
    # member, membership} to every affiliation entry unconditionally, so `wiki audit eval` kept
    # reporting the gold negative fact "Holo is not affiliated with the Medio Company" -- and it
    # was right to, because the fact was still filed as an affiliation. `categories` (site/okf.py
    # front matter) is derived from this list too, so Holo was also categorised under her captor.
    memberships = [a for a in affiliations if a.get("membership", True)]
    other_ties = [a for a in affiliations if not a.get("membership", True)]
    if memberships:
        fields["affiliations"] = memberships
    if other_ties:
        fields["other_ties"] = other_ties
    if relationships:
        fields["relationships"] = relationships
    _dedupe_name_fields(fields, entity, attributes_cfg)

    related = ppr.personalized_pagerank(conn, entity_id, upto_vol)
    already_shown = {entity_id} | {a["entity_id"] for a in affiliations} | {r["entity_id"] for r in relationships}
    mentions = _annotated_mentions(
        conn, events_conn, entity_id, upto_vol, related, already_shown, settings.relations,
        verdicts,
    )

    # Phase 20: include quotes placeholder if the page_outline has a kind: quotes section.
    has_quotes_section = any(s["kind"] == "quotes" for s in settings.page_outline)

    page: dict[str, Any] = {
        "entity_id": entity_id,
        "canonical": entity["canonical"],
        "upto_vol": upto_vol,
        "claim_set_hash": _claim_set_hash(conn, entity_id, upto_vol),
        # Phase 23 E5: the gazetteer already carries aliases (site/okf.py's front matter has read
        # them from `entity` since Phase 6) -- this was simply dropped from the JSON page model
        # itself, so app.js (which reads `page`, not `entity`) never had them. `entity.get(
        # "aliases", [])` mirrors okf.py's own front-matter line exactly.
        "aliases": aliases_at(entity, upto_vol),  # [31] each alias's own first_vol, not the entity's
        "fields": fields,
        "lead": _build_lead(fields, entities_by_id, (settings.attributes.get("STATUS") or {}).get("default")),
        "prose": {s["key"]: None for s in settings.page_outline if s["kind"] == "prose"},
        "traits": _build_trait_sections(
            conn, entity_id, upto_vol, settings, entity_first_vol, verdicts
        ),
        "mentions_of": mentions,
        "generated_by": None,
    }
    if has_quotes_section:
        page["quotes"] = None
    return page


_FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|mine|me)\b", re.IGNORECASE)


def _quote_notability(candidate: dict[str, Any]) -> float:
    """A 0..1-ish score for how well a line of dialogue would serve as a pull quote.

    Phase 26. Deliberately a small, explainable heuristic rather than an LLM call -- the
    quotes section is a deterministic export (CLAUDE.md §2: only prose costs output tokens),
    and the ranking only has to separate substantive lines from conversational filler.

    The four signals, in weight order:
      * `length`   -- a line long enough to stand alone, saturating at 25 words so a speech
                      does not automatically beat an epigram. Median quote length in the live
                      corpus is 42 chars and 724 of 1,968 are under 30, so this does most of
                      the work.
      * `complete` -- ends on sentence punctuation. `"Listen, you"` and `"Am I wrong, then?"`
                      are both short; only one is a finished thought.
      * `self_ref` -- first person. A character asserting something about themselves is what
                      a wiki quote block is for ("I am Holo the Wisewolf"), as against a line
                      managing the immediate exchange.
      * `scene`    -- how many characters were present, as a proxy for the line mattering to
                      more than one person.
    """
    text = (candidate.get("quote") or "").strip()
    if not text:
        return 0.0
    words = len(text.split())
    length = min(words, 25) / 25
    complete = 1.0 if text[-1:] in ".!?" else 0.35
    self_ref = 1.0 if _FIRST_PERSON.search(text) else 0.0
    scene = min(candidate.get("participants") or 0, 4) / 4
    return 0.45 * length + 0.25 * complete + 0.20 * self_ref + 0.10 * scene


def build_quotes_section(
    events_conn: sqlite3.Connection,
    entity_id: str,
    upto_vol: int,
    section: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Select top-N verbatim quotes attributed to `entity_id` from events.db.

    Phase 20: deterministic, no LLM. Reads `character_events_at` (spoiler-fenced to `upto_vol`)
    then collects all `kind=\"quote\"` event_claims where `speaker == entity_id`. Sorts by
    confidence descending, deduplicates by the first 60 characters of the quote text, and takes
    the top `section.get(\"max_quotes\", 3)`. Returns None if events_conn is None or if no
    qualifying quotes are found.

    Spoiler safety: `character_events_at` already filters `vol_start <= upto_vol`, and
    `event_claims_at` additionally filters `vol <= upto_vol` — so no future quote can reach this.
    The `speaker` field carries the surface form from the scene record, which may be a surface
    form rather than an entity_id. We match against `entity_id` as well as the entity's
    `canonical` name — the caller should pass the canonical name via `section[\"_entity_canonical\"]`
    if a surface-match is desired; by default only `speaker == entity_id` is collected.
    """
    from ..graph.events import character_events_at, event_claims_at

    max_n = int(section.get("max_quotes", 3))
    rows = list(character_events_at(events_conn, entity_id, upto_vol))
    entity_hint = section.get("_entity_canonical", "")
    if entity_hint and entity_hint != entity_id:
        seen_event_ids = {r["event_id"] for r in rows}
        rows.extend(
            r
            for r in character_events_at(events_conn, entity_hint, upto_vol)
            if r["event_id"] not in seen_event_ids
        )
    if not rows:
        return None

    candidates: list[dict[str, Any]] = []
    seen_prefixes: set[str] = set()

    for event_row in rows:
        for claim in event_claims_at(events_conn, event_row["event_id"], upto_vol):
            if claim["kind"] != "quote":
                continue
            # Accept quotes where the speaker matches the entity_id or the section's hint.
            speaker = claim["speaker"] or ""
            entity_hint = section.get("_entity_canonical", "")
            if speaker != entity_id and speaker != entity_hint:
                continue
            quote_text = claim["quote"] or ""
            if not quote_text:
                continue
            prefix = quote_text[:60]
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
            candidates.append({
                "quote": quote_text,
                "speaker": speaker,
                "para_id": claim["para_id"],
                "vol": claim["vol"],
                "participants": len(json.loads(event_row["participants_json"] or "[]")),
                "confidence": claim["confidence"],
            })

    if not candidates:
        return None

    # Phase 26: rank by notability, not by extraction confidence. `confidence` measures how
    # sure the extractor was that this IS a quote by this speaker -- it says nothing about
    # whether the line is worth putting on a wiki page, and across the live v1-2 corpus it
    # spans only 0.89-0.98 over 1,968 quotes, so sorting by it left the top-3 as effectively
    # arbitrary DB order. Holo's page shipped `"Listen, you"` ahead of 765 alternatives.
    # Kept as the last tiebreaker only.
    candidates.sort(key=lambda c: (-_quote_notability(c), -c["confidence"], c["para_id"]))
    top = candidates[:max_n]
    # Drop internal ranking keys — not part of the public page schema.
    return [{"quote": c["quote"], "speaker": c["speaker"], "para_id": c["para_id"], "vol": c["vol"]} for c in top]


def fact_set_for_verification(
    conn: sqlite3.Connection,
    entity_id: str,
    upto_vol: int,
    entities_by_id: dict[str, dict[str, Any]],
    max_evidence: int = 20,
) -> list[dict[str, Any]]:
    """Phase 22 C3: the currently-visible fact set behind one character's page, each fact paired
    with its top cited evidence quotes -- input to `graph/verify.py`'s per-character verification
    call. NOT part of `assemble_page()`'s public JSON (see module docstring, same reasoning as
    `trait_values_at()`): a verification prompt needs the `claim_ids`-derived quotes
    `assemble_page()` deliberately discards, via `temporal.evidence_at()`, the same path `wiki
    explain`/`wiki trace` already use.

    `max_evidence` (Phase 23 B4, raised from 2): a verifier judging "does this evidence support
    the fact" needs to see everything the fact rests on, not just its first two quotes -- a fact
    resting on ten corroborating windows and one that rests on a single misread line should not
    look identical to the model doing the checking. This is ONE call per character (unlike the
    bulk extraction passes), so the token cost of a generous cap is a rounding error by
    comparison; 20 is a safety ceiling against a pathological case, not a real-world limit for
    this corpus (`extraction.yaml`'s own `max_quotes: 3` caps how many of a fact's quotes ever
    reach a rendered page, but verification checks the fact against ALL its evidence, not just
    what gets shown).

    Only CURRENT state (`state_at` + `relations_at`), not `history_at` -- a superseded value is
    not what the live page asserts today, so it is out of scope for this check. Attribute/trait
    rows come from `state_at` (skipping the relation-shaped rows it also returns, same filter
    `_build_attribute_fields` applies); relation rows come from `relations_at` and keep the
    interval's OWN stored subject/object (not flipped to this entity's perspective the way
    `_relationship_entries` renders it) since the cited evidence quote supports exactly that
    stored direction. Deduped by `interval_id` the same way `_visible_interval_ids` is, since a
    relation interval can appear in both queries.
    """
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _evidence(row: sqlite3.Row) -> list[dict[str, str]]:
        return [
            {"quote": e["quote"], "para_id": e["para_id"]}
            for e in temporal.evidence_at(conn, _decode_claim_ids(row), upto_vol)[:max_evidence]
        ]

    for row in temporal.state_at(conn, entity_id, upto_vol):
        if row["object"] is not None or row["interval_id"] in seen:
            continue
        seen.add(row["interval_id"])
        facts.append(
            {
                "kind": "attribute",
                "predicate": row["predicate"],
                "value": row["value"],
                "qualifier": row["qualifier"],
                "evidence": _evidence(row),
                "interval_id": row["interval_id"],  # Phase 23 B5 -- verify writes its verdict here
            }
        )

    for row in temporal.relations_at(conn, entity_id, upto_vol):
        if row["interval_id"] in seen:
            continue
        seen.add(row["interval_id"])
        subj = entities_by_id.get(row["subject"], {}).get("canonical", row["subject"])
        obj = entities_by_id.get(row["object"], {}).get("canonical", row["object"])
        facts.append(
            {
                "kind": "relation",
                "predicate": row["predicate"],
                "value": f"{subj} -> {obj}",
                "qualifier": row["qualifier"],
                "evidence": _evidence(row),
                "interval_id": row["interval_id"],  # Phase 23 B5
                "pair": sorted((row["subject"], row["object"])),  # [30] verify's sibling context
            }
        )

    return facts


def _dedupe_name_fields(
    fields: dict[str, Any], entity: dict[str, Any], attributes_cfg: dict[str, Any]
) -> None:
    """Phase 30: a name-like value renders once, and never as an alias of the page's own subject.

    Holo's page carried "**Nicknames:** Holo" -- the character's own name, offered as one of her
    nicknames, directly under the `# Holo` heading -- and listed "Wisewolf of Yoitsu" under both
    Nicknames and Titles. Both are artefacts of two naming systems meeting: the gazetteer's
    surface forms and the graph's own NICKNAME/TITLE claims. 5 of 14 v1-2 pages showed one.

    Two rules, both narrow enough to lose nothing:
      1. The canonical name never appears in a name-like field. It is the page's `# heading`;
         repeating it as an alias of itself is noise in any reference work.
      2. A value carried by a higher-precedence name-like attribute is not repeated by a lower
         one. Precedence is `config/extraction.yaml`'s own declaration order among attributes
         marked `name_like`, so a series overlay reorders it by reordering the file.

    Deliberately does NOT drop values that merely also appear in the gazetteer `aliases`/
    `epithets` front matter: that front matter is YAML metadata, so a name-like field is the only
    place a READER sees those strings. Removing them would delete visible information to tidy
    something invisible.
    """
    def norm(text: Any) -> str:
        cleaned = re.sub(r"^(the|a|an)\s+", "", str(text or "").strip().lower())
        return re.sub(r"[^a-z0-9 ]+", "", cleaned).strip()

    ordered = [p for p, cfg in attributes_cfg.items() if cfg.get("name_like")]
    if not ordered:
        return
    seen = {norm(entity.get("canonical"))} - {""}
    for predicate in ordered:
        key = predicate.lower()
        value = fields.get(key)
        if not value:
            continue
        if isinstance(value, list):
            kept = [row for row in value if norm(row.get("value")) not in seen]
            seen.update(norm(row.get("value")) for row in kept)
            if kept:
                fields[key] = kept
            else:
                fields.pop(key)
        elif norm(value.get("value")) in seen:
            fields.pop(key)
        else:
            seen.add(norm(value.get("value")))


def _build_trait_sections(
    conn: sqlite3.Connection, entity_id: str, upto_vol: int, settings: Any,
    entity_first_vol: int | None, verdicts: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Phase 30: trait predicates rendered as structured rows instead of dissolved into prose.

    `traits:` in config/extraction.yaml had exactly one consumer -- `synth/prose.py`'s prompt --
    so a SKILL/MOTIVATION/FEAR interval reached the reader only as whatever survived a capped
    paragraph. Holo's page carried 3 of her 14 SKILL rows, and across the v1-2 roster 43% of
    SKILL intervals left no trace in the prose at all: extracted, evidence-cited, volume-scoped
    and verified, then dropped at the last step. CLAUDE.md section 2 already says structured
    fields render deterministically from graph rows and cost no tokens; traits simply had no
    renderer. A `kind: traits` section gives them one.

    Which predicates get a structured home is a `page_outline` decision, so a series overlay
    settles it per series -- this module hard-codes no predicate. Applies `_is_renderable` too,
    closing a real gap: `wiki verify`'s withholding reached fields and relationships but never
    traits, so an interval it had rejected could still shape the prose.
    """
    sections = {s["key"]: s for s in settings.page_outline if s["kind"] == "traits"}
    if not sections:
        return {}
    owner = {p: key for key, s in sections.items() for p in s.get("traits", ())}
    excluded = {key: re.compile(s["exclude_pattern"], re.I)
                for key, s in sections.items() if s.get("exclude_pattern")}
    out: dict[str, list[dict[str, Any]]] = {key: [] for key in sections}
    for row in temporal.state_at(conn, entity_id, upto_vol):
        if row["object"] is not None or row["predicate"] not in owner:
            continue
        if not _is_renderable(row, verdicts):
            continue
        if (rx := excluded.get(owner[row["predicate"]])) and rx.search(row["value"] or ""):
            continue
        claim_ids = _decode_claim_ids(row)
        polarity = _claim_polarity(conn, claim_ids, upto_vol)
        # Same call the relationship loop makes: a bullet has no room for a negation, so a
        # denied trait is absent rather than rendered as "not X".
        if polarity == "denied":
            continue
        out[owner[row["predicate"]]].append({
            "value": row["value"],
            "predicate": row["predicate"],
            "polarity": polarity,
            "since_vol": row["vol_start"],
            "show_vol": _show_vol(row["vol_start"], entity_first_vol),
            "evidence": sorted(
                {e["para_id"] for e in temporal.evidence_at(conn, claim_ids, upto_vol)}
            ),
        })
    for key, rows in out.items():
        rows.sort(key=lambda r: (r["since_vol"], (r["value"] or "").lower()))
        out[key] = _fold_contained_rows(rows)
    return out


_FUNCTION_WORDS = frozenset("a an the to of for and or his her their its in on at with by".split())


def _fold_contained_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[30] Drop a trait row whose content words all appear in a longer sibling, moving its
    citations to that sibling. "own a shop" says nothing "to open his own shop" does not, and two
    bullets for one goal read as two goals. Function words are ignored ("a" is in only one of
    those), which `graph/canonicalize.py::subsume_values` does not do; that one also drops every
    single-word value, right for APPEARANCE ("hair") and wrong here ("revenge")."""
    def words(row: dict[str, Any]) -> frozenset[str]:
        return frozenset(re.findall(r"[a-z']+", (row["value"] or "").lower())) - _FUNCTION_WORDS

    kept = []
    for row in rows:
        mine = words(row)
        # The LARGEST superset is never itself folded, so citations are not lost along a chain.
        host = max((o for o in rows if o is not row and mine and mine < words(o)),
                   key=lambda o: len(words(o)), default=None)
        if host is None:
            kept.append(row)
        else:
            host["evidence"] = sorted(set(host["evidence"]) | set(row["evidence"]))
    return kept


def visible_state_at(conn: sqlite3.Connection, entity_id: str, upto_vol: int) -> list[sqlite3.Row]:
    """`temporal.state_at` minus the intervals `wiki verify` withheld -- what a prose prompt may
    see. [30] Fields, relationships and trait lists already applied `_is_renderable`; the prose
    prompts read `state_at` directly and did not, so a rejected fact still shaped published text:
    Norah's History called her "an orphan" from `BACKGROUND 'orphaned with no relatives'`, which
    verification had withheld as the narrator's inference."""
    verdicts = store.read_verdicts(conn)
    return [row for row in temporal.state_at(conn, entity_id, upto_vol) if _is_renderable(row, verdicts)]


def trait_values_at(
    conn: sqlite3.Connection, entity_id: str, upto_vol: int, traits_cfg: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Volume-filtered trait intervals grouped by predicate, for Phase 6's `synth/prose.py`
    prompt — NOT part of `assemble_page()`'s public JSON (see module docstring). Each entry keeps
    `claim_ids` so the prose stage can cite `para_id`/`quote` from `claims`, the same evidence
    path `wiki explain` uses."""
    by_pred: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in visible_state_at(conn, entity_id, upto_vol):
        if row["object"] is not None:
            continue
        if row["predicate"] in traits_cfg:
            by_pred[row["predicate"]].append(
                {"value": row["value"], "since_vol": row["vol_start"], "claim_ids": _decode_claim_ids(row)}
            )
    return dict(by_pred)
