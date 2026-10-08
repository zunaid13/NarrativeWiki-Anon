"""[18] Chapter-major scene extraction driver (PROMPTS.md `scene_extract`) — Phase 18's second
pass; as of Phase 22 C1, the PRIMARY source of attribute/trait/relation claims, not merely
additive scene metadata. `extract/claims.py`'s mention-window pass is being demoted to a targeted
top-up (Phase 22 C2, not yet built) rather than removed.

Inputs:     One chapter's paragraph records (`ingest/segment.py::chapter_paragraphs`), that
            chapter's mention records (CONTRACTS §2.2, filtered to this chapter), the full entity
            list (for location/object-type checking, mirroring claims.py's own object-type check),
            Settings, and an LLMClient.
Outputs:    CONTRACTS §3b scene-record dicts (one per chapter span — `core_para_ids` across a
            chapter's spans partition it exactly, see `extract/spans.py`), CONTRACTS §3b.1
            epithet-record dicts, and CONTRACTS §3 claim dicts (Phase 22 C1 — built from each
            span's `participant_facts`, in the identical shape `claims.py::_build_claim` produces
            so both drivers' output can merge into one claim set by `claim_id`). One call to
            `extract_volume_scenes` per volume produces all three.
Invariants: - `beat_summary` is a paraphrase, not verbatim-quote-gated — the one exception in this
              whole pipeline. Every other field (participants, location, quotes.speaker/addressee,
              state_changes.subject/object, epithets.entity_id, participant_facts) resolves ONLY
              against entities already known-mentioned in THIS CHAPTER
              (`extract/resolve.py::resolve_surface`), exactly like claims.py never resolves a
              relation object against the whole gazetteer.
            - A quote/state_change/epithet/fact's citation must be a real paragraph within the
              span and its quote verbatim in that paragraph, or it is dropped — same discipline as
              `claims.py::_build_claim`, reused via `extract/resolve.py`.
            - A SceneRecord is always emitted for every span, even one with no structured facts at
              all — an empty span is proof the model looked and found nothing, not the same as
              never having read the passage. This is the direct fix for the "quiet transition
              scene never sampled by any mention window" gap this phase exists to close, and (C1)
              is exactly why chapter-span coverage reaches every character a mention-window pass
              would have skipped for lacking enough dense mentions to form a window at all.
            - Quotes/epithets are deduped across a chapter's OVERLAPPING spans by (para_id, quote)
              — a paragraph in one span's trailing overlap context that was already reported by
              the earlier span whose CORE covers it is dropped as a duplicate, not re-reported.
              Scene records themselves are never merged across spans (`beat_summary` is per-span).
              Claims ARE merged across spans/chapters by `claim_id` — same fact re-observed in
              overlap context, or independently in two different chapters of the same volume,
              combines its evidence into one claim instead of duplicating it (mirrors
              `claims.py::_merge_claim` exactly).
            - 2026-09-23: the detail prompt asks for EVERY line of dialogue, not "notable"
              dialogue. Measured: a model reading the old wording literally returned 6 quotes per
              span where the passage held ~40, and volume 1 kept quotes for 4 of its 11 speaking
              characters instead of all 11. Selection is a downstream, deterministic job
              (`quotes.max_quotes`); this pass's job is coverage.
            - Phase 22 B1: each span is now TWO calls, not one — a "beat" call (participants,
              location, beat_summary) and a "detail" call (state_changes, quotes, epithets,
              participant_facts as of C1), both built from the identical passage text/known-
              entities prefix so a caching provider reuses that shared prefix. Facts were folded
              into the EXISTING detail call rather than added as a third, so C1 costs zero extra
              calls per span — the call-count reduction Phase C targets comes from demoting
              claims.py's window pass to a sparse top-up (C2), not from this call itself.
Contract:   docs/CONTRACTS.md §3b, §3 (participant_facts -> claim dicts); docs/PROMPTS.md
            `scene_extract`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol

from pydantic import BaseModel

from ..ingest.segment import chapter_paragraphs
from ..llm.parallel import map_calls, workers_for
from .adequacy import neighbourhood_text, passes_evidence_adequacy, prompt_rules
from .resolve import (
    chapter_of_para_id, group_known_entities, normalize_para_id, recover_verbatim, resolve_surface,
    unwrap_quote, within_dialogue,
)
from .schema import ExtractedFact, make_claim_id
from .scene_schema import ExtractedStateChange, SceneBeatResult, SceneDetailResult, make_scene_id
from .spans import Span, build_chapter_spans

STAGE = "scene_extract"

_BEAT_SYSTEM_TEMPLATE = """You are reading one FULL CHAPTER of a novel, \
span by span, to build a scene-level summary — not fact-by-fact extraction. You are given a whole \
passage in reading order, possibly spanning multiple scenes or POV shifts (marked inline with a \
line reading "[SCENE BREAK]").

For this passage, report:

PARTICIPANTS: every character who takes part in this passage — present in the scene, speaking, \
acting, or acted upon — copy each name EXACTLY as it appears in "Known entities in this chapter" \
below, never invent a new name. A character who is only talked about, remembered, written to, or \
named in passing is NOT a participant: the wiki lists this scene on every participant's page.

LOCATION: where this passage takes place, if stated or clearly implied — copy it EXACTLY from \
"Known entities in this chapter", or leave it null if not stated or not in that list.

BEAT_SUMMARY: 2-4 sentences paraphrasing what actually happens in this passage — this is the one \
field in the whole scene-extraction pass that does NOT need a verbatim quote; summarize freely, \
but only describe what this passage itself shows.

SUMMARY_PARA_IDS: the ids of the 1-4 paragraphs that most directly show what your beat_summary \
says, copied exactly from their labels in the passage (e.g. "v01:c03:p0082"). A reader follows \
them to check the summary.

Rules:
- Only name a participant/location that is in "Known entities in this chapter" — never a name \
invented or paraphrased from elsewhere.
- Omitting something this passage does not support is correct — do not force a fit.

Respond with JSON only, shaped exactly like this worked example:

{{"participants": ["Shin", "Spearhead Squadron"], "location": null,
"beat_summary": "Shin briefs the squadron before the next sortie, reminding them of the plan.",
"summary_para_ids": ["v01:c03:p0082", "v01:c03:p0084"], "confidence": 0.85}}

Return {{"participants": [], "location": null, "beat_summary": "...", "summary_para_ids": [], \
"confidence": 0.5}} (with a \
short beat_summary even if nothing else was found) if this passage supports nothing more \
specific."""

_DETAIL_SYSTEM_TEMPLATE = """You are reading one span of a novel a \
second time, now looking ONLY for dialogue, state changes, epithets, and per-character facts — \
participants, location, and the scene summary are captured by a separate pass and are not part of \
this one. You are given the same whole passage in reading order, possibly spanning multiple \
scenes or POV shifts (marked inline with a line reading "[SCENE BREAK]").

For this passage, report:

STATE_CHANGES: any change in a relationship, status, allegiance, or circumstance this passage \
shows happening (not just being restated) — e.g. two characters becoming enemies, a promotion, a \
character learning a fact that changes their situation. Known predicate names, when one fits \
(leave "predicate" null if none does — that is expected and common):
{predicate_lines}

QUOTES: EVERY line of dialogue in this passage — words a character actually says (or radio \
chatter), attributed to its speaker when the passage makes that clear. Report them all, in the \
order they appear; do not pick out only the memorable ones. Which quotes reach a page is decided \
downstream, deterministically, by `quotes.max_quotes` in config/extraction.yaml, not here. \
Never quote narration, description, or a character's \
unspoken thought. The "quote" text is ONLY the words inside the quotation marks — never include \
the narrator's attribution before or after it (e.g. for `"Why?" she shot back, pouting.`, the quote \
is `Why?`, not `"Why?" she shot back`).

EPITHETS: a descriptive nickname used INSTEAD OF a name ("the wisewolf", "the merchant") — only \
report one for an entity already named elsewhere in THIS chapter under "Known entities in this \
chapter"; never invent a new character from a bare epithet.

PARTICIPANT_FACTS: for EVERY named character this passage states or clearly implies something \
new about, one group `{{"subject": <name copied from "Known entities in this chapter">, "facts": \
[...]}}` listing each such fact about THAT character only — never about anyone else. Group facts \
by subject; do not repeat the same subject in two different groups. Three kinds of fact exist:

ATTRIBUTE (a scalar fact about the character):
{attribute_lines}

RELATION (a link to another character or entity actually named in the passage; "object" must be \
copied verbatim from a name that appears in the passage, not paraphrased or described):
{relation_lines}

TRAIT (a short adjective or noun phrase about the character, never a full sentence):
{trait_lines}

EVERY fact in "facts" needs its OWN "para_id" AND its OWN "quote" — the words from that \
paragraph that show the fact, copied VERBATIM, character for character. Never omit either, even \
when several facts cite the same paragraph. A fact without a verbatim quote cannot be used at \
all and is thrown away.

Rules:
- Every state_change/quote/epithet/fact needs its own "para_id", copied EXACTLY from a bracketed \
label in the passage (e.g. "v01:c03:p0084"), and a "quote" copied VERBATIM from that single \
paragraph, with no extra quotation marks of your own added around it.
- A state_change's "note" is REQUIRED even when no predicate fits — it is the one-sentence \
description of what changed.
- Only name a speaker/addressee/epithet referent/fact subject that is in "Known entities in this \
chapter" — never a name invented or paraphrased from elsewhere.
- Omitting something this passage does not support is correct — do not force a fit.
- A fact stated about a character through a pronoun ("he", "she", "they") or an epithet ("the \
merchant", "the wisewolf") still counts as being about them, as long as the passage makes the \
antecedent unambiguous and the configured naming requirement is satisfied (see EVIDENCE \
REQUIREMENTS below). Cite the paragraph that states the fact. If more than one person could \
plausibly be that antecedent, omit the fact \
instead of guessing.
- A fact's "predicate" MUST be spelled EXACTLY as one of the names listed above, nothing else. If \
none of them fits, DO NOT invent a new predicate name — leave the fact out entirely instead.
- "object" is used only for RELATION facts. Leave it out (or null) for ATTRIBUTE/TRAIT facts. \
"value" is used only for ATTRIBUTE/TRAIT facts. Leave it out (or null) for RELATION facts.
- polarity is "asserted" for a stated fact, "presumed" for something only believed or reported \
secondhand OR for a character's own statement that the passage marks as insincere — sarcasm, a \
boast, an exaggeration, a joke, or teasing (e.g. narration like "she teased," "he lied," "in \
jest") — do not take such a line at face value just because it is phrased as a direct statement. \
Narrator HEDGING counts too: "may have", "might have", "perhaps", "seemed to", "as if", \n"whoever", "someone" mark a guess, not a fact — extract it as "presumed" or not at all, \nnever as "asserted". "denied" for an explicit negation. confidence is 0.0-1.0.
- A RELATION needs an actual stated connection (family, command structure, affiliation, an event \
they share) — do NOT invent one just because two people are named in the same scene.

EVIDENCE REQUIREMENTS for every fact in "participant_facts" — a fact that fails any of these is \
discarded before anyone reads it, so it is worth a moment to satisfy them:
{evidence_rules}

Respond with JSON only, shaped exactly like these worked examples (one call reports all fields
that apply; these are shown separately only to demonstrate each shape):

A state change: {{"state_changes": [{{"subject": "Shin", "predicate": null, "from_value": null, \
"to_value": null, "object": "Raiden", \
"note": "Shin and Raiden's easy friendship curdles into open distrust here.", \
"para_id": "v01:c03:p0084", "quote": "He couldn't look at Raiden the same way anymore."}}], \
"quotes": [], "epithets": [], "participant_facts": [], "confidence": 0.85}}

A quote: {{"state_changes": [], "quotes": [{{"speaker": "Shin", "addressee": null, \
"para_id": "v01:c00:p0031", "quote": "Stick to the plan and watch each other's backs."}}], \
"epithets": [], "participant_facts": [], "confidence": 0.85}}

An epithet: {{"state_changes": [], "quotes": [], "epithets": [{{"entity_id_hint": "Lena", \
"text": "the Handler", "para_id": "v01:c03:p0049", \
"quote": "the Handler smiled and tilted her head"}}], "participant_facts": [], "confidence": 0.85}}

Participant facts: {{"state_changes": [], "quotes": [], "epithets": [], "participant_facts": \
[{{"subject": "Shin", "facts": [{{"predicate": "AFFILIATED_WITH", "kind": "relation", \
"object": "Spearhead Squadron", "value": null, "qualifier": "captain", \
"para_id": "v01:c00:p0031", "quote": "the Spearhead Squadron's captain, code name Undertaker", \
"confidence": 0.92, "polarity": "asserted"}}]}}], "confidence": 0.85}}

Return {{"state_changes": [], "quotes": [], "epithets": [], "participant_facts": [], \
"confidence": 0.5}} if this passage supports nothing more specific."""


class JSONClient(Protocol):
    def complete_json(
        self,
        stage: str,
        prompt: str,
        schema: type[BaseModel],
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> BaseModel: ...

    def profile_for(self, stage: str) -> Any: ...


# A scene response carries more sub-lists than claim_extract's single "facts" array — expect this
# to need the same kind of empirical retuning claim_extract already needed (docs/handover/
# PHASE_3.md), not designed in advance. Starting from claim_extract's own tuned values plus extra
# headroom for the wider response shape. Phase 22 B1 split one combined budget into two smaller,
# purpose-fit ones: the beat call's response is much shorter (three scalar-ish fields) than the
# detail call's (three whole sub-lists plus the worked examples now in its own system prompt).
# Phase 22 C1 adds a fourth sub-list (participant_facts, usually the largest one per span since it
# covers every participant) to the detail call -- raised from 6144 accordingly.
# Phase 23 Part F (2026-09-11): repeat_penalty 1.3, inherited from claim_extract's own tuning
# (chosen there to stop a degenerate repeat-loop, PHASE_3.md), produced corrupted detail-call
# output against real Ollama evidence -- random CJK/Hangul fragments spliced into English subject/
# quote fields (e.g. "the wisewolf smiled" preceded by garbage tokens). A repeat_penalty this
# aggressive is a known way to push a small quantized model into low-probability off-vocabulary
# tokens once it runs out of natural continuations to penalize away from, which the detail call's
# many optional free-text fields (subject, speaker, addressee, value) hit far more often than
# claim_extract's narrower schema does. Lowered to 1.1 for the detail call specifically; the beat
# call (three scalar-ish fields, first cause of the original degenerate-loop bug) keeps 1.3.
# 2026-09-23: temperature pinned low here too -- see the note on claims.py
# ::_GENERATION_OPTIONS. This pass is the one whose run-to-run swing was measured.
_BEAT_GENERATION_OPTIONS = {"num_predict": 2048, "repeat_penalty": 1.3, "temperature": 0.1}
# 2026-09-23: 8192 -> 24576. Once the prompt asks for EVERY line of dialogue, a dense span's
# answer reaches ~7k tokens, and 6 of 42 detail responses were being cut off mid-JSON. The
# truncated tail is always `participant_facts` -- it is the LAST key in the response -- so the
# cap was silently deleting the facts this call exists to produce while leaving the quotes
# before them intact. Output tokens are billed only when generated, so a bigger ceiling costs
# nothing on the spans that do not need it.
_DETAIL_GENERATION_OPTIONS = {"num_predict": 24576, "repeat_penalty": 1.1, "temperature": 0.1}


def _vocab_lines(spec: dict[str, Any]) -> str:
    lines = []
    for name, meta in spec.items():
        note = meta.get("note", "")
        lines.append(f"- {name} — {note}" if note else f"- {name}")
    return "\n".join(lines) or "(none defined)"


def _beat_system_prompt(settings) -> str:
    return _BEAT_SYSTEM_TEMPLATE


def _detail_system_prompt(settings) -> str:
    predicate_lines = _vocab_lines({**settings.attributes, **settings.relations})
    return _DETAIL_SYSTEM_TEMPLATE.format(
        predicate_lines=predicate_lines,
        attribute_lines=_vocab_lines(settings.attributes),
        relation_lines=_vocab_lines(settings.relations),
        trait_lines=_vocab_lines(settings.traits),
        # Generated from the same config this module's own gates read -- adequacy.prompt_rules().
        evidence_rules=prompt_rules(settings),
    )


def _user_prefix(
    vol: int,
    chapter_idx: int,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> str:
    """Shared by both the beat and detail calls for the same span — identical text end to end so a
    caching provider (or Ollama's own context reuse) sees the same prefix twice rather than paying
    for it from scratch on each of the two calls (Phase 22 B1)."""
    known_ids = sorted({m["entity_id"] for m in chapter_mentions})
    known = group_known_entities(known_ids, entities_by_id) or "(none known yet)"
    return (
        f"Volume {vol}, chapter {chapter_idx}.\n"
        f"Known entities in this chapter: {known}\n\n"
        f"Passage (each paragraph labeled with its id):\n{span.text}\n\n"
    )


def _beat_user_prompt(
    vol: int,
    chapter_idx: int,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> str:
    return _user_prefix(vol, chapter_idx, span, chapter_mentions, entities_by_id) + (
        "Report participants, location, and a beat summary for this passage."
    )


def _detail_user_prompt(
    vol: int,
    chapter_idx: int,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> str:
    return _user_prefix(vol, chapter_idx, span, chapter_mentions, entities_by_id) + (
        "Report state changes, quotes, and epithets for this passage."
    )


def _lookup_state_predicate(predicate: str, settings) -> str | None:
    """State changes only resolve against attribute/relation predicates — a relationship or
    status transition, not a trait — and fall back to null (kept as raw `note` text) rather than
    dropping the whole entry when the model's guess doesn't match a configured name."""
    if predicate in settings.attributes or predicate in settings.relations:
        return predicate
    return None


def _resolve_optional(text: str | None, candidates: list[dict[str, Any]], passage_text: str) -> str | None:
    if not text:
        return None
    return resolve_surface(text, candidates, passage_text)


def _lookup_predicate(predicate: str, settings) -> tuple[dict[str, Any] | None, str]:
    """Same lookup as `claims.py::_lookup_predicate` -- kept as a local copy rather than an
    import so `scenes.py` stays self-contained (it never imports from `claims.py`; Phase 22 C1
    is deliberately making claims.py the dependent/secondary pass, not the other way round)."""
    if predicate in settings.attributes:
        return settings.attributes[predicate], "attribute"
    if predicate in settings.relations:
        return settings.relations[predicate], "relation"
    if predicate in settings.traits:
        return settings.traits[predicate], "trait"
    return None, ""


def _build_state_change(
    sc: ExtractedStateChange,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    settings,
    max_quote_chars: int,
    drops: Counter[str],
) -> dict[str, Any] | None:
    subject_id = resolve_surface(sc.subject, chapter_mentions, span.text)
    if subject_id is None:
        drops["state_change_subject_unresolved"] += 1
        return None

    para_id = normalize_para_id(sc.para_id, span.para_ids)
    if para_id is None:
        drops["citation_outside_span"] += 1
        return None
    paragraph = records_by_id.get(para_id)
    if paragraph is None:
        drops["citation_outside_span"] += 1
        return None

    quote = recover_verbatim(unwrap_quote(sc.quote.strip()), paragraph["text"])
    if not quote or len(quote) > max_quote_chars:
        # [31] Separate labels: an over-long quote is verbatim, and was hidden under this one.
        drops["quote_too_long" if quote else "quote_not_verbatim"] += 1
        return None

    predicate = _lookup_state_predicate(sc.predicate.strip().upper(), settings) if sc.predicate else None
    object_id = _resolve_optional(sc.object, chapter_mentions, span.text)

    return {
        "subject": subject_id,
        "predicate": predicate,
        "from_value": (sc.from_value or "").strip() or None,
        "to_value": (sc.to_value or "").strip() or None,
        "object": object_id,
        "note": sc.note.strip(),
        "evidence": [{"para_id": para_id, "print_page": paragraph.get("print_page"), "quote": quote}],
    }


def _build_quote(
    q,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    max_quote_chars: int,
    drops: Counter[str],
) -> dict[str, Any] | None:
    para_id = normalize_para_id(q.para_id, span.para_ids)
    if para_id is None:
        drops["citation_outside_span"] += 1
        return None
    paragraph = records_by_id.get(para_id)
    if paragraph is None:
        drops["citation_outside_span"] += 1
        return None

    quote = recover_verbatim(unwrap_quote(q.quote.strip()), paragraph["text"])
    if not quote or len(quote) > max_quote_chars:
        # [31] Separate labels: an over-long quote is verbatim, and was hidden under this one.
        drops["quote_too_long" if quote else "quote_not_verbatim"] += 1
        return None

    # Phase 25 item 4: `speech` (ingest/epub.py's dialogue/para_raid/machine/narration
    # classification, CLAUDE.md §3 trap 4) is the exact signal that tells a real spoken line
    # apart from narration the model mistook for one — the "speech channel...discarded
    # everywhere downstream" gap the phase's own scope note named. A quote cited from a
    # "narration" paragraph is a hallucinated attribution, not a verbatim-check failure (the
    # substring genuinely IS in the paragraph — it's just not something anyone said).
    # [30] ...but `speech` is per PARAGRAPH, set by how it opens, so `Momonga ... said, "Don't
    # worry about it"` is "narration" and its real dialogue was dropped: 234 quotes in Overlord
    # v1, 99 in Spice and Wolf v1. A quote inside the paragraph's own quotation marks is spoken.
    if paragraph.get("speech") == "narration" and not within_dialogue(quote, paragraph["text"]):
        drops["quote_not_spoken"] += 1
        return None

    return {
        "speaker": _resolve_optional(q.speaker, chapter_mentions, span.text),
        "addressee": _resolve_optional(q.addressee, chapter_mentions, span.text),
        "para_id": para_id,
        "print_page": paragraph.get("print_page"),
        "quote": quote,
    }


def _build_epithet(
    e,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    max_quote_chars: int,
    source: str,
    drops: Counter[str],
) -> dict[str, Any] | None:
    entity_id = resolve_surface(e.entity_id_hint, chapter_mentions, span.text)
    if entity_id is None:
        drops["epithet_referent_unresolved"] += 1
        return None

    para_id = normalize_para_id(e.para_id, span.para_ids)
    if para_id is None:
        drops["citation_outside_span"] += 1
        return None
    paragraph = records_by_id.get(para_id)
    if paragraph is None:
        drops["citation_outside_span"] += 1
        return None

    quote = recover_verbatim(unwrap_quote(e.quote.strip()), paragraph["text"])
    if not quote or len(quote) > max_quote_chars:
        # [31] Separate labels: an over-long quote is verbatim, and was hidden under this one.
        drops["quote_too_long" if quote else "quote_not_verbatim"] += 1
        return None

    text = e.text.strip()
    if not text:
        drops["empty_epithet_text"] += 1
        return None

    return {
        "entity_id": entity_id,
        "text": text,
        "vol": span.vol,
        "chapter_idx": span.chapter_idx,
        "para_id": para_id,
        "print_page": paragraph.get("print_page"),
        "quote": quote,
        "confidence": e.confidence,
        "source": source,
    }


def _build_participant_fact(
    subject_id: str,
    fact: ExtractedFact,
    span: Span,
    chapter_mentions: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    settings,
    source: str,
    drops: Counter[str],
) -> dict[str, Any] | None:
    """One `ParticipantFacts` entry -> a CONTRACTS §3 claim dict, same validation discipline as
    `claims.py::_build_claim` (Phase 22 C1), just resolving against this chapter's known entities
    instead of one mention window's."""
    behaviour = settings.extraction_behaviour
    min_to_keep = float(behaviour.get("confidence", {}).get("min_to_keep", 0.5))
    max_quote_chars = int(behaviour.get("max_quote_chars", 240))
    drop_unresolved = bool(behaviour.get("drop_unresolved_entities", True))

    predicate = fact.predicate.strip().upper()
    spec, true_kind = _lookup_predicate(predicate, settings)
    if spec is None:
        drops["unknown_predicate"] += 1
        return None

    para_id = normalize_para_id(fact.para_id, span.para_ids)
    if para_id is None:
        drops["citation_outside_span"] += 1
        return None
    paragraph = records_by_id.get(para_id)
    if paragraph is None:
        drops["citation_outside_span"] += 1
        return None

    quote = recover_verbatim(unwrap_quote(fact.quote.strip()), paragraph["text"])
    if not quote or len(quote) > max_quote_chars:
        # [31] Separate labels: an over-long quote is verbatim, and was hidden under this one.
        drops["quote_too_long" if quote else "quote_not_verbatim"] += 1
        return None

    if fact.confidence < min_to_keep:
        drops["low_confidence"] += 1
        return None

    object_id: str | None = None
    value: str | None = None
    if true_kind == "relation":
        object_id = resolve_surface(fact.object or "", chapter_mentions, span.text)
        if object_id is None:
            if drop_unresolved:
                drops["unresolved_object"] += 1
                return None
        else:
            object_types = spec.get("object_types")
            if object_types and entities_by_id.get(object_id, {}).get("type") not in object_types:
                drops["object_type_mismatch"] += 1
                return None
        object_norm = object_id or ""
    else:
        value = (fact.value or "").strip()
        if not value:
            drops["empty_value"] += 1
            return None
        object_norm = value.lower()

    if not passes_evidence_adequacy(
        subject_entity=entities_by_id.get(subject_id),
        object_entity=entities_by_id.get(object_id) if object_id else None,
        true_kind=true_kind,
        predicate_spec=spec,
        paragraph_text=paragraph["text"],
        quote=quote,
        behaviour=behaviour,
        drops=drops,
        qualifier=fact.qualifier,
        subject_context_text=neighbourhood_text(
            para_id, span.para_ids, records_by_id,
            int(behaviour.get("evidence_adequacy", {}).get("subject_named_within_paragraphs", 0) or 0),
        ),
    ):
        return None

    qualifier = (fact.qualifier or "").strip() or None
    return {
        "claim_id": make_claim_id(
            subject_id, predicate, object_norm, span.vol,
            polarity=fact.polarity, qualifier=qualifier,
        ),
        "subject": subject_id,
        "predicate": predicate,
        "kind": true_kind,
        "object": object_id,
        "value": value,
        "qualifier": qualifier,
        "first_vol": span.vol,
        "chapter_idx": paragraph["chapter_idx"],
        "evidence": [{"para_id": para_id, "print_page": paragraph.get("print_page"), "quote": quote}],
        "confidence": fact.confidence,
        "polarity": fact.polarity,
        "source": source,
    }


def _merge_claim(existing: dict[str, Any], new: dict[str, Any]) -> None:
    """Mirrors `claims.py::_merge_claim` exactly -- same fact re-observed (overlapping spans, or
    independently in a second chapter of the same volume) combines evidence instead of
    duplicating the claim."""
    seen = {(e["para_id"], e["quote"]) for e in existing["evidence"]}
    for e in new["evidence"]:
        key = (e["para_id"], e["quote"])
        if key not in seen:
            existing["evidence"].append(e)
            seen.add(key)
    existing["confidence"] = max(existing["confidence"], new["confidence"])


def _build_scene_record(
    span: Span,
    beat: SceneBeatResult,
    detail: SceneDetailResult,
    chapter_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    settings,
    source: str,
    drops: Counter[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    max_quote_chars = int(settings.extraction_behaviour.get("max_quote_chars", 240))

    participants: list[str] = []
    for text in beat.participants:
        entity_id = resolve_surface(text, chapter_mentions, span.text)
        if entity_id is None:
            drops["participant_unresolved"] += 1
            continue
        if entity_id not in participants:
            participants.append(entity_id)

    location: str | None = None
    if beat.location:
        loc_id = resolve_surface(beat.location, chapter_mentions, span.text)
        if loc_id is None:
            drops["location_unresolved"] += 1
        elif entities_by_id.get(loc_id, {}).get("type") != "LOCATION":
            drops["location_type_mismatch"] += 1
        else:
            location = loc_id

    state_changes = [
        sc for f in detail.state_changes
        if (sc := _build_state_change(f, span, chapter_mentions, records_by_id, settings, max_quote_chars, drops))
        is not None
    ]
    quotes = [
        q for f in detail.quotes
        if (q := _build_quote(f, span, chapter_mentions, records_by_id, max_quote_chars, drops)) is not None
    ]
    epithets = [
        e for f in detail.epithets
        if (e := _build_epithet(f, span, chapter_mentions, records_by_id, max_quote_chars, source, drops))
        is not None
    ]

    claims: list[dict[str, Any]] = []
    for group in detail.participant_facts:
        subject_id = resolve_surface(group.subject, chapter_mentions, span.text)
        if subject_id is None:
            drops["participant_fact_subject_unresolved"] += len(group.facts)
            continue
        for fact in group.facts:
            claim = _build_participant_fact(
                subject_id, fact, span, chapter_mentions, records_by_id, entities_by_id, settings, source, drops
            )
            if claim is not None:
                claims.append(claim)

    scene = {
        "scene_id": make_scene_id(span.vol, span.chapter_idx, span.span_index),
        "vol": span.vol,
        "chapter_idx": span.chapter_idx,
        "span_index": span.span_index,
        "para_ids": list(span.para_ids),
        "core_para_ids": list(span.core_para_ids),
        "participants": participants,
        "location": location,
        "beat_summary": beat.beat_summary.strip(),
        # [34] only ids of this span's own paragraphs, repaired like every other copied id
        "summary_para_ids": list(dict.fromkeys(
            pid for raw in beat.summary_para_ids[:4] if (pid := normalize_para_id(raw.strip(), span.para_ids)))),
        "state_changes": state_changes,
        "quotes": quotes,
        # Two separate calls now report two separate confidences (Phase 22 B1); the lower of the
        # two is the scene's overall confidence, same conservative posture as everywhere else in
        # this pipeline (drop/discount rather than average away a low-confidence half).
        "confidence": min(beat.confidence, detail.confidence),
        "source": source,
    }
    return scene, epithets, claims


def extract_chapter_scenes(
    vol: int,
    chapter_idx: int,
    chapter_records: list[dict[str, Any]],
    chapter_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    settings,
    client: JSONClient,
    beat_system: str | None = None,
    detail_system: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    """One chapter's scene + epithet + claim records. Builds chapter spans, makes TWO calls per
    span (Phase 22 B1 — beat, then detail), resolves/validates every returned fact, dedups
    quotes/epithets across overlapping spans, and merges claims by `claim_id` across spans (Phase
    22 C1). Returns (scene_records, epithet_records, claims, drop_reasons)."""
    span_cfg = settings.extraction_behaviour.get("scene", {})
    spans = build_chapter_spans(vol, chapter_idx, chapter_records, span_cfg)
    records_by_id = {r["para_id"]: r for r in chapter_records}

    beat_system_prompt = beat_system or _beat_system_prompt(settings)
    detail_system_prompt = detail_system or _detail_system_prompt(settings)
    source = str(client.profile_for(STAGE))

    scene_records: list[dict[str, Any]] = []
    epithet_records: list[dict[str, Any]] = []
    claims: dict[str, dict[str, Any]] = {}
    drops: Counter[str] = Counter()
    seen_quotes: set[tuple[str, str]] = set()
    seen_epithets: set[tuple[str, str, str]] = set()

    for span in spans:
        beat_prompt = _beat_user_prompt(vol, chapter_idx, span, chapter_mentions, entities_by_id)
        beat = client.complete_json(
            STAGE, beat_prompt, SceneBeatResult, system=beat_system_prompt, options=_BEAT_GENERATION_OPTIONS
        )
        detail_prompt = _detail_user_prompt(vol, chapter_idx, span, chapter_mentions, entities_by_id)
        detail = client.complete_json(
            STAGE, detail_prompt, SceneDetailResult, system=detail_system_prompt, options=_DETAIL_GENERATION_OPTIONS
        )
        scene, epithets, span_claims = _build_scene_record(
            span, beat, detail, chapter_mentions, entities_by_id, records_by_id, settings, source, drops
        )

        kept_quotes = []
        for q in scene["quotes"]:
            key = (q["para_id"], q["quote"])
            if key in seen_quotes:
                drops["duplicate_quote_across_spans"] += 1
                continue
            seen_quotes.add(key)
            kept_quotes.append(q)
        scene["quotes"] = kept_quotes
        scene_records.append(scene)

        for ep in epithets:
            key = (ep["entity_id"], ep["para_id"], ep["text"])
            if key in seen_epithets:
                drops["duplicate_epithet_across_spans"] += 1
                continue
            seen_epithets.add(key)
            epithet_records.append(ep)

        for claim in span_claims:
            existing = claims.get(claim["claim_id"])
            if existing is None:
                claims[claim["claim_id"]] = claim
            else:
                _merge_claim(existing, claim)

    return scene_records, epithet_records, sorted(claims.values(), key=lambda c: c["claim_id"]), drops


def extract_volume_scenes(
    vol: int,
    records: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    all_entities: list[dict[str, Any]],
    settings,
    client: JSONClient,
    on_progress=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    """Every chapter's scene + epithet + claim records for one volume (Phase 22 C1). Claims are
    merged by `claim_id` across chapters too — the same fact can be independently (re)stated in
    two different chapters of the same volume, exactly like `claims.py::extract_character_volume`
    merges repeat observations across windows. `on_progress(i, total, chapter_idx, n_scenes)` is
    called after each chapter, if given, so the CLI can print a running count."""
    entities_by_id = {e["entity_id"]: e for e in all_entities}
    beat_system_prompt = _beat_system_prompt(settings)
    detail_system_prompt = _detail_system_prompt(settings)

    mentions_by_chapter: dict[int, list[dict[str, Any]]] = {}
    for m in mentions:
        mentions_by_chapter.setdefault(chapter_of_para_id(m["para_id"]), []).append(m)

    chapter_indices = sorted({r["chapter_idx"] for r in records})
    all_scenes: list[dict[str, Any]] = []
    all_epithets: list[dict[str, Any]] = []
    all_claims: dict[str, dict[str, Any]] = {}
    total_drops: Counter[str] = Counter()
    total = len(chapter_indices)

    def extract_one(chapter_idx: int):
        return extract_chapter_scenes(
            vol, chapter_idx, chapter_paragraphs(records, chapter_idx),
            mentions_by_chapter.get(chapter_idx, []), entities_by_id, settings, client,
            beat_system=beat_system_prompt, detail_system=detail_system_prompt,
        )

    def collect(i: int, chapter_idx: int, result) -> None:
        scenes, epithets, claims, drops = result
        all_scenes.extend(scenes)
        all_epithets.extend(epithets)
        for claim in claims:
            existing = all_claims.get(claim["claim_id"])
            if existing is None:
                all_claims[claim["claim_id"]] = claim
            else:
                _merge_claim(existing, claim)
        total_drops.update(drops)
        if on_progress is not None:
            on_progress(i + 1, total, chapter_idx, len(scenes))

    # Chapters are independent; SPANS INSIDE a chapter are not (quote/epithet dedup and claim
    # merging are order-dependent), so parallelism stops at the chapter boundary. Merge order is
    # unchanged because map_calls collects in input order.
    map_calls(extract_one, chapter_indices, workers_for(client, STAGE), on_result=collect)
    return all_scenes, all_epithets, sorted(all_claims.values(), key=lambda c: c["claim_id"]), total_drops
