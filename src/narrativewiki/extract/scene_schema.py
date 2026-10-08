"""[18] Pydantic models for scene records. Mirrors docs/CONTRACTS.md §3b.

Inputs:     N/A — pure schema module.
Outputs:    `SceneRecord` / `EpithetRecord` (the CONTRACTS §3b/§3b.1 on-disk record shapes);
            `SceneBeatResult` / `SceneDetailResult` (Phase 22 B1 — what the model returns for one
            chapter span's TWO calls, before entity resolution merges both into a `SceneRecord` +
            zero-or-more `EpithetRecord`s; see `extract/scenes.py::_build_scene_record`).
Invariants: `beat_summary` is the ONE field in this entire pipeline that is not verbatim-quote-
            gated — it is a genuine paraphrase of a span's core paragraphs. Every other field
            (participants, location, quotes, state_changes, epithets, participant_facts) goes
            through the same resolve-against-known-entities / verbatim-quote discipline
            `extract/claims.py` already applies to claims — see
            `extract/scenes.py::_build_scene_record`.
            `ParticipantFacts` (Phase 22 C1 — the chapter-span pass becomes the PRIMARY source of
            attribute/trait/relation facts, not just scene metadata) reuses `schema.ExtractedFact`
            verbatim so `scenes.py::_build_participant_fact` can share `claims.py::_build_claim`'s
            resolution/validation discipline exactly, just scoped to a chapter span's known
            entities instead of a mention window's.
Contract:   docs/CONTRACTS.md §3b.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .schema import ExtractedFact, _is_usable_fact, _normalize_kind


class ExtractedQuote(BaseModel):
    speaker: str | None = None
    addressee: str | None = None
    para_id: str
    quote: str

    @field_validator("speaker", "addressee", mode="before")
    @classmethod
    def _coerce_optional_scalar(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return v
        return None


class ExtractedStateChange(BaseModel):
    """A change in a relationship, status, or circumstance the model noticed within a span.
    `predicate` is filled in ONLY when the change fits an existing attribute/relation predicate
    name (settings.attributes | settings.relations) — null is expected and common. `note` is
    always required: a one-sentence free-text description that is the raw material a future
    event/state-change graph layer would consume, whether or not `predicate` resolved."""

    subject: str
    predicate: str | None = None
    from_value: str | None = None
    to_value: str | None = None
    object: str | None = None
    note: str
    para_id: str
    quote: str

    @field_validator("predicate", "from_value", "to_value", "object", mode="before")
    @classmethod
    def _coerce_optional_scalar(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return v
        return None


class ExtractedEpithet(BaseModel):
    """A descriptive nickname used INSTEAD OF a name ("the wisewolf", "the merchant"), reported
    only when it refers to an entity already named elsewhere in the chapter."""

    entity_id_hint: str
    text: str
    para_id: str
    quote: str
    confidence: float = Field(0.7, ge=0.0, le=1.0)


def _is_usable(f: Any, required: tuple[str, ...]) -> bool:
    if not isinstance(f, dict):
        return False
    return all(isinstance(f.get(key), str) and f[key].strip() for key in required)


class SceneBeatResult(BaseModel):
    """What one chapter span's FIRST call returns (Phase 22 B1 — participants/location/beat,
    split off so the second call's system prompt has full headroom for worked examples over
    state_changes/quotes/epithets instead of sharing one combined, budget-constrained response)."""

    participants: list[str] = Field(default_factory=list)
    location: str | None = None
    beat_summary: str = ""
    # [34] the 1-4 paragraphs the summary rests on, so a page can cite them (OPEN_GAPS G7)
    summary_para_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(0.7, ge=0.0, le=1.0)

    @field_validator("location", mode="before")
    @classmethod
    def _coerce_location(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return v
        return None

    @model_validator(mode="before")
    @classmethod
    def _drop_incomplete_participants(cls, data: Any) -> Any:
        if isinstance(data, list) and data and all(isinstance(b, dict) for b in data):
            # [30] A span may cross a hard break (spans.py marks it `[SCENE BREAK]` so two POVs
            # are not blended), and asked for "the" beat of a two-scene span a model answers with
            # one beat per scene. Spice and Wolf has no hard breaks, so this never happened until
            # Overlord (26 in v1 alone), where every such span failed its three repair attempts.
            # Fold the beats into the span's one record rather than keep only the first scene.
            data = {
                "participants": list(dict.fromkeys(
                    p for b in data for p in (b.get("participants") or []) if isinstance(p, str))),
                "location": next((b["location"] for b in data if isinstance(b.get("location"), str)), None),
                "beat_summary": " ".join(str(b.get("beat_summary") or "").strip() for b in data).strip(),
                "confidence": min(float(b.get("confidence", 0.7)) for b in data),
            }
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if isinstance(data.get("participants"), list):
            out["participants"] = [p for p in data["participants"] if isinstance(p, str) and p.strip()]
        return out


class ParticipantFacts(BaseModel):
    """Attribute/trait/relation facts about ONE participant already named in this span (Phase 22
    C1). `facts` reuses `schema.ExtractedFact` unchanged so `subject` is the only thing this adds
    over a bare `claim_extract`-style fact list."""

    subject: str
    facts: list[ExtractedFact] = Field(default_factory=list)


class SceneDetailResult(BaseModel):
    """What one chapter span's SECOND call returns (Phase 22 B1 — state_changes/quotes/epithets;
    Phase 22 C1 adds participant_facts, making this call the primary source of attribute/trait/
    relation facts instead of `claims.py`'s separate mention-window pass)."""

    state_changes: list[ExtractedStateChange] = Field(default_factory=list)
    quotes: list[ExtractedQuote] = Field(default_factory=list)
    epithets: list[ExtractedEpithet] = Field(default_factory=list)
    participant_facts: list[ParticipantFacts] = Field(default_factory=list)
    confidence: float = Field(0.7, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _drop_incomplete_facts(cls, data: Any) -> Any:
        """Same posture as `schema.py::ExtractionResult`'s own validator: drop an individual
        state_change/quote/epithet/fact missing a structurally required field rather than fail
        the whole span response over one bad entry among several good ones."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if isinstance(data.get("state_changes"), list):
            out["state_changes"] = [
                f for f in data["state_changes"] if _is_usable(f, ("subject", "note", "para_id", "quote"))
            ]
        if isinstance(data.get("quotes"), list):
            out["quotes"] = [f for f in data["quotes"] if _is_usable(f, ("para_id", "quote"))]
        if isinstance(data.get("epithets"), list):
            out["epithets"] = [
                f for f in data["epithets"] if _is_usable(f, ("entity_id_hint", "text", "para_id", "quote"))
            ]
        if isinstance(data.get("participant_facts"), list):
            groups = []
            for g in data["participant_facts"]:
                if not isinstance(g, dict) or not isinstance(g.get("subject"), str) or not g["subject"].strip():
                    continue
                raw_facts = g.get("facts")
                normalized = [_normalize_kind(f) for f in raw_facts] if isinstance(raw_facts, list) else []
                usable = [f for f in normalized if _is_usable_fact(f)]
                if usable:
                    groups.append({**g, "facts": usable})
            out["participant_facts"] = groups
        return out


class SceneRecord(BaseModel):
    """The validated, on-disk record. CONTRACTS §3b."""

    scene_id: str
    vol: int
    chapter_idx: int
    span_index: int
    para_ids: list[str]
    core_para_ids: list[str]
    participants: list[str] = Field(default_factory=list)
    location: str | None = None
    beat_summary: str = ""
    summary_para_ids: list[str] = Field(default_factory=list)   # [34] what the summary rests on
    state_changes: list[dict[str, Any]] = Field(default_factory=list)
    quotes: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str


class EpithetRecord(BaseModel):
    """One row of epithets_v{NN}.jsonl. CONTRACTS §3b.1."""

    entity_id: str
    text: str
    vol: int
    chapter_idx: int
    para_id: str
    print_page: int | None = None
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str


def make_scene_id(vol: int, chapter_idx: int, span_index: int) -> str:
    return f"sn_v{vol:02d}c{chapter_idx:02d}s{span_index:03d}"
