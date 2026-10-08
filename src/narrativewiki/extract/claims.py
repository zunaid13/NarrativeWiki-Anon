"""[3] Per-character claim extraction driver. THE BULK STAGE (PROMPTS.md `claim_extract`).

Inputs:     One CHARACTER entity, that volume's paragraph records, that volume's mention index
            (CONTRACTS §2.2), the full entity list (for relation-object type checking), Settings,
            and an LLMClient.
Outputs:    CONTRACTS §3 claim dicts, one call to `extract_volume` per volume producing every
            character's claims for that volume.
Invariants: - `subject` is never resolved by the model — it is the entity_id being processed, named
              explicitly in the prompt (PROMPTS.md: "one subject per call").
            - `object` (relation only) is resolved against the mentions already indexed for this
              window, never against the whole gazetteer — a name the model invents that was not
              actually mentioned in the passage cannot become a claim. Unresolved relations are
              dropped (extraction.yaml `drop_unresolved_entities`), never invented.
            - A claim's quote must appear verbatim in its cited paragraph, or the claim is dropped
              here (not just flagged later by `wiki audit claims` — PROMPTS.md rule 4).
            - `claim_id` is content-addressed (`schema.make_claim_id`), so the same fact reobserved
              in a second window merges its evidence into one claim instead of duplicating it.
            - `client` is duck-typed to `LLMClient.complete_json` (see classify.py precedent) so
              tests never touch Ollama.
Contract:   docs/CONTRACTS.md §3; docs/PROMPTS.md `claim_extract`.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Any, Protocol

from pydantic import BaseModel

from ..llm.budget import estimate_tokens
from .adequacy import neighbourhood_text, passes_evidence_adequacy, prompt_rules
from .resolve import group_known_entities, normalize_para_id, recover_verbatim, resolve_surface, unwrap_quote
from .schema import ExtractedFact, ExtractionResult, make_claim_id
from ..llm.parallel import map_calls, workers_for
from .windows import Window, allocate_window_caps, build_windows, pack_windows

STAGE = "claim_extract"

_SYSTEM_TEMPLATE = """You are extracting facts about ONE named character from passages of a \
novel. You will be told the character's name; extract facts ONLY about \
that character, never about anyone else named alongside them.

Three kinds of fact exist:

ATTRIBUTE (a scalar fact about the character):
{attribute_lines}

RELATION (a link to another character or entity actually named in the passage; "object" must be \
copied verbatim from a name that appears in the passage, not paraphrased or described):
{relation_lines}

TRAIT (a short adjective or noun phrase about the character, never a full sentence):
{trait_lines}

Rules:
- Extract only what this passage actually states or clearly implies about the named character.
- Omitting a fact this passage does not support is correct and expected — do not force a fit.
- A fact stated about the named character through a pronoun ("he", "she", "they") or an epithet \
("the merchant", "the wisewolf") still counts as being about them, as long as the passage makes \
the antecedent unambiguous and the configured naming requirement is satisfied (see \
EVIDENCE REQUIREMENTS below). Cite the paragraph that states the fact.
- If more than one person could plausibly be that pronoun's or epithet's antecedent, or the \
passage does not make it clear, omit the fact rather than guessing.
- A RELATION needs an actual stated connection (family, command structure, affiliation, an \
event they share) — do NOT invent one just because two people are named in the same scene. \
"Several people are in the same room doing separate things" is not a relation to any of them.
- WRONG example (do not do this): the passage shows two characters talking, fighting, or simply \
standing near each other, with nothing said about how they know each other, and a RELATIVE_OF \
or COMMANDS fact gets emitted anyway just because a name was there to attach it to. A quote of \
dialogue or narration that does not itself state or clearly imply the relationship is not \
evidence for one, no matter how confident-sounding the two characters' interaction is.
- "object" is used only for RELATION facts. Leave it out (or null) for ATTRIBUTE/TRAIT facts.
- "value" is used only for ATTRIBUTE/TRAIT facts (a short scalar or phrase). Leave it out (or \
null) for RELATION facts.
- "predicate" MUST be spelled EXACTLY as one of the names listed above, nothing else. If none of \
them fits, DO NOT invent a new predicate name and do NOT use a placeholder like "RELATION" or \
"RELATIONSHIP_STATUS" — leave the fact out entirely instead.
- "para_id" MUST be copied EXACTLY, character for character, from one of the bracketed labels \
above — e.g. copy "v01:c03:p0084" exactly as shown, never retype it as "p84" or "p084". A \
fact whose para_id was retyped instead of copied cannot be matched to its paragraph and is \
thrown away.
- "quote" must be copied VERBATIM — character for character, from a SINGLE paragraph, not \
stitched together from two — and must NOT be wrapped in extra quotation marks of your own. If \
the paragraph already shows the line in quotes, copy it exactly as it appears, with no \
additional quote marks added around it.
- polarity is "asserted" for a stated fact, "presumed" for something only believed or reported \
secondhand (a death report that might be wrong, a rumor) OR for a character's own statement about \
themselves or someone else that the passage marks as insincere — sarcasm, a boast, an exaggeration, \
a joke, or teasing (e.g. narration like "she teased," "he lied," "in jest," or a tone the passage \
itself undercuts) — do not take such a line at face value just because it is phrased as a direct \
statement. Narrator HEDGING counts too: "may have", "might have", "perhaps", "seemed to", \n"as if", "whoever", "someone" mark a guess, not a fact — extract it as "presumed" or \nnot at all, never as "asserted". "denied" for an explicit negation.
- confidence is 0.0-1.0: how directly the passage supports this fact.
- EVERY fact needs its OWN "para_id" — never omit it, even when several facts cite the same \
paragraph. A fact without one cannot be used at all and is thrown away.
- List each distinct fact ONCE. Never repeat a predicate+object/value pair you already listed \
earlier in the same response.

EVIDENCE REQUIREMENTS — a fact that fails any of these is discarded before anyone reads it, so it \
is worth a moment to satisfy them:
{evidence_rules}

Respond with JSON only, shaped exactly like this worked example (a RELATION fact about a
character named "Shin", using null for the fields a TRAIT or ATTRIBUTE fact would instead need):

{{"facts": [
  {{"predicate": "AFFILIATED_WITH", "kind": "relation", "object": "Spearhead Squadron", \
"value": null, "qualifier": "captain", "para_id": "v01:c00:p0031", \
"quote": "the Spearhead Squadron's captain, code name Undertaker", \
"confidence": 0.92, "polarity": "asserted"}}
]}}

Return {{"facts": []}} if this passage says nothing new about this character."""


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


# The shared local_fast profile's generation settings (models.yaml) are tuned for
# entity_classify's and entity_alias's short, single-object responses. A dense evidence window
# (many co-mentioned entities in one scene) can push a 14B model into a degenerate loop, re-
# emitting the same fact over and over until it hits num_predict mid-object -- observed against
# real Ollama output, not hypothetical (see docs/handover/PHASE_3.md). A stronger repeat_penalty
# and more headroom before that cap both reduce how often it happens; `llm/client.py`'s
# `_parse_json` truncation salvage covers the residual cases. Override for this stage only,
# rather than changing the shared profile for every stage that reuses it.
# 2026-09-11 (Phase 23 Part F): raised 4096 -> 8192 after a live OpenRouter smoke test
# (Nex-N2.5-Pro free) hit this cap mid-JSON on a genuinely fact-dense window (many real
# BACKGROUND/TRAIT facts, not a repeat-loop -- the response was clean up to the cut, confirmed by
# reading the run's own calls.jsonl) -- salvage recovered the facts before the cut but the ones
# after it were lost outright. This value is shared across every provider this stage might be
# routed to. The driver below reserves this output allowance when fitting requests to num_ctx.
# 2026-09-23: temperature pinned LOW for extraction specifically. Structured extraction has
# one right answer per passage, and sampling variance here is not creativity -- it is an
# unreproducible deliverable. Measured: re-running only the scene pass at temperature 0.4
# moved mean gold fact recall by 7 points in both directions with no code change, which made
# every before/after comparison in this phase argue with itself. The prose stages keep the
# profile default (0.4), because there a little variation reads better.
_GENERATION_OPTIONS = {"num_predict": 8192, "repeat_penalty": 1.3, "temperature": 0.1}


def _vocab_lines(spec: dict[str, Any]) -> str:
    lines = []
    for name, meta in spec.items():
        note = meta.get("note", "")
        lines.append(f"- {name} — {note}" if note else f"- {name}")
    return "\n".join(lines) or "(none defined)"


def _system_prompt(settings) -> str:
    return _SYSTEM_TEMPLATE.format(
        attribute_lines=_vocab_lines(settings.attributes),
        relation_lines=_vocab_lines(settings.relations),
        trait_lines=_vocab_lines(settings.traits),
        # Generated from the same config extract/adequacy.py enforces -- see prompt_rules().
        evidence_rules=prompt_rules(settings),
    )


def _user_prompt(
    entity: dict[str, Any],
    vol: int,
    window: Window,
    window_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> str:
    other_ids = sorted({m["entity_id"] for m in window_mentions})
    others = group_known_entities(other_ids, entities_by_id) or "(no one else named)"
    aliases = ", ".join(entity.get("aliases", [])) or "none"
    return (
        f'Character: "{entity["canonical"]}" (aliases: {aliases})\n'
        f"Volume {vol}.\n"
        f"Other people/things named in these passages: {others}\n\n"
        f"Passages (each paragraph labeled with its id; a passage break means the text on either "
        f"side comes from a different part of the volume and does not continue):\n{window.text}\n\n"
        f'Extract facts about "{entity["canonical"]}" only.'
    )


def _lookup_predicate(predicate: str, settings) -> tuple[dict[str, Any] | None, str]:
    if predicate in settings.attributes:
        return settings.attributes[predicate], "attribute"
    if predicate in settings.relations:
        return settings.relations[predicate], "relation"
    if predicate in settings.traits:
        return settings.traits[predicate], "trait"
    return None, ""


def _build_claim(
    entity: dict[str, Any],
    vol: int,
    fact: ExtractedFact,
    window: Window,
    window_mentions: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    settings,
    source: str,
    drops: Counter[str],
) -> dict[str, Any] | None:
    behaviour = settings.extraction_behaviour
    min_to_keep = float(behaviour.get("confidence", {}).get("min_to_keep", 0.5))
    max_quote_chars = int(behaviour.get("max_quote_chars", 240))
    drop_unresolved = bool(behaviour.get("drop_unresolved_entities", True))

    predicate = fact.predicate.strip().upper()
    spec, true_kind = _lookup_predicate(predicate, settings)
    if spec is None:
        drops["unknown_predicate"] += 1
        return None

    para_id = normalize_para_id(fact.para_id, window.para_ids)
    if para_id is None:
        drops["citation_outside_window"] += 1
        return None
    paragraph = records_by_id.get(para_id)
    if paragraph is None:
        drops["citation_outside_window"] += 1
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
        object_id = resolve_surface(fact.object or "", window_mentions, window.text)
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
        subject_entity=entity,
        object_entity=entities_by_id.get(object_id) if object_id else None,
        true_kind=true_kind,
        predicate_spec=spec,
        paragraph_text=paragraph["text"],
        quote=quote,
        behaviour=behaviour,
        drops=drops,
        qualifier=fact.qualifier,
        subject_context_text=neighbourhood_text(
            para_id, window.para_ids, records_by_id,
            int(behaviour.get("evidence_adequacy", {}).get("subject_named_within_paragraphs", 0) or 0),
        ),
    ):
        return None

    qualifier = (fact.qualifier or "").strip() or None
    return {
        "claim_id": make_claim_id(
            entity["entity_id"], predicate, object_norm, vol,
            polarity=fact.polarity, qualifier=qualifier,
        ),
        "subject": entity["entity_id"],
        "predicate": predicate,
        "kind": true_kind,
        "object": object_id,
        "value": value,
        "qualifier": qualifier,
        "first_vol": vol,
        "chapter_idx": paragraph["chapter_idx"],
        "evidence": [{"para_id": para_id, "print_page": paragraph.get("print_page"), "quote": quote}],
        "confidence": fact.confidence,
        "polarity": fact.polarity,
        "source": source,
    }


def _merge_claim(existing: dict[str, Any], new: dict[str, Any]) -> None:
    seen = {(e["para_id"], e["quote"]) for e in existing["evidence"]}
    for e in new["evidence"]:
        key = (e["para_id"], e["quote"])
        if key not in seen:
            existing["evidence"].append(e)
            seen.add(key)
    existing["confidence"] = max(existing["confidence"], new["confidence"])


def extract_character_volume(
    entity: dict[str, Any],
    vol: int,
    records: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    settings,
    client: JSONClient,
    system: str | None = None,
    max_windows: int | None = None,
    stage: str = STAGE,
    window_cfg: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """One character's claims for one volume. Builds evidence windows, calls the model once per
    window, resolves/validates each returned fact, and merges repeat observations of the same
    fact (by `claim_id`) into one claim with combined evidence. Returns (claims, drop_reasons).
    `max_windows`, when given, is this entity's share of the volume's window pool (Phase 22 B3,
    `windows.allocate_window_caps`) and overrides the flat config cap. `stage`/`window_cfg` let
    `extract/infobox.py` run the same gates over one packed whole-volume dossier instead."""
    window_cfg = window_cfg if window_cfg is not None else settings.extraction_behaviour.get("window", {})
    entity_mentions = [m for m in mentions if m["entity_id"] == entity["entity_id"]]
    entity_mention_ids = {m["para_id"] for m in entity_mentions}
    drops: Counter[str] = Counter()
    windows = build_windows(
        entity["entity_id"], [m["para_id"] for m in entity_mentions], records, window_cfg,
        max_windows=max_windows, drops=drops,
    )

    mentions_by_para: dict[str, list[dict[str, Any]]] = {}
    for m in mentions:
        mentions_by_para.setdefault(m["para_id"], []).append(m)
    records_by_id = {r["para_id"]: r for r in records}

    system_prompt = system or _system_prompt(settings)
    profile = client.profile_for(stage)
    source = str(profile) if stage == STAGE else f"{stage}:{profile}"
    profile_options = getattr(profile, "options", {})
    context_limit = profile_options.get("num_ctx")
    # No guessed context size for profiles that do not declare one. This is an estimated
    # guard (the shared estimator is not a tokenizer), with explicit framing headroom.
    safety_tokens = int(window_cfg.get("context_safety_tokens", 256))
    input_limit = None
    if isinstance(context_limit, int) and context_limit > 0:
        input_limit = context_limit - _GENERATION_OPTIONS["num_predict"] - safety_tokens

    # Phase 26 part C: pack several windows into one request. OpenRouter's free tier is capped on
    # CALLS, not tokens, and this stage was sending 361 requests per two volumes with a median of
    # 3.6k in / 216 out -- most of each one the same system prompt. `max_windows_per_request`
    # bounds the batch by the OUTPUT cap (facts scale with passages, and a truncated JSON costs a
    # repair retry, spending the call this saves), the token budget by the input side: the
    # profile's own usable context when it declares one, else the configured default.
    per_request = int(window_cfg.get("max_windows_per_request", 1))
    if per_request > 1:
        request_budget = input_limit if input_limit is not None else int(
            window_cfg.get("max_request_tokens", 24000)
        )
        windows = pack_windows(
            windows, request_budget, estimate_tokens(system_prompt) + 512, per_request
        )

    claims: dict[str, dict[str, Any]] = {}
    pending = deque(windows)
    while pending:
        window = pending.popleft()
        window_mentions = [
            m for pid in window.para_ids for m in mentions_by_para.get(pid, []) if m["entity_id"] != entity["entity_id"]
        ]
        prompt = _user_prompt(entity, vol, window, window_mentions, entities_by_id)
        if input_limit is not None and estimate_tokens(system_prompt + "\n" + prompt) > input_limit:
            if len(window.para_ids) == 1:
                # Never truncate an evidence paragraph and then accept a citation against the
                # unseen original. Count the unsendable window in the existing yield artifact.
                drops["context_window_overflow"] += 1
                continue
            midpoint = len(window.para_ids) // 2
            children = []
            for ids in (window.para_ids[:midpoint], window.para_ids[midpoint:]):
                children.append(Window(
                    entity_id=window.entity_id, vol=window.vol, para_ids=ids,
                    text="\n".join(f'[{pid}] {records_by_id[pid]["text"]}' for pid in ids),
                    mention_count=sum(pid in entity_mention_ids for pid in ids),
                ))
            pending.extendleft(reversed(children))
            drops["context_window_split"] += 1
            continue
        result = client.complete_json(
            stage, prompt, ExtractionResult, system=system_prompt, options=_GENERATION_OPTIONS
        )
        for fact in result.facts:
            claim = _build_claim(
                entity, vol, fact, window, window_mentions, records_by_id, entities_by_id, settings, source, drops
            )
            if claim is None:
                continue
            existing = claims.get(claim["claim_id"])
            if existing is None:
                claims[claim["claim_id"]] = claim
            else:
                _merge_claim(existing, claim)

    return sorted(claims.values(), key=lambda c: c["claim_id"]), drops


def extract_volume(
    characters: list[dict[str, Any]],
    vol: int,
    records: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    all_entities: list[dict[str, Any]],
    settings,
    client: JSONClient,
    on_progress=None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Every character's claims for one volume. `on_progress(i, total, entity, n_claims)` is
    called after each character, if given, so the CLI can print a running count."""
    entities_by_id = {e["entity_id"]: e for e in all_entities}
    system_prompt = _system_prompt(settings)

    window_cfg = settings.extraction_behaviour.get("window", {})
    base_cap = int(window_cfg.get("max_windows_per_entity_per_volume", 40))
    floor = int(window_cfg.get("min_windows_per_entity_per_volume", 3))
    # The pool is sized over EVERY character in the volume, never over `characters` -- which is
    # the filtered list when `--entities`/`--limit` narrows the run. `allocate_window_caps` sets
    # `total_pool = base_cap * len(mention_counts)`, so passing the filtered subset shrinks the
    # whole pool to fit it: measured 2026-09-21, Holo's v1 cap was 164 in a full run and 40 under
    # `--entities "Holo"`, discarding 36 real evidence windows (`window_cap_truncated`) before
    # the model ever saw them. That inverted the one remedy `wiki audit eval` recommends for low
    # recall -- a targeted top-up gave the character FOUR TIMES LESS evidence than a full run --
    # and it silently made every targeted-vs-stored claim-count comparison meaningless.
    # The sign of the error depends on density, which is why it hid for so long: collapsing the
    # pool to a single entity leaves that entity `base_cap` (40), so a SPARSE character gains
    # (jakob v2: 33 in a full run, 40 targeted, 22 windows actually built -- never truncated
    # either way) while a DENSE one loses badly (holo v2: 116 -> 40, 36 windows discarded).
    char_ids = {e["entity_id"] for e in all_entities if e.get("type") == "CHARACTER"}
    mention_counts = Counter(m["entity_id"] for m in mentions if m["entity_id"] in char_ids)
    window_caps = allocate_window_caps(dict(mention_counts), base_cap, floor)

    all_claims: list[dict[str, Any]] = []
    total_drops: Counter[str] = Counter()
    total = len(characters)

    def extract_one(entity: dict[str, Any]):
        return extract_character_volume(
            entity, vol, records, mentions, entities_by_id, settings, client, system=system_prompt,
            max_windows=window_caps.get(entity["entity_id"]),
        )

    def collect(i: int, entity: dict[str, Any], result) -> None:
        character_claims, drops = result
        all_claims.extend(character_claims)
        total_drops.update(drops)
        if on_progress is not None:
            on_progress(i + 1, total, entity, len(character_claims))

    # Characters are independent of each other here -- `extract_character_volume` reads shared
    # data and returns its own claims, touching nothing global. Running several at once is the
    # single biggest wall-clock win in the pipeline; the claim ORDER is unchanged, because
    # map_calls returns in input order.
    map_calls(extract_one, characters, workers_for(client, STAGE), on_result=collect)
    return all_claims, total_drops
