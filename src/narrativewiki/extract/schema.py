"""[3] Pydantic models for claims. Mirrors docs/CONTRACTS.md §3.

Inputs:     N/A — pure schema module.
Outputs:    `Claim` (the CONTRACTS §3 record shape, for validation/documentation); `Observation`
            (the versioned, source-occurrence record used by the WP2 shadow replay); `ExtractedFact`
            / `ExtractionResult` (what the model returns for one evidence window, before entity
            resolution and `claim_id` assignment turn it into a `Claim`); `make_claim_id`, the one
            place the CONTRACTS §3 hash formulas are computed.
Invariants: `claim_id` is deterministic in (subject, predicate, object_norm, first_vol, polarity,
            normalized qualifier) so the same fact re-observed in a different window, or in a rerun,
            lands on the same id — this is
            what lets `extract/claims.py` merge repeated evidence instead of duplicating a claim.
Contract:   docs/CONTRACTS.md §3.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from collections import Counter

Kind = Literal["attribute", "relation", "trait"]
Polarity = Literal["asserted", "denied", "presumed"]
ObservationStage = Literal["claim_extract", "scene_extract"]
OBSERVATION_SCHEMA_VERSION = 1


class Evidence(BaseModel):
    para_id: str
    print_page: int | None = None
    quote: str


class Claim(BaseModel):
    """The CONTRACTS §3 record. Used to validate what `extract/claims.py` writes, not to carry
    data through the extraction pipeline itself (that stays plain dicts, like every other stage)."""

    claim_id: str
    subject: str
    predicate: str
    kind: Kind
    object: str | None = None
    value: str | None = None
    qualifier: str | None = None
    first_vol: int
    chapter_idx: int
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    polarity: Polarity = "asserted"
    source: str


class Observation(BaseModel):
    """One accepted fact occurrence before cross-window/source semantic merging.

    This is a shadow contract: production readers still consume ``Claim``. An observation keeps
    the raw response's polarity, qualifier and confidence plus an immutable pointer to the exact
    recorded call/fact position that produced it. ``candidate_assertion_id`` uses the current
    claim merge key but does not imply that qualifier paraphrases are semantically distinct.
    """

    schema_version: Literal[1] = OBSERVATION_SCHEMA_VERSION
    observation_id: str
    series_id: str
    subject: str
    predicate: str
    kind: Kind
    object: str | None = None
    value: str | None = None
    qualifier: str | None = None
    polarity: Polarity
    confidence: float = Field(ge=0.0, le=1.0)
    disclosure_vol: int = Field(ge=1)
    chapter_idx: int = Field(ge=0)
    evidence: Evidence
    source_stage: ObservationStage
    source_run_id: str
    source_cache_key: str
    source_fact_index: str
    source_prompt_sha256: str = Field(min_length=64, max_length=64)
    extraction_source: str
    candidate_assertion_id: str
    legacy_claim_id: str | None = None


class ExtractedFact(BaseModel):
    """One fact as the model reports it for a single evidence window — a surface-level claim not
    yet resolved to entity ids or assigned a `claim_id`. See `extract/claims.py::_build_claim`."""

    predicate: str
    kind: Kind
    object: str | None = None
    value: str | None = None
    qualifier: str | None = None
    para_id: str
    quote: str
    # Defaulted, not required: observed against real Ollama output, a 14B model occasionally
    # omits "confidence" on some facts in a list while including it on others in the same
    # response. 0.7 sits above extraction.yaml's default `min_to_keep` (0.5) so an otherwise
    # well-formed fact isn't auto-dropped for this alone, without being an inflated max score.
    confidence: float = Field(0.7, ge=0.0, le=1.0)
    polarity: Polarity = "asserted"

    @field_validator("object", "value", "qualifier", mode="before")
    @classmethod
    def _coerce_optional_scalar(cls, v: Any) -> Any:
        """A 14B model occasionally emits `[]`/`{}` for a field it means to leave empty, instead
        of `null` or omitting it (observed against real Ollama output, not hypothetical). Treat
        anything that isn't already a string or None as absent rather than fail the whole fact
        over a cosmetic field — the field is optional precisely because "not stated" is a
        rewarded, expected answer (PROMPTS.md rule 5)."""
        if v is None or isinstance(v, str):
            return v
        return None

    @field_validator("polarity", mode="before")
    @classmethod
    def _reject_invalid_polarity(cls, v: Any) -> Any:
        """A 14B model occasionally writes a polarity synonym ("true", "confirmed", ...) instead
        of one of the three closed values (observed against real Ollama output). Preserve it for
        Pydantic's closed enum to reject; batch validators quarantine only the malformed fact."""
        return v


_VALID_KINDS = {"attribute", "relation", "trait"}
_VALID_POLARITIES = {"asserted", "denied", "presumed"}
# predicate/para_id/quote have no sensible default (nothing to fall back to without inventing
# content); kind and any supplied polarity must be in their closed vocabularies. Other fields
# (object/value/qualifier/confidence) are optional, defaulted, or coerced elsewhere.
_REQUIRED_STRING_FIELDS = ("predicate", "para_id", "quote")


def _normalize_kind(f: Any) -> Any:
    """A 14B model occasionally cases `kind` like the predicate itself ("RELATION" instead of
    "relation") — harmless, since `claims.py::_lookup_predicate` re-derives the true kind from
    `settings.attributes`/`relations`/`traits` and ignores this field entirely once a claim is
    built. Normalizing case here, before `_is_usable_fact` checks membership in `_VALID_KINDS`,
    stops that cosmetic mismatch from silently dropping an otherwise well-formed fact (Phase 22
    B2)."""
    if isinstance(f, dict) and isinstance(f.get("kind"), str):
        return {**f, "kind": f["kind"].strip().lower()}
    return f


# Yield-loss counter (plan Stage 0.2): `_drop_incomplete_facts` below is a pydantic
# "before" validator, so a fact it rejects never becomes an `ExtractedFact` and never reaches
# any of `claims.py`/`scenes.py`'s own drop-reason `Counter`s -- it was invisible. Extraction is
# single-threaded (no concurrent callers in this pipeline), so a module-level counter is safe;
# a caller reads it with `pop_incomplete_fact_drops()` after each `complete_json` call.
_INCOMPLETE_FACT_DROPS: Counter[str] = Counter()


def pop_incomplete_fact_drops() -> Counter[str]:
    """Return and reset the counter `_drop_incomplete_facts` has accumulated since the last call."""
    global _INCOMPLETE_FACT_DROPS
    counts, _INCOMPLETE_FACT_DROPS = _INCOMPLETE_FACT_DROPS, Counter()
    return counts


def _is_usable_fact(f: Any) -> bool:
    if not isinstance(f, dict):
        _INCOMPLETE_FACT_DROPS["not_a_dict"] += 1
        return False
    if f.get("kind") not in _VALID_KINDS:
        _INCOMPLETE_FACT_DROPS["invalid_kind"] += 1
        return False
    if "polarity" in f and f.get("polarity") not in _VALID_POLARITIES:
        _INCOMPLETE_FACT_DROPS["invalid_polarity"] += 1
        return False
    missing = [key for key in _REQUIRED_STRING_FIELDS if not (isinstance(f.get(key), str) and f[key].strip())]
    if missing:
        _INCOMPLETE_FACT_DROPS[f"missing_{missing[0]}"] += 1
        return False
    return True


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_incomplete_facts(cls, data: Any) -> Any:
        """A fact missing a structurally required field, or naming a kind outside the closed
        vocabulary, is unusable evidence — not just a malformed response. Drop it here rather
        than fail the ENTIRE batch (and burn a repair-retry) over one bad fact among several good
        ones. Observed against real Ollama output: a 14B model is inconsistent about *which*
        field it omits per fact within one response — first `para_id`, then on retry
        `confidence` (now defaulted), then `quote` — so this filters on every required field at
        once rather than chasing them one at a time. `claims.py` still separately verifies
        para_id is one of the window's own paragraphs and the quote is verbatim in it; this only
        guards against a field being absent, non-string, or (for `kind`) not one of the three
        closed values."""
        if isinstance(data, dict) and isinstance(data.get("facts"), list):
            normalized = [_normalize_kind(f) for f in data["facts"]]
            data = {**data, "facts": [f for f in normalized if _is_usable_fact(f)]}
        return data


def normalize_qualifier(qualifier: str | None) -> str:
    """Case/whitespace-insensitive qualifier identity used only for claim deduplication."""
    return " ".join((qualifier or "").split()).casefold()


def make_claim_id(
    subject: str,
    predicate: str,
    object_norm: str,
    first_vol: int,
    *,
    polarity: str = "asserted",
    qualifier: str | None = None,
) -> str:
    """CONTRACTS §3: stable hash of a claim's semantic merge key.

    `object_norm` is the resolved object `entity_id` for a relation, or the lowercased/stripped
    `value` for an attribute or trait — see `extract/claims.py::_build_claim`.
    """
    semantic_key = (
        f"{subject}|{predicate}|{object_norm}|{first_vol}|{polarity}|{normalize_qualifier(qualifier)}"
    )
    digest = hashlib.sha1(semantic_key.encode("utf-8")).hexdigest()
    return f"c_{digest[:8]}"


def make_observation_id(
    series_id: str,
    source_run_id: str,
    source_cache_key: str,
    source_fact_index: str,
) -> str:
    """Stable identity for one fact position in one recorded model response."""
    occurrence_key = "|".join(
        (str(OBSERVATION_SCHEMA_VERSION), series_id, source_run_id, source_cache_key, source_fact_index)
    )
    digest = hashlib.sha256(occurrence_key.encode("utf-8")).hexdigest()
    return f"o_{digest[:16]}"
