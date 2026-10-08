"""[30] Infobox pass: one long-context call per character per volume for the infobox attributes the
windowed `claim_extract` pass leaves empty (PROMPTS.md `infobox_extract`).

Why a separate pass. On the v1-2 graph, GENDER was filled on 2 of 14 character pages, ORIGIN on 1,
AGE on 2 (MEASUREMENTS section 34). These are the fields every fandom infobox carries, and the
windowed pass misses them structurally: each ~4k-token window is judged alone, and a fact like a
character's gender is never *stated* in any one window -- it is carried by pronouns across the
whole volume. Long-context models are markedly better at locating scattered literary evidence
across a full novel than retrieval over fragments (Literary Evidence Retrieval via Long-Context
LMs, ACL 2025: 62.5% vs 4.5% for embedding retrieval), so this pass shows the model the
character's whole dossier for the volume in one request and asks only for these few fields,
each bound to a verbatim quote (evidence-bound extraction).

Additive by construction: the `claim_extract` prompt is untouched (editing it re-rolls every claim,
MEASUREMENTS section 30), and the output goes to its own `infobox_vNN.jsonl`.

Inputs:     Same as `extract/claims.py::extract_volume`.
Outputs:    CONTRACTS section 3 claims, `source` = "infobox:<profile>". Produced by
            `claims.extract_character_volume` itself, so every gate there applies unchanged:
            verbatim quote, cited paragraph inside what the model was shown, confidence floor,
            evidence adequacy (subject named nearby, minimum quote length).
Invariants: - Spoiler safety: a volume's dossier is that volume's paragraphs only, so every claim's
              `first_vol` is the volume that supplied its evidence, as for any other claim.
            - Only the attributes listed in `extraction.infobox.attributes` are accepted; anything
              else the model returns is dropped as `not_infobox_attribute`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..llm.parallel import map_calls, workers_for
from .adequacy import prompt_rules
from .claims import JSONClient, _vocab_lines, extract_character_volume

STAGE = "infobox_extract"

_SYSTEM_TEMPLATE = """You are filling in the infobox of ONE named character's wiki page, from \
passages of a novel. The passages are every part of one volume where \
this character appears, in reading order. Extract ONLY these fields, and only about the named \
character:

{attribute_lines}

Rules:
- Each field needs a "para_id" copied EXACTLY from a bracketed label, and a "quote" copied \
VERBATIM from that single paragraph (no added quotation marks) that shows the value.
- GENDER: "female" or "male". The quote MUST contain both the character's name (or a listed \
alias) AND a gendered word referring to them -- e.g. "Lena shook her head", "Shin said, and \
he laughed". Pick such a sentence even if a vaguer one ("said the girl") appears earlier; a quote \
without the name is discarded, and a quote without a gendered word does not show gender. Never \
infer gender from a name alone.
- AGE: a number or range only when the text states it or states something that fixes it.
- ORIGIN: where the character was born or comes from (homeland, hometown). NOT where they \
currently live, work, trade or are visiting.
- If the passages do not support a field, leave it out. Omitting is correct; guessing is not.
- polarity is "asserted", or "presumed" when the text only reports a belief or a claim the \
passage marks as untrue or uncertain. confidence is 0.0-1.0.
- You may give up to 3 entries per field, each with a different quote, so that at least one \
meets the evidence requirements below. Every entry for a field must give the same value.

EVIDENCE REQUIREMENTS -- a fact that fails any of these is discarded:
{evidence_rules}

Respond with JSON only, shaped exactly like this:
{{"facts": [
  {{"predicate": "GENDER", "kind": "attribute", "object": null, "value": "female", \
"qualifier": null, "para_id": "v01:c02:p0040", "quote": "She pulled her hood lower over her ears", \
"confidence": 0.95, "polarity": "asserted"}}
]}}

Return {{"facts": []}} if none of these fields is supported."""


_BACKSTORY_TEMPLATE = """You are writing the background of ONE named character's wiki page, from \
passages of a novel. The passages are every part of one volume where \
this character appears, with nearby context, in reading order. Extract ONLY this kind of fact, \
and only about the named character:

{attribute_lines}

What counts: the character's life BEFORE the story's present, or lasting facts about their past \
that the text reveals -- upbringing, family, where they lived or grew up, earlier work, how they \
came to be where they are, a formative event. What does NOT count: things they do during the \
story's present, personality, appearance, or their current job.

Rules:
- "value" is a short phrase of at most 20 words, not a sentence, in the PAST tense, describing \
something that happened or was true before the story's present: "raised in an almshouse attached \
to an abbey", "apprenticed to a relative at twelve". If it cannot be phrased that way, it is not \
backstory -- leave it out.
- Never return: an age; a current job, title, employer or backer; who they work with now; health \
complaints; where they are from, as a bare place name (a separate field covers that).
- The character's own first-person account of their past counts; so does another character or \
the narrator stating it as fact. A rumour, a boast or a guess is "presumed", not "asserted".
- Each fact needs a "para_id" copied EXACTLY from a bracketed label and a "quote" copied VERBATIM \
from that single paragraph (no added quotation marks).
- Give each distinct fact once, at most 8 facts. Omitting is correct; guessing is not.

EVIDENCE REQUIREMENTS -- a fact that fails any of these is discarded:
{evidence_rules}

Respond with JSON only, shaped exactly like this:
{{"facts": [
  {{"predicate": "BACKGROUND", "kind": "trait", "object": null, "value": "set out on his own at \
eighteen", "qualifier": null, "para_id": "v01:c01:p0006", "quote": "This year made the seventh \
since he'd struck out on his own", "confidence": 0.9, "polarity": "asserted"}}
]}}

Return {{"facts": []}} if the passages reveal nothing about this character's past."""

# One long-context pass per entry. `fields` names the predicates it may return, from the config
# block of the same name; `vocab` is where those predicates are declared. `name_only_dossier`:
# see `_dossier_window_cfg`. Separate stages, prompts and output files, so re-running one pass
# never re-rolls the other's cached answers.
PASSES: dict[str, dict[str, Any]] = {
    "infobox": {"stage": STAGE, "template": _SYSTEM_TEMPLATE, "vocab": "attributes",
                "name_only_dossier": True},
    "backstory": {"stage": "backstory_extract", "template": _BACKSTORY_TEMPLATE, "vocab": "traits",
                  "name_only_dossier": False},
}


def pass_config(settings, name: str = "infobox") -> dict[str, Any]:
    return settings.extraction_behaviour.get(name, {}) or {}


def system_prompt(settings, name: str = "infobox") -> str:
    spec = PASSES[name]
    vocab = getattr(settings, spec["vocab"])
    wanted = pass_config(settings, name).get("fields", [])
    return spec["template"].format(
        attribute_lines=_vocab_lines({k: vocab[k] for k in wanted}),
        evidence_rules=prompt_rules(settings),
    )


def _dossier_window_cfg(settings, name: str = "infobox") -> dict[str, Any]:
    """One request per character-volume: every evidence window, packed. `claims.py`'s overflow
    splitter still halves a dossier that exceeds a profile's declared context, so a small local
    fallback degrades to several calls rather than failing.

    The infobox pass sends only the paragraphs that NAME the character (radius 0), measured on v1:
    given Holo's 90k-token dossier with the windowed pass's +-4 context, flash-lite answered GENDER
    from "said the girl" -- her first appearance, before she is named -- three runs out of three,
    even when told to prefer a named sentence, and the subject-named gate rightly dropped it each
    time. The backstory pass keeps the context: a past is usually told in first-person dialogue
    ("I helped at an almshouse..."), a paragraph that never names its speaker, and the
    subject-named gate needs the neighbouring "said Norah" to accept it."""
    cfg = dict(settings.extraction_behaviour.get("window", {}))
    if PASSES[name]["name_only_dossier"]:
        cfg["context_paragraphs_before"] = cfg["context_paragraphs_after"] = 0
    cfg["max_windows_per_request"] = 10_000
    cfg["max_request_tokens"] = int(pass_config(settings, name).get("max_request_tokens", 150_000))
    return cfg


def extract_volume(
    characters: list[dict[str, Any]],
    vol: int,
    records: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    all_entities: list[dict[str, Any]],
    settings,
    client: JSONClient,
    on_progress=None,
    name: str = "infobox",
) -> tuple[list[dict[str, Any]], Counter[str]]:
    entities_by_id = {e["entity_id"]: e for e in all_entities}
    stage = PASSES[name]["stage"]
    wanted = set(pass_config(settings, name).get("fields", []))
    system = system_prompt(settings, name)
    window_cfg = _dossier_window_cfg(settings, name)
    all_claims: list[dict[str, Any]] = []
    total_drops: Counter[str] = Counter()

    def one(entity: dict[str, Any]):
        return extract_character_volume(
            entity, vol, records, mentions, entities_by_id, settings, client,
            system=system, max_windows=10_000, stage=stage, window_cfg=window_cfg,
        )

    def collect(i: int, entity: dict[str, Any], result) -> None:
        claims, drops = result
        kept = [c for c in claims if c["predicate"] in wanted]
        drops[f"not_{name}_field"] += len(claims) - len(kept)
        all_claims.extend(kept)
        total_drops.update(drops)
        if on_progress is not None:
            on_progress(i + 1, len(characters), entity, len(kept))

    map_calls(one, characters, workers_for(client, stage), on_result=collect)
    return all_claims, total_drops
