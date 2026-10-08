"""[6, 19, 20] Generates the LLM-written page prose sections.

Phase 6 introduced two trait-driven sections (background, personality). Phase 19 adds a third,
event-driven section (chronology) dispatched by `source: "events"` in `config/extraction.yaml`'s
`page_outline`. Phase 20 adds adaptive sentence budgets: each prose section may declare
`sentences_per_evidence` in its config, and the sentence bounds scale with how many evidence
items (trait facts or events) are available, within the configured floor/ceiling. Everything
else on a page is deterministic (synth/assemble.py, CLAUDE.md file map) — this is the one
generative step in the pipeline.

Inputs:     One gazetteer entity dict, `upto_vol`, an LLMClient (or any object satisfying
            `JSONClient`), Settings (`.traits` for the predicate taxonomy, `.page_outline`
            for which sections are `kind: prose`, their `traits` list, and their sentence-count
            bounds — Phase 13), and an optional `events_conn` (a connection to events.db,
            required only for the `source: "events"` chronology section; absent or None means
            the chronology section is skipped). Reads `graph.db` through the same two sanctioned
            functions Phase 5 already established for evidence: `synth/assemble.py::trait_values_at`
            (which trait values are visible at this cutoff) and `graph/temporal.py::evidence_at`
            (which quotes are safe to print).
Outputs:    `generate_prose(...)` -> CONTRACTS §5 `prose` dict: one
            `{"text": str, "evidence": [para_id, ...]} | None` entry per `page_outline` `kind:
            prose` section key (the base taxonomy has three: `chronology`, `background`,
            `personality`).
            A section is `None`, with NO LLM call made, when the character has no relevant trait
            claims (for background/personality) or no event rows (for chronology) at this cutoff
            -- there is nothing to describe, and asking a model to invent sentences from zero
            evidence is exactly the hallucination risk this project is built to avoid (CLAUDE.md
            §1/§2).
Invariants: - CLAUDE.md §1: every quote reaching a trait-driven prompt is already cutoff-filtered
              by `evidence_at`. The chronology prompt is built from `beat_summary` strings, which
              are paraphrases (not verbatim), never from raw paragraph text.
            - CLAUDE.md §2: trait-driven input is restricted to the TRAIT claims each section's
              own `traits` list names. The chronology section reads from events.db only
              (beat_summary strings), never from trait claims or structured fields.
            - `evidence` in the returned dict is the exact set of para_ids FED to the prompt
              (for trait sections: filtered by `evidence_at`; for chronology: the para_ids of
              event_claims visible at this cutoff) -- deterministic, and impossible for the model
              to falsify.
            - Phase 20: `_adaptive_bounds(section, evidence_count)` computes the actual
              min/max sentence bounds from `sentences_per_evidence` (if configured) and the
              count of evidence items available. The configured `min_sentences`/`max_sentences`
              become the absolute floor/ceiling respectively; sections without
              `sentences_per_evidence` use those values unchanged (backward compatible).
Contract:   docs/CONTRACTS.md §5 `prose`; docs/PROMPTS.md `background_prose`/`personality_prose`.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from ..graph import temporal
from .assemble import trait_values_at, visible_state_at

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]")


# [34] The model's answer that it found nothing ("Ned Clay is not present in the provided events.
# As a result, there are no details available regarding his history") is not prose about the
# character: six frozen Anne pages printed it as History (OPEN_GAPS G8). Treated as no evidence.
_NO_EVIDENCE_ANSWER = re.compile(
    r"\b(?:in|from|within|among) the (?:provided|given|supplied) (?:text|events|facts|passages|"
    r"scene summaries|scenes|summaries|information|evidence|relations)\b"
    r"|\bno (?:further |additional )?(?:details|information) (?:is |are )?(?:available|provided|given)\b",
    re.I)


def _is_no_evidence_answer(text: str) -> bool:
    return bool(_NO_EVIDENCE_ANSWER.search(text))


def _prose_or_none(text: str, para_ids: list[str]) -> dict[str, Any] | None:
    text = text.strip()
    return None if _is_no_evidence_answer(text) else {"text": text, "evidence": sorted(set(para_ids))}


# [34] A section's `evidence` was every paragraph fed to the prompt, and a page printed the first
# six: 63% of the Anne build's assertions linked only to a chapter, and a prose source list named
# the opening paragraphs, not the ones a sentence rests on (OPEN_GAPS G7). Each input line now
# carries a key; the model names the keys it used, and only those lines' paragraphs are cited. A
# key it invents cites nothing; an answer with no valid key keeps every fed paragraph, as before.
CITE_INSTRUCTION = ('Every line you are given starts with a key such as [F1] or [S2]. In "cited", list '
                    'the keys of the lines your text actually rests on, and no others.')


def keyed_lines(entries: list[tuple[str, list[str]]], prefix: str, start: int = 1) -> tuple[list[str], dict[str, list[str]]]:
    """`[(line, para_ids)]` -> (`["[F1] line", ...]`, `{"F1": para_ids}`)."""
    lines: list[str] = []
    keymap: dict[str, list[str]] = {}
    for k, (line, ids) in enumerate(entries, start):
        key = f"{prefix}{k}"
        lines.append(f"[{key}] {line}")
        keymap[key] = list(ids)
    return lines, keymap


def cited_evidence(cited: list[str], keymap: dict[str, list[str]]) -> list[str]:
    """The paragraphs of the keys the model named, or every fed paragraph when it named none."""
    ids = [pid for key in cited for pid in keymap.get(key.strip("[] "), [])]
    return sorted(set(ids or [pid for ids_ in keymap.values() for pid in ids_]))


def _count_sentences(text: str) -> int:
    """Real sentence-boundary count. Phase 25 item 7: the previous validator checked
    `len(text) <= max_sentences * 280` chars as a proxy for sentence count, tuned when a 900-char
    flat cap proved too tight for a wordy local model (see git history) -- but a char budget is
    not a sentence cap, and it let real output silently exceed the configured `max_sentences`
    (a real corpus page: 5 sentences against a 4-sentence cap, 15 against a 12-sentence cap, both
    comfortably inside their char allowance). Counts on `.`/`!`/`?` the same way this bug was
    diagnosed against real data; falls back to 1 for any non-empty text with no terminal
    punctuation rather than 0, so a one-line answer isn't rejected as sentence-less."""
    text = text.strip()
    if not text:
        return 0
    return len(_SENTENCE_RE.findall(text)) or 1


class ProseText(BaseModel):
    """Plain, unbounded `{"text": str}` shape -- used by tests as a canned response; real
    generation always goes through `_prose_schema_for`'s bounded schema instead."""

    text: str = Field(min_length=1)


def _prose_schema_for(max_s: int) -> type[BaseModel]:
    """A `{"text": str}` schema whose validator enforces this section's own adaptive
    `max_sentences` directly by counting real sentences (Phase 25 item 7), replacing the
    char-length proxy `_count_sentences`'s docstring explains. Only the ceiling is a hard gate --
    `min_sentences` stays a prompt instruction, not a validation failure, since undershooting a
    minimum is a quality nit, not the runaway-verbosity bug this exists to catch."""

    class _BoundedProseText(BaseModel):
        text: str = Field(min_length=1)
        cited: list[str] = Field(default_factory=list)   # [34] keys of the lines the text rests on

        @field_validator("text")
        @classmethod
        def _sentence_count_at_most(cls, v: str) -> str:
            # Phase 26 part B: TRIM, do not raise. Raising bought a repair-retry, but a real
            # v1-2 run died on it -- nex-n2.5-pro returned 14 sentences against a 12-sentence cap
            # on all 3 attempts for the same character, killing the whole `wiki synthesize
            # --upto 2` stage over one wordy page. Trimming to the first max_s WHOLE sentences
            # keeps the cap exactly as binding, costs no extra call, and is deterministic.
            if _count_sentences(v) <= max_s:
                return v
            return " ".join(part.strip() for part in _SENTENCE_RE.findall(v)[:max_s])

    return _BoundedProseText


class JSONClient(Protocol):
    def complete_json(
        self, stage: str, prompt: str, schema: type[BaseModel], system: str | None = None
    ) -> BaseModel: ...


def _system_prompt(section: dict[str, Any], min_s: int, max_s: int) -> str:
    subject = section.get("subject") or f"a short {section['title'].lower()} summary"
    source = section.get("source") or "facts"
    return (
        f"You are writing {subject} of a character from a novel, for a wiki page a reader "
        f"is viewing at one specific point in the story. You are given ONLY the {source} "
        f"extracted so far, each with the exact passage it came from. Write {min_s}-{max_s} "
        "sentences, third person, encyclopedic tone, no meta-commentary. Do not mention any "
        "name, number, place, relationship or event that is not stated in the facts you were "
        "given — the reader has not read past this point in the story, and a detail you add or "
        "infer from outside knowledge of the series would spoil it. If the facts describe a "
        "change over time, you may mention that in passing, but never state or imply what "
        "happens after the given facts.\n\n"
        f"{CITE_INSTRUCTION}\n\n"
        'Respond with JSON only: {"text": "...", "cited": ["F1"]}'
    )


def _gather_facts(
    conn: sqlite3.Connection,
    upto_vol: int,
    traits_by_pred: dict[str, list[dict[str, Any]]],
    predicates: tuple[str, ...],
    traits_cfg: dict[str, Any],
) -> list[tuple[str, list[str]]]:
    """One predicate group's fact lines, each with the para_ids of the quotes behind it. Quotes are
    re-filtered here via `evidence_at`, never trusted from a trait's own `claim_ids` blindly —
    Phase 5's spoiler-leak fix applies to this evidence path exactly as much as it does to
    `wiki explain`."""
    entries: list[tuple[str, list[str]]] = []
    for predicate in predicates:
        display = traits_cfg.get(predicate, {}).get("display", predicate)
        for entry in traits_by_pred.get(predicate, []):
            quotes = temporal.evidence_at(conn, entry["claim_ids"], upto_vol)
            if not quotes:
                continue
            cited = "; ".join(f'"{q["quote"]}"' for q in quotes)
            entries.append((f'{display}: {entry["value"]} (since v{entry["since_vol"]}) -- {cited}',
                            [q["para_id"] for q in quotes]))
    return entries


def _adaptive_bounds(section: dict[str, Any], evidence_count: int) -> tuple[int, int]:
    """Compute the actual min/max sentence bounds for a prose section.

    Phase 20: if the section declares `sentences_per_evidence`, the bounds scale with how
    many evidence items are available (trait facts for background/personality; event rows for
    chronology). The configured `min_sentences`/`max_sentences` become the absolute floor and
    ceiling respectively. If `sentences_per_evidence` is absent the function returns the fixed
    configured bounds unchanged — backward compatible with any section that does not declare it.

    Formula (when sentences_per_evidence is set):
        raw     = evidence_count * sentences_per_evidence
        min_s   = max(floor_sentences, round(raw * 0.5))
        max_s   = min(ceil_sentences, max(min_s + 1, ceil(raw)))

    The +1 in max_s ensures min_s < max_s even when raw is very small, giving the model
    at least one degree of freedom.
    """
    floor_s = int(section.get("min_sentences", 2))
    ceil_s = int(section.get("max_sentences", 8))
    spe = section.get("sentences_per_evidence")
    if spe is None:
        return floor_s, ceil_s
    raw = evidence_count * float(spe)
    min_s = min(ceil_s, max(floor_s, round(raw * 0.5)))
    max_s = min(ceil_s, max(min_s + 1, math.ceil(raw)))
    return min_s, max_s


def _generate_one(
    client: JSONClient,
    stage: str,
    section: dict[str, Any],
    canonical: str,
    upto_vol: int,
    entries: list[tuple[str, list[str]]],
) -> dict[str, Any] | None:
    if not entries:
        return None
    # Phase 20: adaptive bounds based on evidence count.
    min_s, max_s = _adaptive_bounds(section, len(entries))
    system = _system_prompt(section, min_s, max_s)
    lines, keymap = keyed_lines(entries, "F")
    prompt = f"Character: {canonical}\nAs known through Volume {upto_vol}.\n\n" + "\n".join(lines)
    result = client.complete_json(stage, prompt, _prose_schema_for(max_s), system=system)
    return _prose_or_none(result.text, cited_evidence(getattr(result, "cited", []), keymap))


def _scene_para_ids(events_conn: sqlite3.Connection, row: Any, upto_vol: int) -> list[str]:
    """[34] A scene's citation: the paragraphs its summary names (`summary_para_ids_json`), else the
    paragraphs of its claims, as before."""
    from ..graph.events import event_claims_at

    named = json.loads(row["summary_para_ids_json"]) if "summary_para_ids_json" in row.keys() else []
    return named or [claim["para_id"] for claim in event_claims_at(events_conn, row["event_id"], upto_vol)]


def _chronology_system_prompt(section: dict[str, Any], min_s: int, max_s: int) -> str:
    subject = section.get("subject") or "a narrative arc for the character"
    return (
        f"You are writing {subject} of a character from a novel, for a wiki page a reader "
        f"is viewing at one specific point in the story. You are given a sequence of scene "
        f"summaries in reading order. Write {min_s}-{max_s} sentences, third person, "
        "encyclopedic tone, no meta-commentary. Describe what actually happened in the order "
        "it happened — do not speculate, do not introduce any name, place, or event not present "
        "in the scene summaries below, and do not imply anything that happens after the last "
        "summary given.\n\n"
        "Stick to the major turning points in THIS character's own arc — a relationship "
        "forming or breaking, a goal won or lost, a defining choice or revelation. Leave out a "
        "minor scene's logistics (exact sums of money, incidental side-character errands, "
        "specific place names in a transaction) unless that detail is itself the point of the "
        "turning point — a wiki history is a life story, not a scene-by-scene recap.\n\n"
        f"{CITE_INSTRUCTION}\n\n"
        'Respond with JSON only: {"text": "...", "cited": ["S1", "S4"]}'
    )


def generate_attribute_prose(
    conn: sqlite3.Connection,
    client: JSONClient,
    entity: dict[str, Any],
    upto_vol: int,
    section: dict[str, Any],
    attributes_cfg: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any] | None:
    """Phase 23 E2: prose generated from ATTRIBUTE rows (`state_at`), not TRAIT rows —
    `generate_prose()`'s `source: "attributes"` dispatch. Parallel to `_gather_facts` (which reads
    `trait_values_at`), but attributes have no `trait_values_at` equivalent since they are not
    traits (`config/extraction.yaml`'s own distinction). Built for the `appearance` section
    (APPEARANCE is `single: false`, so every visible row is its own fact, no history/supersession
    to thread through) — reads `section["traits"]` for which attribute predicates to include (the
    same config key name every other prose section uses for its predicate list, even though the
    values here are ATTRIBUTE, not TRAIT, predicates).

    Returns None (no LLM call) when there is no evidence, same as every other prose function here
    (CLAUDE.md §1/§2)."""
    predicates = tuple(section.get("traits", ()))
    entries: list[tuple[str, list[str]]] = []
    for row in visible_state_at(conn, entity["entity_id"], upto_vol):
        if row["object"] is not None or row["predicate"] not in predicates:
            continue
        claim_ids = json.loads(row["claim_ids_json"])
        quotes = temporal.evidence_at(conn, claim_ids, upto_vol)
        if not quotes:
            continue
        display = attributes_cfg.get(row["predicate"], {}).get("display", row["predicate"])
        cited = "; ".join(f'"{q["quote"]}"' for q in quotes)
        entries.append((f'{display}: {row["value"]} (since v{row["vol_start"]}) -- {cited}',
                        [q["para_id"] for q in quotes]))
    return _generate_one(client, stage, section, entity["canonical"], upto_vol, entries)


def generate_history(
    conn: sqlite3.Connection,
    events_conn: sqlite3.Connection | None,
    client: JSONClient,
    entity: dict[str, Any],
    upto_vol: int,
    section: dict[str, Any],
    traits_cfg: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any] | None:
    """Phase 23 E1: the combined "History" section — `generate_prose()`'s `source: "history"`
    dispatch. Replaces the old separate Chronology (`source: "events"`, event-driven) + Background
    (trait-driven, `traits: [BACKGROUND]`) split with ONE narrative built from both: this section's
    own `traits` (biographical facts, via `_gather_facts`/`trait_values_at`, same as `background`
    used to read) PLUS events.db's scene summaries (via `character_events_at`, same as
    `generate_chronology` reads) — a Fandom-style "History" heading is one flowing biography, not
    two separately-generated blurbs stacked under two headings (docs/vision/PHASE_23.md's
    2026-09-10 entry; every gold file's own `outline` lists "History", never "Chronology").

    Adaptive sentence bounds are computed from the COMBINED evidence count (biographical facts +
    event rows), so a character with only trait facts and no events.db yet (or vice versa) still
    gets a sensible budget instead of one half silently starving the other. Returns None (no LLM
    call) only when BOTH sources are empty — CLAUDE.md's zero-evidence rule, same as every other
    prose function here."""
    from ..graph.events import character_events_at

    traits_by_pred = trait_values_at(conn, entity["entity_id"], upto_vol, traits_cfg)
    facts = _gather_facts(conn, upto_vol, traits_by_pred, tuple(section.get("traits", ())), traits_cfg)

    event_rows = list(character_events_at(events_conn, entity["entity_id"], upto_vol)) if events_conn is not None else []
    scenes = [(f"(v{row['vol']} ch.{row['chapter_idx']}) {row['beat_summary']}",
               _scene_para_ids(events_conn, row, upto_vol)) for row in event_rows]

    if not facts and not scenes:
        return None
    fact_lines, keymap = keyed_lines(facts, "F")
    scene_lines, scene_keys = keyed_lines(scenes, "S")
    keymap.update(scene_keys)
    lines: list[str] = []
    if fact_lines:
        lines += ["Biographical facts:", *fact_lines]
    if scene_lines:
        lines += ["Scene summaries (in reading order):", *scene_lines]

    min_s, max_s = _adaptive_bounds(section, len(facts) + len(scenes))
    system = _chronology_system_prompt(section, min_s, max_s)
    prompt = f"Character: {entity['canonical']}\nAs known through Volume {upto_vol}.\n\n" + "\n".join(lines)
    result = client.complete_json(stage, prompt, _prose_schema_for(max_s), system=system)
    return _prose_or_none(result.text, cited_evidence(getattr(result, "cited", []), keymap))


def generate_chronology(
    events_conn: sqlite3.Connection,
    client: JSONClient,
    entity: dict[str, Any],
    upto_vol: int,
    section: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any] | None:
    """Event-driven chronology prose for one character at one cutoff.

    Reads `character_events_at` from events.db — the spoiler-safe list of events this character
    participated in through `upto_vol`. Returns None (no LLM call) when there are no events.
    The `evidence` list contains the para_ids of every event_claim visible at this cutoff,
    not a model-reported citation — deterministic, impossible to falsify.

    Deliberately does NOT also fold in each event's `core_para_ids` (CONTRACTS §4b.1) — that is
    the paragraphs `beat_summary` was paraphrased from, and it is a genuine, larger source of
    grounding, but a chapter-span-scale slice (tens to 100+ paragraphs per event) rather than a
    precise citation; including it here would bloat every character's stored page evidence list
    into the thousands for any character present in most scenes, without making the field more
    useful for anything that reads it (CLAUDE.md's token discipline). `wiki audit eval`
    (`eval/gold.py::score_citation_entailment`) checks this broader grounding separately, on
    demand, from `events.db` directly — see its own docstring.

    Called by `generate_prose()` when a `page_outline` `kind: prose` section has
    `source: "events"` (the sentinel that routes here instead of the trait path).
    """
    from ..graph.events import character_events_at

    rows = character_events_at(events_conn, entity["entity_id"], upto_vol)
    if not rows:
        return None

    lines, keymap = keyed_lines([(f"(v{row['vol']} ch.{row['chapter_idx']}) {row['beat_summary']}",
                                  _scene_para_ids(events_conn, row, upto_vol)) for row in rows], "S")
    min_s, max_s = _adaptive_bounds(section, len(rows))
    system = _chronology_system_prompt(section, min_s, max_s)
    prompt = (
        f"Character: {entity['canonical']}\nAs known through Volume {upto_vol}.\n\n"
        "Scene summaries (in reading order):\n" + "\n".join(lines)
    )
    result = client.complete_json(stage, prompt, _prose_schema_for(max_s), system=system)
    return _prose_or_none(result.text, cited_evidence(getattr(result, "cited", []), keymap))


def generate_prose(
    conn: sqlite3.Connection,
    client: JSONClient,
    entity: dict[str, Any],
    upto_vol: int,
    settings: Any,
    *,
    stage: str,
    events_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """The CONTRACTS §5 `prose` dict for one character at one cutoff — one entry per
    `settings.page_outline` `kind: prose` section (Phase 13), each fed by that section's own
    `traits` predicate list (for personality) or by events.db (for the old `source: "events"`
    chronology path), or -- Phase 23 E1/E2 -- by BOTH biographical trait facts and events.db
    together (`source: "history"`), or by ATTRIBUTE rows instead of trait rows (`source:
    "attributes"`, `appearance`).

    `stage` names the routing role to call — `"prose"` for an ordinary character,
    `"prose_polish"` for a `--polish` major character (the caller, `wiki synthesize`, decides).
    `events_conn` is the connection to events.db; when None, any section whose dispatch needs it
    (`source: "events"`/`"history"`) degrades to whatever evidence it has without events (or to
    None outright for the events-only `"events"` path), the same "no evidence, no LLM call" rule
    every prose function here follows."""
    traits_by_pred = trait_values_at(conn, entity["entity_id"], upto_vol, settings.traits)

    result: dict[str, Any] = {}
    for section in settings.page_outline:
        if section["kind"] != "prose":
            continue
        source = section.get("source")
        if source == "events":
            # Chronology dispatch: source: "events" routes to the event-driven path.
            result[section["key"]] = (
                generate_chronology(events_conn, client, entity, upto_vol, section, stage=stage)
                if events_conn is not None
                else None
            )
            continue
        if source == "history":
            # Phase 23 E1: combined biographical-facts + events narrative.
            result[section["key"]] = generate_history(
                conn, events_conn, client, entity, upto_vol, section, settings.traits, stage=stage
            )
            continue
        if source == "attributes":
            # Phase 23 E2: attribute-driven prose (e.g. Appearance), not trait-driven.
            result[section["key"]] = generate_attribute_prose(
                conn, client, entity, upto_vol, section, settings.attributes, stage=stage
            )
            continue
        predicates = tuple(section.get("traits", ()))
        entries = _gather_facts(conn, upto_vol, traits_by_pred, predicates, settings.traits)
        result[section["key"]] = _generate_one(client, stage, section, entity["canonical"], upto_vol, entries)
    return result


# ---------------------------------------------------------------------------
# Phase 26 — relationship narration
# ---------------------------------------------------------------------------


_RELATIONSHIP_SYSTEM = """You are writing the Relationships section of a wiki page for a \
character from a novel, for a reader who has read up to one specific point in the story.

For each OTHER character listed, write {min_s}-{max_s} sentences explaining what the \
relationship actually amounts to: how the two met, what they do for each other, how it has \
changed, and any tension in it. A bare label like "friend of" tells a reader nothing — say what \
the bond consists of.

Rules:
- Use ONLY the extracted relations and scene summaries given to you. Do not use outside \
knowledge of this series, and do not describe anything after the stated volume.
- Where the extracted relations conflict (for example both "comrade of" and "enemy of"), say \
what the evidence supports rather than asserting both.
- A relation marked "presumed" is something a character claimed, joked about, or the narration \
hedged. Do not restate it as fact; say who believed or claimed it.
- Third person, encyclopedic tone. No meta-commentary, no second person, no speculation.
- Every relation and scene line starts with a key such as [R3] or [S7]. In each entry's "cited", \
list the keys of the lines that entry's text rests on, and no others.

Respond with JSON only: {{"relationships": [{{"entity_id": "...", "text": "...", "cited": ["R1", "S2"]}}, \
...]}}, one entry per character given, using the exact entity_id strings supplied."""


class _RelationshipNarration(BaseModel):
    entity_id: str
    text: str = Field(min_length=1)
    cited: list[str] = Field(default_factory=list)   # [34] keys of this pair's lines it rests on


class RelationshipProse(BaseModel):
    relationships: list[_RelationshipNarration] = Field(default_factory=list)


def generate_relationship_prose(
    events_conn: sqlite3.Connection | None,
    client: JSONClient,
    entity: dict[str, Any],
    page: dict[str, Any],
    upto_vol: int,
    entities_by_id: dict[str, dict[str, Any]],
    *,
    stage: str,
    min_sentences: int = 2,
    max_sentences: int = 4,
) -> dict[str, dict[str, Any]]:
    """One short paragraph per CHARACTER this entity has a relation with.

    Phase 26, answering the audit's sharpest finding: the shipped v1-2 wiki rendered Holo's
    relationships as 31 flat bullets, 24 of 89 relations across the graph carrying no
    explanation at all, so the page said "Friend of" and "Enemy of" without ever saying what
    either consisted of.

    ONE call per page, not one per pair -- the model sees every relation this character has at
    once, which is what lets it reconcile a pair carrying both "comrade of" and "enemy of"
    instead of narrating each in isolation. It also keeps the cost at one call where a
    per-pair loop would have made roughly eight for a main character.

    Caching is inherited, not new: the result is stored on the page dict, and `synth/cache.py`
    already re-generates a page only when its `claim_set_hash` changes -- and that hash covers
    relation intervals, so a relationship paragraph is regenerated exactly when the relations
    behind it move.

    Returns `{entity_id: {"text", "evidence"}}`, `{}` when there is nothing to describe (no
    LLM call in that case -- CLAUDE.md's zero-evidence rule).
    """
    from ..graph.events import shared_events_at

    relationships = page.get("fields", {}).get("relationships") or []
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in relationships:
        other = row["entity_id"]
        if entities_by_id.get(other, {}).get("type", "CHARACTER") != "CHARACTER":
            continue  # organisations and places render under their own heading
        by_entity.setdefault(other, []).append(row)
    if not by_entity:
        return {}

    lines: list[str] = []
    keymaps: dict[str, dict[str, list[str]]] = {}   # [34] this pair's own line keys -> paragraphs
    n_rel = n_scene = 0
    for other, rows in by_entity.items():
        name = entities_by_id.get(other, {}).get("canonical", other)
        lines.append(f"\n## {name} (entity_id: {other})")
        relation_entries = []
        for row in rows:
            note = f" -- {row['note']}" if row.get("note") else ""
            span = f"since v{row['since_vol']}"
            if not row.get("current", True):
                span += f", ended v{row['vol_end']}" if row.get("vol_end") else ", ended"
            relation_entries.append((f"{row['label']}{note} ({span})", list(row.get("evidence") or [])))
        rel_lines, keymaps[other] = keyed_lines(relation_entries, "R", n_rel + 1)
        n_rel += len(relation_entries)
        lines.extend(f"- {line}" for line in rel_lines)
        if events_conn is not None:
            shared = list(shared_events_at(events_conn, entity["entity_id"], other, upto_vol))[:6]
            if shared:
                scene_lines, scene_keys = keyed_lines(
                    [(f"(v{row['vol']} ch.{row['chapter_idx']}) {row['beat_summary']}",
                      _scene_para_ids(events_conn, row, upto_vol)) for row in shared], "S", n_scene + 1)
                n_scene += len(shared)
                keymaps[other].update(scene_keys)
                lines.append("  Shared scenes:")
                lines.extend(f"  {line}" for line in scene_lines)

    system = _RELATIONSHIP_SYSTEM.format(min_s=min_sentences, max_s=max_sentences)
    prompt = (
        f"Character: {entity['canonical']}\nAs known through Volume {upto_vol}.\n"
        + "\n".join(lines)
    )
    result = client.complete_json(stage, prompt, RelationshipProse, system=system)

    out: dict[str, dict[str, Any]] = {}
    for item in result.relationships:
        if item.entity_id not in by_entity or _is_no_evidence_answer(item.text):
            continue  # an invented or mangled id, or "no information in the provided relations"
        own = keymaps[item.entity_id]
        relation_ids = [pid for key, ids in own.items() if key.startswith("R") for pid in ids]
        cited = [key for key in item.cited if key.strip("[] ") in own]
        out[item.entity_id] = {
            "text": item.text.strip(),
            # the named lines' paragraphs; with none named, this pair's relation evidence, as before
            "evidence": cited_evidence(cited, own) if cited else sorted(set(relation_ids)),
        }
    return out
