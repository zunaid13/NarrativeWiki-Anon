"""[2] Local-LLM typing: is a candidate surface an entity, and if so, what kind?

Inputs:     Candidates from `entities/candidates.py`, a para_id -> text lookup for evidence,
            Settings (for the closed entity-type vocabulary), and an LLMClient.
Outputs:    Candidate dicts with `type` and `type_confidence` added; candidates the model judges
            not to be an entity (`NOT_ENTITY`, or an unrecognised type) are dropped.
Invariants: - One decision per call (PROMPTS.md rule 1): type only, nothing else.
            - The type vocabulary is read from `settings.entity_types` at call time, never
              hard-coded, so a new series' `extraction.yaml` needs no code change.
            - `client` is duck-typed to `LLMClient.complete_json(stage, prompt, schema, system=)`
              so tests can pass a fake without touching Ollama.
Contract:   docs/PROMPTS.md `entity_type` row; docs/CONTRACTS.md section 2.1 (`type`).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..llm.parallel import map_calls, workers_for

STAGE = "entity_classify"

_SYSTEM_TEMPLATE = """You are typing candidate names mined from a novel. Each candidate is a capitalised phrase that occurs in the text; most are real entities \
but some are sentence fragments, honorific-only phrases, or common words that happened to be \
capitalised.

Classify the candidate into exactly one of these types, or say it is not an entity at all:
{type_lines}

If the candidate is not a named entity — a stray common word, part of a longer name cut short, \
a rank or honorific with no name attached — respond with entity_type "NOT_ENTITY". This is a \
correct and expected answer for a large share of candidates; do not force a fit.

Respond with JSON only: {{"entity_type": "...", "confidence": 0.0-1.0, "notes": "short reason"}}"""


class EntityTypeDecision(BaseModel):
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class JSONClient(Protocol):
    def complete_json(
        self, stage: str, prompt: str, schema: type[BaseModel], system: str | None = None
    ) -> BaseModel: ...


def _system_prompt(entity_types: dict[str, Any]) -> str:
    lines = "\n".join(f"- {name}" for name in entity_types)
    return _SYSTEM_TEMPLATE.format(type_lines=lines)


def _user_prompt(candidate: dict[str, Any], evidence: list[str]) -> str:
    quotes = "\n".join(f'- "{t.strip()[:220]}"' for t in evidence[:5]) or "- (no example text)"
    return (
        f'Candidate surface: "{candidate["surface"]}"\n'
        f'Seen {candidate["count"]} time(s), first in volume {candidate["first_vol"]}.\n'
        f'Mining signals: {", ".join(candidate.get("signals", [])) or "none"}\n'
        f"Example sentences it appears in:\n{quotes}\n\n"
        f"What is this?"
    )


def classify_candidate(
    candidate: dict[str, Any],
    evidence: list[str],
    entity_types: dict[str, Any],
    client: JSONClient,
) -> dict[str, Any] | None:
    """Classify one candidate. Returns None when the model says it is not an entity, or names
    a type outside the closed vocabulary (treated the same way — dropped, never invented)."""
    decision = client.complete_json(
        STAGE, _user_prompt(candidate, evidence), EntityTypeDecision, system=_system_prompt(entity_types)
    )
    entity_type = decision.entity_type.strip().upper()
    if entity_type not in entity_types:
        return None
    return {**candidate, "type": entity_type, "type_confidence": decision.confidence}


def classify_candidates(
    candidates: list[dict[str, Any]],
    paragraphs_by_id: dict[str, str],
    settings,
    client: JSONClient,
    on_progress=None,
) -> list[dict[str, Any]]:
    """Classify every candidate. `on_progress(i, n, candidate, kept)` is called after each one,
    if given, so the CLI can print a running count without this module knowing about Rich."""
    entity_types = settings.entity_types
    kept: list[dict[str, Any]] = []
    total = len(candidates)

    def classify(candidate: dict[str, Any]) -> dict[str, Any] | None:
        evidence = [
            paragraphs_by_id[pid] for pid in candidate.get("evidence_para_ids", []) if pid in paragraphs_by_id
        ]
        return classify_candidate(candidate, evidence, entity_types, client)

    def collect(i: int, candidate: dict[str, Any], result: dict[str, Any] | None) -> None:
        if result is not None:
            kept.append(result)
        if on_progress is not None:
            on_progress(i + 1, total, candidate, result is not None)

    # One short independent JSON call per candidate -- the shape parallelism was made for.
    map_calls(classify, candidates, workers_for(client, STAGE), on_result=collect)
    return kept
