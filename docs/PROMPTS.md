# PROMPTS.md — the prompt catalogue and why each one is shaped that way

Prompts live in code next to the stage that uses them (`entities/classify.py`,
`extract/claims.py`, `synth/prose.py`). This file records the **design rules** they all follow
and the reasoning behind each one, so a change here is a deliberate decision rather than a tweak
someone made at 2am to fix one bad output.

Every prompt is aimed at **Qwen2.5-14B-Instruct at 4-bit**, not at a frontier model. That
constraint drives nearly every rule below.

---

## Rules every prompt follows

**1. One decision per call.** A 14B model asked to find entities, type them, resolve their
aliases and extract their relationships in one pass does all four badly. Each stage asks for one
kind of judgement.

**2. Closed vocabularies, always inlined.** Predicates, attribute keys and entity types come from
`config/extraction.yaml` and are listed verbatim in the prompt. An open vocabulary produces
`FRIEND_OF`, `IS_FRIEND`, `FRIENDS_WITH` and `ALLY_OF` for one relationship, which then fails to
merge in the graph and renders as four contradictory rows.

**3. JSON mode is enforced by the runtime, not by asking — but the FIELD NAMES still are.**
Ollama's grammar-constrained `format: json` is far more reliable than instructing a small model
to behave. The client validates against a Pydantic schema and re-asks with the specific
validation error on failure — see `llm/client.py::complete_json`. But `json_mode` only forces
*valid JSON*, never a specific *shape*: Google's adapter sets `response_mime_type:
application/json` (`llm/providers/google.py`), which enforces syntax only, and a model left to
infer its own keys from context will happily do so — this was hit real-data-testing
`codex_summary` (Phase 7): a `{"text": "..."}`-shaped schema with a system prompt that never
showed an example, primed by a line like `"Location: Sixth Sector"`, reliably came back as
`{"location": "...", "definition": "..."}` from `gemini-flash-lite-latest` instead — `text:
Field required`, every one of the 3 repair attempts, because the repair loop's own correction
text apparently reads as less authoritative than the model's first instinct once it has picked a
frame. `entities/classify.py` already had the fix: embed a literal example,
`` Respond with JSON only: {"entity_type": "...", ...} ``. Every NEW `complete_json` prompt must
do the same — end the system prompt with a one-line literal JSON example naming every field, not
just a prose description of them.

**4. Evidence is mandatory and verbatim.** Every claim must carry a `para_id` and a quote copied
exactly from that paragraph. `wiki audit claims` verifies the quote actually appears there. This
is the main defence against a confident 14B model inventing a plausible fact.

**5. "Not stated" is an explicit, rewarded option.** Every extraction prompt says that omitting a
field is correct when the text does not support it. Without this, small models fill every field
they are shown.

**6. Prompts never mention volumes the caller did not include.** The spoiler boundary is enforced
by *what goes into the prompt*, not by asking the model to keep a secret. A prose prompt at
cutoff N receives only claims with `first_vol <= N` and is told the current volume is N.

**7. Stable prefix first, variable content last.** System prompt and the taxonomy block are
byte-identical across calls in a stage so the cache key differs only where the input differs, and
so API providers can cache the prefix.

---

## Catalogue

Status column: **built** = implemented; **planned** = arrives in the named phase.

| Prompt | Stage / role | Phase | Status | What it decides |
|---|---|---|---|---|
| `entity_type` | `entity_classify` | 2 | planned | Given a surface form and 3–5 example sentences, is this a CHARACTER, FACTION, LOCATION, EVENT, TECH, or not an entity? |
| `alias_same_person` | `entity_alias` | 2 | planned | Given two surface forms with evidence, do they refer to the same entity? Returns a decision plus confidence. |
| `claim_extract` | `claim_extract` | 3 | planned | Given one character and their evidence windows from one volume, return typed claims with evidence and confidence. The bulk stage. |
| `arbitrate_conflict` | `arbitrate` | 4 | built | Two same-volume claims disagree. Given both evidence passages, which is right — or is neither? |
| `codex_summary` | `codex_summary` | 7 | built | One sentence defining a faction, location, battle or technology. |
| `background_prose` | `prose` / `prose_polish` | 6 | built | 2–4 sentences of biography, from BACKGROUND trait claims only. |
| `personality_prose` | `prose` / `prose_polish` | 6 | built | 2–4 sentences of characterisation, from PERSONALITY/MOTIVATION/SKILL/FEAR trait claims. |
| `scene_extract` (beat) | `scene_extract` | 18 | built | Given one ~4k-token chapter span with known-mentioned entities, return participants, location, and a beat summary for that passage. |
| `scene_extract` (detail) | `scene_extract` | 18/22 | built | Same span, same known-entities prefix, second call (Phase 22 B1): return state changes, quotes, epithet mentions, and (C1) attribute/trait/relation facts per participant for that passage. |
| `verify_facts` | `verify` | 22 | built | Given one character's currently visible facts, their quoted evidence, and deduplicated nearby paragraph context from the same chapter at or below the cutoff, which facts does the evidence NOT support? Context interprets the quote; it is not additional cited evidence. A relation fact also carries the bare claims (no quotes) of the other relations between the same pair (2026-09-25, `attach_pair_siblings`). |
| `infobox_extract` | `infobox_extract` | 30 | built | Given every paragraph of one volume that names one character, in reading order, which of GENDER / AGE / ORIGIN does the text support? Up to 3 quotes per field; a GENDER quote must contain both the name and a gendered word. Same JSON shape and gates as `claim_extract` (`extract/infobox.py`). |
| `backstory_extract` | `backstory_extract` | 30 | built | Same dossier as `infobox_extract` but with ±4-paragraph context (first-person pasts rarely name the speaker): which BACKGROUND facts, phrased in the past tense, does the text reveal? Excludes age, current job/employer, health, bare origin. `extract/infobox.py` `PASSES["backstory"]`. |

---

## Notes on the harder ones

### `claim_extract` — where the token budget is won or lost

This prompt runs once per character per volume and consumes the overwhelming majority of the
project's tokens. Two things keep it affordable:

- Its input is **only** the evidence windows where the character is actually named, taken from the
  Aho-Corasick mention index. It never sees a whole chapter.
- Windows are merged when they overlap and capped by
  `extraction.window.max_windows_per_entity_per_volume`, so a protagonist named 4,000 times does
  not produce 4,000 windows.

The prompt names one subject explicitly and asks only about that subject. Asking a 14B model to
extract claims about "everyone in this passage" produces subject-attribution errors that are
expensive to detect later.

**Phase 22 B2 (fixes U1)** added two rules to `_SYSTEM_TEMPLATE` recovering facts the pre-B2
prompt silently lost: a fact stated about the subject through a pronoun or epithet still counts
when the passage makes the antecedent unambiguous (this had been read, incorrectly, as needing
the subject's own name in the same sentence), paired with an explicit "omit rather than guess"
rule for the genuinely ambiguous case, so recall does not just shift into a new hallucination
mode. The same change also groups the "other people/things named in this passage" line by
entity_id (`resolve.py::group_known_entities`) instead of listing raw surfaces flat — see
`scene_extract` below for why that matters (fixes S3).

**Phase 25 item 6** extended the `polarity` rule: a character's own sarcastic, boastful,
exaggerated, or in-jest statement about themselves or someone else must be `presumed`, not
`asserted`, the same as narrator-hedged/secondhand claims already were. Found against the real
corpus — Spice and Wolf's Holo is built around teasing and suspected lying, and nothing in the
prompt previously told the model that a confident-sounding line of dialogue could be insincere.
Reuses the existing `Polarity` literal rather than adding a fourth value or a new field.

### `alias_same_person` — the highest-leverage prompt in the project

A wrong merge silently mixes two people's facts onto one page; a wrong split gives one character
two half-empty pages. Both are hard to spot downstream, which is why Phase 2 produces
`roster.html` for a human to review before extraction ever runs.

The prompt is deliberately conservative: it is told that **leaving two forms separate is the
safer error**, because a human reviewing the roster can merge them from
`entity_overrides.merge` in the series config far more easily than they can unpick a bad merge.

### `background_prose` / `personality_prose` — short on purpose

The user asked for short descriptions, and short prose is also where hallucination has the least
room to hide. Both are capped at 2–4 sentences by `extraction.prose` and are given *only* TRAIT
claims — BACKGROUND for the former, PERSONALITY/MOTIVATION/SKILL/FEAR for the latter
(`synth/prose.py`, Phase 6) — not the structured attribute/relation fields `synth/assemble.py`
renders. `forbid_uncited_specifics: true` instructs the model that any name, number, place or
event it mentions must appear in the claims it was handed; the `evidence` array returned on the
page is not a citation the model reports back, but the exact set of `para_id`s that were fed to
the prompt, so it cannot be falsified. A character with no relevant trait claims at a given cutoff
gets `null` for that field and no LLM call at all — see CONTRACTS §5.

Structured fields — name, age, gender, affiliations, titles, nicknames, relationships — are
**never** generated. They render from graph rows. There is no prompt for them, and there should
never be one.

**Phase 25 item 7** replaced the cap's own enforcement: it had been checked as
`len(text) <= max_sentences * 280` chars, a proxy that let a real corpus page carry 5 sentences
against a 4-sentence cap and 15 against a 12-sentence cap, both comfortably inside their char
budget. `synth/prose.py::_prose_schema_for` now counts real sentences
(`_count_sentences`) and rejects a response over the configured `max_sentences` directly — the
cap is enforced on what it names, not a stand-in for it. The `history` section's own prompt
(shared with the older `generate_chronology` path) was also tightened to ask for the character's
major turning points rather than a scene's incidental logistics (exact sums, side-character
errands) — a real corpus history read like a coin-fraud subplot recap, not a biography, before
this.

### `codex_summary` — one sentence, no claims to read from

Non-character entities (FACTION/LOCATION/EVENT/TECH) never get a `claim_extract` pass — that
stage is a per-character driver (`extract/claims.py`) — so this prompt cannot read TRAIT claims
the way `background_prose` does. Instead `site/bundle.py::_codex_windows_for_volume` reuses
`extract/windows.py::build_windows` directly over the entity's own mention paragraphs (the same
context/hard-break discipline `claim_extract` gets), accumulated across every volume up to the
cutoff and capped by `_cap_codex_evidence` (Phase 22 B4) at 5 windows total AND
`window.codex_max_evidence_tokens` (2000 by default) — not per volume, and not window count
alone: a corpus-wide entity like "the Legion" would otherwise hand the model dozens of windows, or
(with paragraph merging) tens of thousands of tokens from just 5. Same zero-evidence rule as
`background_prose`: no windows, no LLM call, `summary: null`. The call itself is also only made
when the evidence changed — a cutoff whose member list changed but evidence didn't reuses the
previous call's text rather than re-paying for an identical prompt. Capped at
`extraction.prose.codex_summary.max_sentences` (1 by default) — see rule #3 above for the real
failure this prompt's system message had to be fixed to avoid.

### `arbitrate_conflict` — the only prompt allowed to spend money by default

Same-volume contradictions are extraction errors, and the bulk-stage model produced them, so
asking it to referee its own mistake is weak. This routes to a separate `arbitrate` role — a
bigger local model (`local_best`, `qwen2.5:32b`) as of the 2026-09-03 routing reset, with
`groq_fast` as the config-declared fallback; see `config/models.yaml`, the source of truth for the
exact profile. It fires rarely — only when two
claims disagree within one volume *and* their confidences are within
`conflicts.escalate_when_confidence_within` — so the cost stays small. Implemented in
`graph/contradictions.py::_resolve_pair`.

Cross-volume disagreement never reaches this prompt. That is a character changing, not an error,
and it is handled by interval supersession in `graph/temporal.py`.

### `scene_extract` — wider context, more outputs, same discipline

The chapter-major second pass (Phase 18) receives a whole `~4k-token chapter span` rather than
a single entity's evidence windows. Its wider scope — participants are named, not passed in as a
fixed subject — is what dissolves the pronoun and low-mention-count coverage problems
`claim_extract` inherits from the mention-window design.

**Phase 22 B1 split this into two calls per span** — a "beat" call (participants, location,
beat_summary) and a "detail" call (state_changes, quotes, epithets) — 56 → 112 calls per volume
in exchange for giving each call its own, purpose-fit system prompt instead of one combined
budget shared across every field. Both calls are built from the identical passage-text/known-
entities prefix (`extract/scenes.py::_user_prefix`), diverging only in the final "Report ..."
instruction line, so a caching provider (or Ollama's own context reuse) pays for that shared
prefix once, not twice.

- `_BEAT_SYSTEM_TEMPLATE` (`extract/scenes.py`) needs no predicate vocabulary at all — it only
  ever returns participants/location/beat_summary — and closes with one literal JSON example.
- `_DETAIL_SYSTEM_TEMPLATE` inlines the predicate vocabulary TWICE, for two different purposes:
  once as `predicate_lines` (attributes + relations combined, for `state_changes`, which may or
  may not resolve to a configured predicate), and again split three ways as `attribute_lines` /
  `relation_lines` / `trait_lines` for the PARTICIPANT_FACTS section (Phase 22 C1) — the same
  three-way vocabulary `claim_extract`'s own system prompt uses, reused verbatim so the two
  drivers ask the model to extract facts under an identical rulebook. Closes with **four** literal
  JSON examples, one per field (state_change / quote / epithet / participant_facts).

**Phase 22 C1 — `participant_facts` makes this the PRIMARY facts pass, not just scene metadata.**
For every participant a span actually says something new about, the detail call now also returns
`{"subject": <name>, "facts": [...]}` groups — each `fact` shaped exactly like a `claim_extract`
fact (predicate/kind/object/value/qualifier/para_id/quote/confidence/polarity), just scoped to one
named subject per group instead of one subject per whole call. This was folded into the EXISTING
detail call rather than added as a third one, so C1 costs zero extra calls per span — chapter-span
coverage already reaches every paragraph and every participant by construction (`extract/spans.py`
§3b), which is what lets this replace `claim_extract`'s mention-window pass as the primary facts
source rather than merely supplementing it. `claims.py`'s own pass is being demoted to a targeted
top-up (Phase 22 C2, not yet built) instead of being removed — see `docs/CONTRACTS.md` §3.

The user prompt injects the known entity surfaces so the model can resolve participants and
epithets without inventing names — grouped by entity_id as `"Kraft Lawrence (also: Lawrence,
Kraft)"` rather than a flat set of raw surfaces (Phase 22 B2, `resolve.py::group_known_entities`,
fixes S3: the flat form let two aliases of one person read as two different people, which is
what let a beat_summary hallucinate a transaction between them).

The six return fields still obey the verbatim-quote and entity-resolution rules differently:

- **`participants` / `location`** — resolved against the known-entity list via
  `resolve.py::resolve_surface`: exact match first, then a whole-word name-part fallback (Phase
  22 B2 — "Kraft" alone now resolves to the "Kraft Lawrence" entity instead of going unresolved
  or, worse, matching an unrelated short candidate; the longest qualifying candidate wins when
  more than one matches). A surface that resolves to nothing is dropped.
- **`state_changes` / `quotes`** — each requires a real `para_id` label from the span (copied
  verbatim) and a `quote` verbatim from that paragraph. Dropped by `_build_state_change` /
  `_build_quote` if either check fails, same audit-trail discipline as `claim_extract`'s evidence.
  **Phase 25 item 4**: `_build_quote` also drops a quote cited from a paragraph `ingest/epub.py`
  classified `speech: "narration"` — found against the real corpus (narrator exposition extracted
  as if it were spoken dialogue). Deliberately NOT applied to `state_changes`/`epithets`, which
  are legitimately narration-sourced by design. The prompt itself was also tightened: a quote's
  text is only the words inside the quotation marks, never a trailing narrator attribution
  ("she shot back") the model had previously included verbatim.
- **`epithets`** — same para_id + verbatim-quote requirement PLUS the entity it refers to must
  resolve (the referent cannot be invented from a bare epithet). Rejected epithets are counted
  and logged but never written.
- **`participant_facts`** (Phase 22 C1) — same para_id + verbatim-quote requirement as
  `state_changes`/`quotes` PLUS: the group's `subject` must resolve (same as a quote's speaker),
  a relation's `object` must resolve against this chapter's known entities (never the whole
  gazetteer), and `predicate` must be one of the three closed vocabularies exactly — identical
  discipline to `claim_extract`'s own facts, just built by `extract/scenes.py::_build_participant_fact`
  instead of `claims.py::_build_claim`. Output is a §3 claim dict, not a scene sub-field.
- **`beat_summary`** — the one exception: a genuine paraphrase, not quote-gated. It is the raw
  material for the future narrative / Chronology section (Phase 20), never directly rendered.

`_BEAT_GENERATION_OPTIONS`/`_DETAIL_GENERATION_OPTIONS` replace the old single combined
`_GENERATION_OPTIONS`, split from `claim_extract`'s empirically tuned values: the beat call needs
much less headroom (three scalar-ish fields) than the detail call (four sub-lists plus four worked
examples). `_DETAIL_GENERATION_OPTIONS.num_predict` was raised 6144 -> 8192 when `participant_facts`
was added (Phase 22 C1) — usually the largest sub-list per span since it covers every participant,
not just those with dialogue or a state change. Both are expected to need the same kind of
real-data tuning that `claim_extract` already needed — see `docs/handover/PHASE_3.md` for the
tuning methodology.
