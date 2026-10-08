"""[4, 17] Same-volume arbitration vs cross-volume supersession (CONTRACTS §4.2), plus (Phase
17) value canonicalization and relation-conflict arbitration.

Inputs:     Every claim across all present `data/03_claims/v{NN}.jsonl` files, config/
            extraction.yaml's `attributes`/`relations`/`canonicalize` taxonomy and `conflicts:`
            block, and an LLMClient (called for the `arbitrate` role — PROMPTS.md
            `arbitrate_conflict` — only when a same-volume tie's two confidences are within
            `conflicts.escalate_when_confidence_within`; called for `canonicalize` — bge-m3 via
            `LLMClient.embed` — once per multi-valued attribute/trait `(subject, predicate)`
            group with 2+ distinct values).
Outputs:    `build_graph()` returns `(intervals, edges, contradictions_doc)`. `graph/store.py`
            writes the first two into `graph.db`; `contradictions_doc` is written verbatim to
            `data/04_graph/contradictions.json` (CONTRACTS §4.2), now also carrying a
            `"canonicalization"` key (Phase 17).
Invariants: - Same volume + differing value = extraction_error: arbitrated, never silently
            dropped (CLAUDE.md §2). Different volumes + differing value = narrative_change:
            supersede, logged the same way regardless of predicate.
            - Only `single: true` attributes go through tie-resolution and supersession by
            default. Non-single attributes and traits are multi-valued by design — two different
            nicknames are not a conflict — but (Phase 17) their VALUES are first run through
            `graph/canonicalize.py` so near-duplicate phrasings collapse into one value, then
            (Phase 22 A2) through `canonicalize.subsume_values()` when the predicate declares
            `subsume: true` (APPEARANCE), before reaching graph/temporal.py::assign_multi_valued —
            which itself now buckets by `normalize_value` rather than exact string match, so a
            same-fact restatement that differs only by case/whitespace still merges even if
            `canonicalize` never clustered it (e.g. no embed model reachable). `single: true`
            attributes get the same phrasing tolerance one layer up, in `_resolve_single_valued`
            and `assign_single_valued`, both driven by the predicate's `attributes.*.format`
            (Phase 22 A2 — AGE's `format: number_or_range` is the case that motivated this:
            `'25'`/`'twenty-five'`/`'twenty-five years old'` all normalize to `'25'`). Relations
            are multi-valued too, UNLESS the predicate is declared
            `conflicts_with` another predicate (Phase 17) and both actually appear on the same
            (subject, object) pair — then that pair's claims across the whole conflict group go
            through `_resolve_relation_group`, the relation analogue of `_resolve_single_valued`.
            - Symmetric and inverse relation pairs (SIBLING_OF, PARENT_OF/CHILD_OF, ...) are
            canonicalised to one storage direction here, per config/extraction.yaml
            `relations.*.symmetric`/`.inverse` — closes the gap Phase 3's handover left open:
            extraction asks "what do we learn about X" and "what do we learn about Y"
            independently, so the same fact can arrive as two claims in opposite directions.
            This only merges a predicate with its DECLARED `inverse` counterpart (PARENT_OF(A,B)
            + CHILD_OF(B,A) -> one interval); it does NOT catch the SAME predicate extracted in
            both directions (CHILD_OF(A,B) and CHILD_OF(B,A) both asserted) — a real bug found in
            the Phase 23 audit (Kraft Lawrence rendered simultaneously as Jakob's child AND
            parent, from one line of dialogue where Lawrence adopts a false identity).
            `_strip_mutual_direction_collisions` (Phase 23 A4) catches that shape specifically,
            confidence-ranking the two directions and logging the loser as an `extraction_error`,
            before canonicalization ever runs — a directional (non-`symmetric`) predicate can
            never legitimately hold in both directions between the same pair at once.
            - A `flag` resolution still needs a provisional value so the interval sequence has no
            gap; the higher-confidence candidate is used provisionally, but the conflict is still
            recorded with `resolution: "flag"` so `wiki audit contradictions` surfaces it for a
            human — "never silently pick a winner" (CLAUDE.md §2) means never hiding the conflict,
            not that the pipeline halts.
Contract:   docs/CONTRACTS.md §4.2; docs/PROMPTS.md `arbitrate_conflict`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from . import canonicalize, temporal

STAGE = "arbitrate"

Resolution = Literal["keep_a", "keep_b", "keep_both", "flag"]


def _ranking_score(claim: dict[str, Any]) -> float:
    return claim.get("_support_score", claim["confidence"])


def _rank_key(claim: dict[str, Any]) -> tuple[float, str]:
    # Classifier ties are deterministic without reverting to self-reported confidence.
    return (-_ranking_score(claim), claim["claim_id"] if "_support_score" in claim else "")


def _direction_strength(claims: list[dict[str, Any]]) -> tuple[float, int, str]:
    """Rank one direction of a mutual-direction collision, whole side against whole side.

    Confidence alone ties routinely — both directions of `SAVED` between Holo and Lawrence were
    extracted at 1.0 from the same sentence — and `_rank_key` breaks a tie on `claim_id` only when
    an NLI support score is present, leaving dict order to decide otherwise. That picks the wrong
    direction about half the time, which is worse than not checking at all. Distinct evidence count
    breaks it: the direction the text states more often is the one to keep. `claim_id` is the final
    tiebreak so the outcome never depends on iteration order."""
    best = min(claims, key=_rank_key)
    evidence = {
        (e["para_id"], e["quote"]) for c in claims for e in (c.get("evidence") or [])
    }
    return (-_ranking_score(best), -len(evidence), min(c["claim_id"] for c in claims))


class ArbitrationDecision(BaseModel):
    resolution: Resolution
    rationale: str = Field(max_length=400)

    @model_validator(mode="before")
    @classmethod
    def _alias_decision_key(cls, data: Any) -> Any:
        # openrouter/free's nex-n2.5-pro repeatedly returns "decision" instead of the
        # requested "resolution" key, and its repair-retry doesn't always self-correct
        # (observed: 20260919T190103-graph_build-v1-2, jakob/RANK, all 3 attempts).
        if isinstance(data, dict) and "resolution" not in data and "decision" in data:
            data = {**data, "resolution": data["decision"]}
        return data


class JSONClient(Protocol):
    def complete_json(
        self, stage: str, prompt: str, schema: type[BaseModel], system: str | None = None
    ) -> BaseModel: ...

    def embed(self, stage: str, texts: list[str]) -> list[list[float]]: ...


_SYSTEM_PROMPT = """You are arbitrating a contradiction found in facts extracted about a character \
from a novel. Two facts were extracted from the SAME volume and disagree, which means one is \
an extraction error -- the source text does not actually contradict itself within one volume. You \
will be shown both facts and the exact passage each was drawn from. Decide which is correct, or \
whether both are legitimately true at once (e.g. two compatible phrasings of the same fact), or \
whether neither passage clearly supports a value (flag for human review). Base your decision only \
on the quoted evidence, never on outside knowledge of the series."""


def _user_prompt(subject: str, predicate: str, vol: int, a: dict[str, Any], b: dict[str, Any]) -> str:
    def _fmt(entry: dict[str, Any]) -> str:
        quotes = "; ".join(f'"{ev["quote"]}"' for ev in entry["evidence"][:3])
        return f'value="{entry["value"]}", confidence={entry["confidence"]:.2f}, evidence: {quotes}'

    return (
        f"Character: {subject}\nAttribute: {predicate}\nVolume: {vol}\n\n"
        f"Fact A: {_fmt(a)}\nFact B: {_fmt(b)}\n\n"
        "Which fact is correct? resolution must be one of keep_a, keep_b, keep_both, flag, plus a "
        "one-sentence rationale citing the evidence."
    )


def _resolve_pair(
    subject: str,
    predicate: str,
    vol: int,
    a: dict[str, Any],
    b: dict[str, Any],
    threshold: float,
    client: JSONClient | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str, str, str]:
    """Returns (winner, loser, resolution, arbiter, rationale). winner/loser are None for
    keep_both/flag, where neither claim is discarded outright."""
    if "_support_score" in a or "_support_score" in b:
        if "_support_score" not in a or "_support_score" not in b:
            raise ValueError("Cannot arbitrate with partial support scores")
        gap = abs(a["_support_score"] - b["_support_score"])
        winner, loser = (a, b) if _rank_key(a) <= _rank_key(b) else (b, a)
        support_threshold = winner.get("_support_threshold", 0.5)
        if winner["_support_score"] <= support_threshold or gap < threshold:
            return None, None, "flag", "nli:ambiguous", (
                f"Support scores {a['_support_score']:.6f}/{b['_support_score']:.6f}; "
                f"no supported winner separated by {threshold:g}. Highest score is provisional."
            )
        return winner, loser, "keep_a" if winner is a else "keep_b", "nli:higher_support", (
            f"Evidence support {winner['_support_score']:.6f} vs {loser['_support_score']:.6f}; "
            "extractor confidence was not used."
        )
    gap = abs(a["confidence"] - b["confidence"])
    if gap >= threshold:
        winner, loser = (a, b) if a["confidence"] > b["confidence"] else (b, a)
        resolution = "keep_a" if winner is a else "keep_b"
        return (
            winner,
            loser,
            resolution,
            "rule:higher_confidence",
            f"Confidence gap {gap:.2f} >= threshold {threshold:.2f}; kept the higher-confidence value.",
        )

    if client is None:
        winner, loser = (a, b) if a["confidence"] >= b["confidence"] else (b, a)
        resolution = "keep_a" if winner is a else "keep_b"
        return (
            winner,
            loser,
            resolution,
            "rule:higher_confidence_fallback",
            "No arbitrate model reachable; fell back to the higher-confidence value.",
        )

    decision = client.complete_json(
        STAGE, _user_prompt(subject, predicate, vol, a, b), ArbitrationDecision, system=_SYSTEM_PROMPT
    )
    if decision.resolution == "keep_a":
        return a, b, "keep_a", "model:arbitrate", decision.rationale
    if decision.resolution == "keep_b":
        return b, a, "keep_b", "model:arbitrate", decision.rationale
    if decision.resolution == "keep_both":
        return None, None, "keep_both", "model:arbitrate", decision.rationale
    return None, None, "flag", "model:arbitrate", decision.rationale


def _conflict_record(
    subject: str,
    predicate: str,
    kind: str,
    resolution: str,
    arbiter: str,
    rationale: str,
    a: dict[str, Any],
    b: dict[str, Any],
    vol_a: int,
    vol_b: int,
) -> dict[str, Any]:
    return {
        "subject": subject,
        "predicate": predicate,
        "kind": kind,
        "resolution": resolution,
        "a": {"value": a["value"], "vol": vol_a, "confidence": a["confidence"], "claim_id": a["claim_id"],
              **({"support_score": a["_support_score"]} if "_support_score" in a else {})},
        "b": {"value": b["value"], "vol": vol_b, "confidence": b["confidence"], "claim_id": b["claim_id"],
              **({"support_score": b["_support_score"]} if "_support_score" in b else {})},
        "arbiter": arbiter,
        "rationale": rationale,
    }


def _collapse_same_normalized_value(
    group: list[dict[str, Any]], fmt: str | None
) -> list[dict[str, Any]]:
    """Phase 22 A2, seam 2: one same-volume group of claims for a `single: true` attribute may
    carry two claims that only *look* like a contradiction -- e.g. AGE `'25'` and `'twenty-five'`
    extracted from the same volume are the same fact stated twice, not a tie to arbitrate. Claim
    merging in extract/claims.py only collapses EXACT (subject, predicate, object_norm, first_vol)
    duplicates, so distinct phrasings of one value still arrive here as separate claims. Collapse
    by `temporal.normalize_value(value, fmt)` first, keeping the highest-confidence claim per
    normalized value (ties broken by first-seen) -- only genuinely distinct VALUES should ever
    reach arbitration below."""
    by_norm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in group:
        by_norm[temporal.normalize_value(c["value"], fmt)].append(c)

    collapsed: list[dict[str, Any]] = []
    for members in by_norm.values():
        members.sort(key=_rank_key)
        winner, *rest = members
        # The losing claim_id(s) are not discarded -- they still confirm the same fact, so they
        # belong in the eventual interval's claim_ids (evidence_at citations) even though only
        # the winner's own value/confidence/evidence drive arbitration below. Carried as a side
        # channel (`claim_ids_extra`) rather than a plural `claim_ids` field so this dict still
        # satisfies every reader downstream (_resolve_pair, _conflict_record, ...) that expects a
        # single `claim_id`.
        collapsed.append({**winner, "claim_ids_extra": [m["claim_id"] for m in rest]} if rest else winner)
    return collapsed


def _resolve_single_valued(
    subject: str,
    predicate: str,
    claims: list[dict[str, Any]],
    threshold: float,
    client: JSONClient | None,
    fmt: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One `single: true` attribute for one character: resolve every same-volume tie, then walk
    the resulting one-value-per-volume sequence and log a narrative_change conflict everywhere the
    value actually changes. Returns (intervals, conflicts). `fmt` (Phase 22 A2, `attributes.*.
    format`) drives both the same-volume collapse below and the cross-volume comparison later in
    this function, via `temporal.normalize_value`."""
    by_vol: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for c in claims:
        by_vol[c["first_vol"]].append(c)

    conflicts: list[dict[str, Any]] = []
    resolved_by_vol: dict[int, dict[str, Any]] = {}

    for vol, raw_group in sorted(by_vol.items()):
        group = _collapse_same_normalized_value(raw_group, fmt)
        if len(group) == 1:
            resolved_by_vol[vol] = group[0]
            continue

        # Claim merging in extract/claims.py already collapses identical (subject, predicate,
        # object_norm, first_vol) tuples into one claim_id, and the collapse above just handled
        # same-volume claims that only differ by phrasing, so every entry here is a genuinely
        # distinct value. More than two is rare; keep only the top two by confidence for
        # arbitration and log the rest as auto-dropped outliers rather than chaining N-1 LLM calls.
        ranked = sorted(group, key=_rank_key)
        top, second, *outliers = ranked
        for outlier in outliers:
            conflicts.append(
                _conflict_record(
                    subject,
                    predicate,
                    "extraction_error",
                    "keep_a",
                    "nli:lower_support_dropped" if "_support_score" in top else "rule:lowest_confidence_dropped",
                    f"{len(ranked)} conflicting values in one volume; kept the top two by "
                    + ("evidence support." if "_support_score" in top else "confidence."),
                    top,
                    outlier,
                    vol,
                    vol,
                )
            )

        winner, loser, resolution, arbiter, rationale = _resolve_pair(
            subject, predicate, vol, top, second, threshold, client
        )
        conflicts.append(
            _conflict_record(
                subject, predicate, "extraction_error", resolution, arbiter, rationale, top, second, vol, vol
            )
        )
        # keep_both is not representable for a single-valued attribute (only one value can be
        # true at a time by definition) — treat it the same as flag: provisional pick, surfaced.
        resolved_by_vol[vol] = winner if winner is not None else top

    ordered = [resolved_by_vol[vol] for vol in sorted(resolved_by_vol)]
    entries = [
        {
            "value": c["value"], "qualifier": c.get("qualifier"), "first_vol": c["first_vol"],
            "claim_ids": [c["claim_id"], *c.get("claim_ids_extra", [])],
            "confidence": c["confidence"],  # Phase 23 B1
        }
        for c in ordered
    ]

    for prev, cur in zip(entries, entries[1:]):
        if temporal.normalize_value(prev["value"], fmt) != temporal.normalize_value(cur["value"], fmt):
            conflicts.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "kind": "narrative_change",
                    "resolution": "supersede",
                    "a": {"value": prev["value"], "vol": prev["first_vol"], "claim_id": prev["claim_ids"][0]},
                    "b": {"value": cur["value"], "vol": cur["first_vol"], "claim_id": cur["claim_ids"][0]},
                    "arbiter": "rule:cross_volume",
                    "rationale": (
                        f"Different volumes ({prev['first_vol']} -> {cur['first_vol']}); "
                        "treated as a narrative change, not an error."
                    ),
                }
            )

    intervals = temporal.assign_single_valued(subject, predicate, entries, fmt)
    return intervals, conflicts


def _canonicalize_relation(
    predicate: str, subject: str, obj: str, relations_cfg: dict[str, Any]
) -> tuple[str, str, str]:
    """Map a relation claim to one canonical storage direction, so independently-extracted
    (A, FRIEND_OF, B) and (B, FRIEND_OF, A) — or (A, PARENT_OF, B) and (B, CHILD_OF, A) — become
    the same (subject, predicate, object) triple. Canonical predicate is always the
    alphabetically-smaller of a symmetric/inverse pair; `display`/`inverse` in extraction.yaml
    still drive which label a renderer shows on each side."""
    cfg = relations_cfg.get(predicate, {})
    if cfg.get("symmetric"):
        return (predicate, obj, subject) if obj < subject else (predicate, subject, obj)
    inverse = cfg.get("inverse")
    if inverse and inverse < predicate:
        return inverse, obj, subject
    return predicate, subject, obj


def _relation_conflict_groups(relations_cfg: dict[str, Any]) -> dict[str, frozenset[str]]:
    """predicate -> its mutually-exclusive group (Phase 17, config `relations.*.conflicts_with`).

    A predicate only joins a group when EVERY member declares every other member back — the same
    tolerance `symmetric`/`inverse` already have for a one-sided config mistake: silently not
    grouped, not a crash. Predicates with no `conflicts_with` (the overwhelming majority) are
    simply absent from the returned dict."""
    groups: dict[str, set[str]] = {}
    for pred, cfg in relations_cfg.items():
        for other in cfg.get("conflicts_with") or []:
            if pred not in (relations_cfg.get(other, {}).get("conflicts_with") or []):
                continue
            groups.setdefault(pred, {pred}).add(other)
            groups.setdefault(other, {other}).add(pred)
    return {pred: frozenset(members) for pred, members in groups.items()}


def _strip_status_contradicting_relations(
    intervals: list[dict[str, Any]],
    relations_cfg: dict[str, Any],
    attributes_cfg: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Drop a relation whose object's own evidenced STATUS says it cannot have happened.

    Phase 26 part B. The audit's most visible single error: `holo KILLED liebert` (conf 0.96)
    and `liebert STATUS alive` (conf 0.95) are both extracted from Volume 2, and Liebert's page
    shipped "Holo — Killed" directly above "Status: alive". Both cite the same sentence, "Holo
    shook her head slightly and let the man drop to the ground."

    The existing machinery cannot catch this. `conflicts_with` compares two RELATIONS on one
    pair; `single: true` arbitration compares two VALUES of one attribute. Nothing compares a
    relation against an attribute of its object, so each half was individually well-formed.

    The rule is config-driven, not predicate-specific: a relation declaring
    `object_status_implies: <value>` asserts a post-state for its object. When the object also
    carries an EVIDENCED, still-open STATUS interval at that volume whose value contradicts it,
    the relation is dropped.

    Deliberately not arbitrated by confidence. Confidence would keep KILLED (0.96 > 0.95), which
    is the wrong survivor, and this repo already documented verbalized confidence as uncalibrated
    (`docs/papers/REFERENCES.md`, 2306.13063 — the finding that retired `rule:lowest_confidence_dropped`).
    "X killed Y" while Y is alive is not a tie between two readings; the relation is the
    extraordinary claim and the narrative state is the check on it.

    The "evidenced" qualifier matters: a character with no STATUS claims at all materialises
    `attributes.STATUS.default: "alive"` at render time (Phase 22 A5 U6) and never appears here,
    so a genuine death with an un-updated status is not silently discarded -- there is simply no
    evidence on either side to adjudicate.

    Returns `({id(interval): interval}, [conflict record, ...])`.
    """
    status_predicates = {
        name for name, cfg in attributes_cfg.items() if cfg.get("single") and name == "STATUS"
    }
    if not status_predicates:
        return {}, []

    status_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for interval in intervals:
        if interval["predicate"] in status_predicates and interval.get("value"):
            status_by_subject[interval["subject"]].append(interval)

    dropped: dict[int, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for interval in intervals:
        implied = relations_cfg.get(interval["predicate"], {}).get("object_status_implies")
        if not implied or not interval.get("object"):
            continue
        for status in status_by_subject.get(interval["object"], []):
            # Still open at the volume the relation opens in, and saying something else.
            if status["vol_start"] > interval["vol_start"]:
                continue
            if status["vol_end"] is not None and status["vol_end"] < interval["vol_start"]:
                continue
            if temporal.normalize_value(status["value"]) == temporal.normalize_value(implied):
                continue
            dropped[id(interval)] = interval
            conflicts.append(
                _relation_conflict_record(
                    interval["subject"], interval["object"], "extraction_error", "keep_b",
                    "rule:object_status_contradicts",
                    f"{interval['predicate']} implies the object is {implied!r}, but "
                    f"{interval['object']}'s own evidenced STATUS at v{interval['vol_start']} is "
                    f"{status['value']!r}. Dropped the relation, not the status: it is the "
                    "extraordinary claim, and confidence is not a usable arbiter between them.",
                    {
                        "predicate": interval["predicate"],
                        "claim_id": (interval.get("claim_ids") or [None])[0],
                        "confidence": interval.get("confidence", 0.0),
                    },
                    {
                        "predicate": status["predicate"],
                        "claim_id": (status.get("claim_ids") or [None])[0],
                        "confidence": status.get("confidence", 0.0),
                    },
                    interval["vol_start"], status["vol_start"],
                )
            )
            break
    return dropped, conflicts


def _relation_conflict_record(
    subject: str,
    obj: str,
    kind: str,
    resolution: str,
    arbiter: str,
    rationale: str,
    a: dict[str, Any],
    b: dict[str, Any],
    vol_a: int,
    vol_b: int,
) -> dict[str, Any]:
    """`_conflict_record`'s relation analogue: the thing in conflict is WHICH predicate holds
    between `subject` and `obj`, not a value of one fixed predicate, so each side carries its own
    `predicate` — and, for `_contradictions_report`'s existing flagged-conflict printer (which
    reads `c["a"]["value"]`), a `value` alias set to that same predicate name."""
    return {
        "subject": subject,
        "object": obj,
        "predicate": f"{a['predicate']}|{b['predicate']}",
        "kind": kind,
        "resolution": resolution,
        "a": {"value": a["predicate"], "predicate": a["predicate"], "vol": vol_a,
              "confidence": a["confidence"], "claim_id": a["claim_id"],
              **({"support_score": a["_support_score"]} if "_support_score" in a else {})},
        "b": {"value": b["predicate"], "predicate": b["predicate"], "vol": vol_b,
              "confidence": b["confidence"], "claim_id": b["claim_id"],
              **({"support_score": b["_support_score"]} if "_support_score" in b else {})},
        "arbiter": arbiter,
        "rationale": rationale,
    }


def _conflict_cliques(
    predicates: list[str], conflict_groups: dict[str, frozenset[str]]
) -> list[list[str]]:
    """Partition the conflict-group predicates observed for ONE pair into sets that actually
    conflict with each other.

    Phase 26. `build_graph` buckets a pair's claims under `pair_claims[(subj, obj)][pred]` for
    every predicate that appears in ANY conflict group, then hands the whole bucket to
    `_resolve_relation_group`, which arbitrates it down to a single survivor. That is only
    correct when every predicate in the bucket is mutually exclusive with every other one.
    It is not: `conflicts_with` declares a graph, not a partition. ENEMY_OF conflicts with both
    FRIEND_OF and COMRADE_OF, but FRIEND_OF and COMRADE_OF do not conflict with each other —
    Holo is both Lawrence's comrade and his friend, and arbitrating those two against each
    other would silently drop one true relation. The same latent bug already existed for the
    declared-non-conflicting SPOUSE_OF/ROMANTIC_WITH pair (see extraction.yaml's own note);
    it simply never fired because no live pair carried both.

    Union-find over the predicates PRESENT for this pair, linking p~q when q is in p's declared
    group. A transitive clique is intentional: a pair carrying FRIEND_OF, COMRADE_OF and
    ENEMY_OF yields one 3-way clique, because ENEMY_OF is incompatible with both others and
    exactly one of the three can hold — which `_resolve_relation_group` already handles via its
    `outliers` branch. A pair carrying ENEMY_OF, KILLED and SAVED yields two cliques
    (`[ENEMY_OF]`, `[KILLED, SAVED]`): being someone's enemy and having saved them are not
    mutually exclusive, but having killed and saved them are.
    """
    parent = {p: p for p in predicates}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in predicates:
        for b in predicates:
            if a < b and b in conflict_groups.get(a, frozenset()):
                parent[find(a)] = find(b)

    cliques: dict[str, list[str]] = defaultdict(list)
    for p in predicates:
        cliques[find(p)].append(p)
    return [sorted(members) for _, members in sorted(cliques.items())]


def _resolve_relation_group(
    subject: str,
    obj: str,
    claims_by_predicate: dict[str, list[dict[str, Any]]],
    threshold: float,
    client: JSONClient | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One mutually-exclusive relation group (e.g. FRIEND_OF/ENEMY_OF) for one (subject, object)
    pair, already known to carry claims under >=2 of the group's predicates. Mirrors
    `_resolve_single_valued`'s same-volume/cross-volume shape exactly, with the relation predicate
    itself standing in for "the value" — same-volume ties reuse `_resolve_pair` unchanged via a
    pseudo `{"value": predicate, ...}` dict (no new arbitrate prompt needed), cross-volume
    predicate changes supersede via `temporal.assign_relation_supersession`."""
    by_vol: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for predicate, claims in claims_by_predicate.items():
        for c in claims:
            by_vol[c["first_vol"]].append({**c, "predicate": predicate})

    conflicts: list[dict[str, Any]] = []
    resolved_by_vol: dict[int, dict[str, Any]] = {}

    for vol, group in sorted(by_vol.items()):
        # Real-data finding (Spice and Wolf: Holo/Liebert both extracted bidirectionally as
        # ENEMY_OF, landing as two separate claim_ids under the SAME predicate for the SAME
        # canonicalized pair): collapse same-predicate claims to ONE representative per predicate
        # THIS VOLUME first, merging every supporting claim_id -- mirrors how the ordinary
        # (non-conflict-group) path already merges same-predicate relation claims in
        # graph/temporal.py::assign_multi_valued. Only genuinely DIFFERENT predicates are ever
        # compared against each other below; two mentions of the same predicate are never a
        # conflict, regardless of how many OTHER predicates also appear this volume.
        by_predicate_this_vol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in group:
            by_predicate_this_vol[c["predicate"]].append(c)
        representatives = {
            predicate: {**min(claims, key=_rank_key), "claim_ids": [c["claim_id"] for c in claims]}
            for predicate, claims in by_predicate_this_vol.items()
        }

        if len(representatives) == 1:
            (only,) = representatives.values()
            resolved_by_vol[vol] = only
            continue

        ranked = sorted(representatives.values(), key=_rank_key)
        top, second, *outliers = ranked
        for outlier in outliers:
            conflicts.append(
                _relation_conflict_record(
                    subject, obj, "extraction_error", "keep_a",
                    "nli:lower_support_dropped" if "_support_score" in top else "rule:lowest_confidence_dropped",
                    f"{len(ranked)} conflicting relations in one volume; kept the top two by "
                    + ("evidence support." if "_support_score" in top else "confidence."),
                    top, outlier, vol, vol,
                )
            )

        pseudo_top = {**top, "value": top["predicate"]}
        pseudo_second = {**second, "value": second["predicate"]}
        winner, _loser, resolution, arbiter, rationale = _resolve_pair(
            f"{subject} and {obj}", "relationship", vol, pseudo_top, pseudo_second, threshold, client
        )
        conflicts.append(
            _relation_conflict_record(subject, obj, "extraction_error", resolution, arbiter, rationale, top, second, vol, vol)
        )
        # keep_both is not representable (only one predicate in a mutually-exclusive group can
        # hold at once, by definition) -- same "provisional pick, surfaced" treatment as
        # _resolve_single_valued gives flag/keep_both.
        if winner is pseudo_second:
            resolved_by_vol[vol] = second
        else:
            resolved_by_vol[vol] = top

    ordered = [resolved_by_vol[vol] for vol in sorted(resolved_by_vol)]
    entries = [
        {
            "predicate": c["predicate"], "qualifier": c.get("qualifier"),
            "first_vol": c["first_vol"], "claim_ids": c["claim_ids"],
            "confidence": c["confidence"],  # Phase 23 B1
        }
        for c in ordered
    ]

    for prev, cur in zip(entries, entries[1:]):
        if prev["predicate"] != cur["predicate"]:
            conflicts.append(
                {
                    "subject": subject,
                    "object": obj,
                    "predicate": f"{prev['predicate']}|{cur['predicate']}",
                    "kind": "narrative_change",
                    "resolution": "supersede",
                    "a": {
                        "value": prev["predicate"], "predicate": prev["predicate"],
                        "vol": prev["first_vol"], "claim_id": prev["claim_ids"][0],
                    },
                    "b": {
                        "value": cur["predicate"], "predicate": cur["predicate"],
                        "vol": cur["first_vol"], "claim_id": cur["claim_ids"][0],
                    },
                    "arbiter": "rule:cross_volume",
                    "rationale": (
                        f"Different volumes ({prev['first_vol']} -> {cur['first_vol']}); the relationship "
                        f"between {subject} and {obj} changed from {prev['predicate']} to {cur['predicate']}, "
                        "treated as a narrative change, not an error."
                    ),
                }
            )

    intervals = temporal.assign_relation_supersession(subject, obj, entries)
    return intervals, conflicts


def _strip_mutual_direction_collisions(
    relation_claims: list[dict[str, Any]],
    relations_cfg: dict[str, Any],
    conflict_groups: dict[str, frozenset[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Phase 23 A4. A directional (non-`symmetric`) predicate cannot legitimately hold in both
    directions between the same pair at once — `jakob CHILD_OF kraft-lawrence` AND
    `kraft-lawrence CHILD_OF jakob` both asserted is a self-contradiction, not two independent
    facts. `_canonicalize_relation` only merges a predicate with its DECLARED `inverse`
    counterpart (PARENT_OF(A,B) + CHILD_OF(B,A) -> one interval); it never touches a raw
    same-predicate reversal, since both sides already carry the identical predicate name and
    canonicalization has nothing to normalize away. This runs BEFORE canonicalization, directly
    over the raw extracted claims (which still carry `confidence`, unlike the
    `value_or_object`-shaped observations `build_graph` derives afterward).

    Symmetric predicates are skipped (already collapse to one direction via
    `_canonicalize_relation`'s alphabetical subject/object ordering — no collision possible).

    Membership of a `conflict_groups` group is NOT a reason to skip, though it was until
    2026-09-24: the original note assumed "every predicate placed in a group by Phase 23 A4's
    config is itself symmetric". SAVED and KILLED falsify that — they declare each other in
    `conflicts_with` and are both directional — so the skip silently exempted them from the only
    check that catches a same-predicate reversal. The grouped path cannot stand in for it: it
    buckets by the already-direction-canonicalized (subject, object) pair and so cannot see a raw
    reversal at all (this module's header comment says as much). The two paths resolve different
    shapes — cross-predicate conflict vs. same-predicate reversal — so both must run. Live
    consequence: `holo SAVED kraft-lawrence` and `kraft-lawrence SAVED holo` both shipped from one
    sentence, the second reading "Holo stopped the wolves from attacking Lawrence" under a heading
    saying Lawrence saved Holo.

    Returns `(surviving_claims, conflicts)`: `surviving_claims` drops every claim on the losing
    side of a detected collision (confidence-ranked, same "keep the higher, log the lower as
    extraction_error" posture `_resolve_pair` uses elsewhere); the caller's existing per-(subject,
    predicate) grouping then proceeds over `surviving_claims` unchanged."""
    directional: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    other_claims: list[dict[str, Any]] = []
    for c in relation_claims:
        pred = c["predicate"]
        cfg = relations_cfg.get(pred, {})
        if cfg.get("symmetric"):
            other_claims.append(c)
            continue
        directional[(pred, c["subject"], c["object"])].append(c)

    conflicts: list[dict[str, Any]] = []
    dropped_claim_ids: set[str] = set()
    seen_pairs: set[frozenset[tuple[str, str, str]]] = set()
    for (pred, subj, obj), claims in directional.items():
        if subj == obj:
            continue
        reverse_key = (pred, obj, subj)
        reverse_claims = directional.get(reverse_key)
        if not reverse_claims:
            continue
        pair_id = frozenset({(pred, subj, obj), reverse_key})
        if pair_id in seen_pairs:
            continue
        seen_pairs.add(pair_id)

        if relations_cfg.get(pred, {}).get("mutual"):
            # [30] `mutual: true` -- both directions can be true as separate incidents (Holo
            # catches Lawrence as he topples; Lawrence pulls Holo back from a window). Only claims
            # reading the SAME paragraph both ways collide. Without this, one newly admitted
            # `kraft-lawrence SAVED holo` outranked and deleted every `holo SAVED kraft-lawrence`.
            def _paras(cs: list[dict[str, Any]]) -> set[str]:
                return {e["para_id"] for c in cs for e in (c.get("evidence") or [])}

            shared = _paras(claims) & _paras(reverse_claims)
            claims = [c for c in claims if _paras([c]) & shared]
            reverse_claims = [c for c in reverse_claims if _paras([c]) & shared]
            if not claims or not reverse_claims:
                continue

        top = min(claims, key=_rank_key)
        top_rev = min(reverse_claims, key=_rank_key)
        if _direction_strength(claims) <= _direction_strength(reverse_claims):
            winner, loser, resolution = top, top_rev, "keep_a"
        else:
            winner, loser, resolution = top_rev, top, "keep_b"
        conflicts.append(
            _relation_conflict_record(
                subj, obj, "extraction_error", resolution,
                "nli:mutual_direction_collision" if "_support_score" in winner else "rule:mutual_direction_collision",
                (
                    f"{pred} was extracted in both directions between {subj} and {obj} "
                    "(a directional relation cannot hold both ways at once); kept the "
                    + ("higher-support direction." if "_support_score" in winner else "higher-confidence direction.")
                ),
                winner, loser, winner["first_vol"], loser["first_vol"],
            )
        )
        dropped_claim_ids.update(c["claim_id"] for c in (reverse_claims if winner is top else claims))

    surviving = other_claims + [
        c
        for claims in directional.values()
        for c in claims
        if c["claim_id"] not in dropped_claim_ids
    ]
    return surviving, conflicts


def _canonicalization_entries(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"value": c["value"], "confidence": c["confidence"], "first_vol": c["first_vol"], "claim_id": c["claim_id"]}
        for c in group
    ]


def _run_canonicalization(
    subject: str,
    predicate: str,
    group: list[dict[str, Any]],
    settings: Any,
    client: JSONClient | None,
    merges: list[dict[str, Any]],
    embed_state: dict[str, bool],
) -> dict[str, str]:
    """Cluster one `(subject, predicate)` group's values (Phase 17) and return a `claim_id ->
    canonical value` remap for the caller to apply before building `assign_multi_valued`
    observations. Appends one entry to `merges` per cluster that actually merged >1 distinct
    value (nothing to report for a cluster that was already a single value).

    `embed_state` is a single `{"unavailable": bool}` dict shared across every call from one
    `build_graph()` run: the first time the embed model turns out to be unreachable (real-data
    finding — a `graph build` with Ollama down must not crash, and must not retry a dead
    connection once per `(subject, predicate)` group, which could be hundreds of times), every
    later group in this run skips straight to the no-embed fallback instead of re-attempting."""
    cfg = settings.canonicalize_config
    if not cfg.get("enabled", True):
        return {c["claim_id"]: c["value"] for c in group}

    threshold = float(cfg.get("similarity_threshold", 0.86))
    min_group_size = int(cfg.get("min_group_size", 2))
    embed_fn = None
    if not embed_state["unavailable"] and client is not None:
        client_embed = getattr(client, "embed", None)
        if client_embed is not None:
            embed_fn = lambda texts: client_embed("canonicalize", texts)  # noqa: E731

    entries = _canonicalization_entries(group)
    try:
        clusters = canonicalize.cluster_values(entries, embed_fn, threshold, min_group_size)
    except Exception:  # noqa: BLE001 - an unreachable/misbehaving embed model must degrade
        # gracefully (exact-match-only canonicalization for the rest of this run), never crash
        # `graph build` -- same "external call, never let it take the pipeline down" precedent as
        # llm/providers/ollama.py::health.
        embed_state["unavailable"] = True
        clusters = canonicalize.cluster_values(entries, None, threshold, min_group_size)

    remap: dict[str, str] = {}
    for cluster in clusters:
        canonical = cluster.canonical
        for member in cluster.members:
            remap[member["claim_id"]] = canonical
        variants = [m for m in cluster.members if m["value"] != canonical]
        if variants:
            merges.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "canonical": canonical,
                    "variants": [
                        {"value": m["value"], "claim_id": m["claim_id"], "first_vol": m["first_vol"]}
                        for m in variants
                    ],
                }
            )
    return remap


def _strip_object_type_mismatches(
    relation_claims: list[dict[str, Any]],
    relations_cfg: dict[str, Any],
    entity_types: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop a relation whose object is the wrong KIND of thing for its predicate.

    Phase 26 part C. `extract/claims.py` and `extract/scenes.py` already enforce a predicate's
    `object_types` at extraction, but an extraction-time gate only ever protects claims extracted
    AFTER it lands: the claim files on disk are not revisited, so every gate added to the
    extractor leaves the live graph exactly as wrong as it was. The shipped v1-2 wiki still
    renders `holo KILLED pazzio` -- Pazzio is a LOCATION, a town -- on a graph built after
    KILLED gained `object_types: [CHARACTER]`, and would keep rendering it until the whole corpus
    is re-extracted (which the OpenRouter daily cap has blocked for two days running).

    Enforcing the same config here makes the rule a property of the GRAPH rather than of one
    extraction run, which is where a schema constraint belongs anyway. Cheap and deterministic:
    no LLM call, one dict lookup per relation claim.

    `entity_types` is optional so the many tests that call `build_graph` with claims but no
    gazetteer keep working -- absent it, nothing is dropped, exactly as before.

    Returns `(surviving_claims, [conflict record, ...])`.
    """
    if not entity_types:
        return relation_claims, []

    survivors: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for c in relation_claims:
        if c.get("object") and c["object"] == c["subject"]:
            # [31] `captain-nemo RELATIVE_OF captain-nemo` reached the graph (a man looking at a
            # portrait of his family). A relation to oneself is never a fact.
            conflicts.append(_relation_conflict_record(
                c["subject"], c["object"], "extraction_error", "keep_b", "rule:self_relation",
                f"{c['predicate']} relates {c['subject']} to itself.",
                {"predicate": c["predicate"], "claim_id": c["claim_id"], "confidence": c.get("confidence", 0.0)},
                {"predicate": "self", "claim_id": None, "confidence": 1.0},
                c["first_vol"], c["first_vol"],
            ))
            continue
        spec = relations_cfg.get(c["predicate"], {})
        allowed = spec.get("object_types")
        # [31] A symmetric relation's endpoints are interchangeable, so the subject is held to
        # the same types: the scene pass produced `france FRIEND_OF conseil`.
        endpoints = [c.get("object")] + ([c["subject"]] if spec.get("symmetric") else [])
        bad = next(((e, entity_types[e]) for e in endpoints
                    if allowed and e in entity_types and entity_types[e] not in allowed), None)
        if bad is None:
            survivors.append(c)
            continue
        bad_id, obj_type = bad
        conflicts.append(
            _relation_conflict_record(
                c["subject"], c["object"], "extraction_error", "keep_b",
                "rule:object_type_mismatch",
                f"{c['predicate']} takes an endpoint of type {'/'.join(allowed)}, but "
                f"{bad_id} is a {obj_type}. The entity's own type is the check on the "
                "relation, not the other way round -- a relation is never evidence that its "
                "object is a different kind of thing than the gazetteer says.",
                {"predicate": c["predicate"], "claim_id": c["claim_id"],
                 "confidence": c.get("confidence", 0.0)},
                {"predicate": f"type:{obj_type}", "claim_id": None, "confidence": 1.0},
                c["first_vol"], c["first_vol"],
            )
        )
    return survivors, conflicts


def build_graph(
    claims: list[dict[str, Any]],
    settings: Any,
    client: JSONClient | None,
    *, support_scores: dict[str, dict[str, Any]] | None = None, support_threshold: float = 0.5,
    entity_types: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The Phase 4 entry point. `client` may be `None` to force the rule-only fallback path
    (used by tests, and gracefully by a real run if the `arbitrate` model is unreachable)."""
    attributes_cfg = settings.attributes
    relations_cfg = settings.relations
    threshold = float(settings.conflicts_config.get("escalate_when_confidence_within", 0.10))
    if support_scores is not None:
        if not math.isfinite(support_threshold) or not 0 <= support_threshold <= 1:
            raise ValueError("Support threshold must be in [0, 1]")
        scored_claims = []
        for c in claims:
            if c["kind"] == "relation" or (c["kind"] == "attribute" and attributes_cfg.get(c["predicate"], {}).get("single")):
                result = support_scores.get(c["claim_id"], {})
                score = result.get("score")
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError(f"Missing or invalid support score for {c['claim_id']}")
                c = {**c, "_support_score": score, "_support_threshold": support_threshold}
            scored_claims.append(c)
        claims = scored_claims

    single_claims: list[dict[str, Any]] = []
    relation_claims: list[dict[str, Any]] = []
    other_claims: list[dict[str, Any]] = []  # non-single attributes + traits: multi-valued too

    for c in claims:
        if c["kind"] == "relation":
            relation_claims.append(c)
        elif c["kind"] == "attribute" and attributes_cfg.get(c["predicate"], {}).get("single"):
            single_claims.append(c)
        else:
            other_claims.append(c)

    all_intervals: list[dict[str, Any]] = []
    all_edges: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    # Phase 26 part C: a predicate's `object_types` is a schema constraint on the graph, not a
    # one-off extraction check -- claims already on disk never see the extractor's gate again.
    relation_claims, type_conflicts = _strip_object_type_mismatches(
        relation_claims, relations_cfg, entity_types
    )
    conflicts.extend(type_conflicts)

    # --- single-valued attributes: resolve same-volume ties, then supersede ------------------
    grouped_single: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in single_claims:
        grouped_single[(c["subject"], c["predicate"])].append(c)
    for (subject, predicate), group in grouped_single.items():
        fmt = attributes_cfg.get(predicate, {}).get("format")
        intervals, group_conflicts = _resolve_single_valued(subject, predicate, group, threshold, client, fmt)
        all_intervals.extend(intervals)
        conflicts.extend(group_conflicts)

    # --- non-single attributes + traits: canonicalize near-duplicate values (Phase 17), then
    # every distinct (post-canonicalization) value coexists, no conflicts ---------------------
    canonicalization_merges: list[dict[str, Any]] = []
    embed_state = {"unavailable": False}
    grouped_other: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in other_claims:
        grouped_other[(c["subject"], c["predicate"])].append(c)
    for (subject, predicate), group in grouped_other.items():
        remap = _run_canonicalization(
            subject, predicate, group, settings, client, canonicalization_merges, embed_state
        )
        observations = [
            {
                "value_or_object": remap.get(c["claim_id"], c["value"]),
                "qualifier": c.get("qualifier"),
                "first_vol": c["first_vol"],
                "claim_id": c["claim_id"],
                "confidence": c["confidence"],  # Phase 23 B1
            }
            for c in group
        ]
        predicate_cfg = attributes_cfg.get(predicate, {})
        if predicate_cfg.get("subsume"):
            observations = canonicalize.subsume_values(observations)
        all_intervals.extend(
            temporal.assign_multi_valued(
                subject, predicate, observations, is_relation=False, fmt=predicate_cfg.get("format")
            )
        )

    # --- relations: canonicalise direction first. A pair whose predicate is declared
    # `conflicts_with` another (Phase 17) AND actually has claims under >=2 of that group's
    # predicates for the SAME (subject, object) pair is arbitrated as a group; everything else
    # (the overwhelming majority) is multi-valued exactly as before. -----------------------------
    conflict_groups = _relation_conflict_groups(relations_cfg)
    if support_scores is not None:
        # A later-volume reverse observation cannot discard an earlier direction. Apply NLI
        # direction arbitration only within each volume, as for other extraction conflicts.
        per_volume: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for c in relation_claims:
            per_volume[c["first_vol"]].append(c)
        relation_claims, mutual_direction_conflicts = [], []
        for vol in sorted(per_volume):
            survivors, collisions = _strip_mutual_direction_collisions(per_volume[vol], relations_cfg, conflict_groups)
            relation_claims.extend(survivors)
            mutual_direction_conflicts.extend(collisions)
    else:
        relation_claims, mutual_direction_conflicts = _strip_mutual_direction_collisions(
            relation_claims, relations_cfg, conflict_groups
        )
    conflicts.extend(mutual_direction_conflicts)
    grouped_rel: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pair_claims: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for c in relation_claims:
        pred, subj, obj = _canonicalize_relation(c["predicate"], c["subject"], c["object"], relations_cfg)
        if pred in conflict_groups:
            pair_claims[(subj, obj)][pred].append(c)
        else:
            grouped_rel[(subj, pred)].append(
                {
                    "value_or_object": obj,
                    "qualifier": c.get("qualifier"),
                    "first_vol": c["first_vol"],
                    "claim_id": c["claim_id"],
                    "confidence": c["confidence"],  # Phase 23 B1
                }
            )

    def _add_relation_intervals(intervals: list[dict[str, Any]]) -> None:
        all_intervals.extend(intervals)
        all_edges.extend(
            {
                "subject": iv["subject"],
                "predicate": iv["predicate"],
                "object": iv["object"],
                "vol_start": iv["vol_start"],
                "vol_end": iv["vol_end"],
            }
            for iv in intervals
        )

    for (subject, predicate), observations in grouped_rel.items():
        _add_relation_intervals(temporal.assign_multi_valued(subject, predicate, observations, is_relation=True))

    for (subj, obj), claims_by_predicate in pair_claims.items():
        # Phase 26: split into predicates that genuinely conflict WITH EACH OTHER before
        # arbitrating. `conflicts_with` is a graph, not a partition -- see `_conflict_cliques`.
        for clique in _conflict_cliques(sorted(claims_by_predicate), conflict_groups):
            if len(clique) < 2:
                # Only one predicate of any conflict group appeared for this pair -- nothing to
                # arbitrate, treat exactly like an ordinary multi-valued relation.
                for predicate in clique:
                    observations = [
                        {
                            "value_or_object": obj, "qualifier": c.get("qualifier"), "first_vol": c["first_vol"],
                            "claim_id": c["claim_id"], "confidence": c["confidence"],  # Phase 23 B1
                        }
                        for c in claims_by_predicate[predicate]
                    ]
                    _add_relation_intervals(
                        temporal.assign_multi_valued(subj, predicate, observations, is_relation=True)
                    )
                continue
            intervals, group_conflicts = _resolve_relation_group(
                subj, obj, {p: claims_by_predicate[p] for p in clique}, threshold, client
            )
            _add_relation_intervals(intervals)
            conflicts.extend(group_conflicts)

    dropped, status_conflicts = _strip_status_contradicting_relations(
        all_intervals, relations_cfg, attributes_cfg
    )
    if dropped:
        all_intervals = [iv for iv in all_intervals if id(iv) not in dropped]
        dropped_keys = {
            (iv["subject"], iv["predicate"], iv["object"], iv["vol_start"]) for iv in dropped.values()
        }
        all_edges = [
            e
            for e in all_edges
            if (e["subject"], e["predicate"], e["object"], e["vol_start"]) not in dropped_keys
        ]
        conflicts.extend(status_conflicts)

    kind_counts = Counter(c["kind"] for c in conflicts)
    doc = {
        "conflicts": conflicts,
        "summary": {
            "total": len(conflicts),
            "narrative_change": kind_counts.get("narrative_change", 0),
            "extraction_error": kind_counts.get("extraction_error", 0),
            "flagged": sum(1 for c in conflicts if c["resolution"] == "flag"),
        },
        "canonicalization": {
            "merges": canonicalization_merges,
            "summary": {
                "groups_merged": len(canonicalization_merges),
                "variants_absorbed": sum(len(m["variants"]) for m in canonicalization_merges),
            },
        },
    }
    if support_scores is not None:
        doc["support_scores"] = support_scores
        doc["support_threshold"] = support_threshold
    return all_intervals, all_edges, doc
