# CONTRACTS.md — every data schema in the pipeline

**Read this file instead of reading code to learn a data shape.** It is the single source of truth
for what each stage produces and consumes. If you change a schema, change it here in the same commit.

Conventions used throughout:

- `vol` is a **1-indexed integer** volume number. Never a filename, never 0-indexed.
- `first_vol` is the volume whose text supplied the evidence. **Every fact carries one.** This field
  is what makes spoiler scoping work; a record without it is a bug.
- IDs are stable across reruns so caches and diffs stay meaningful.
- All files are UTF-8. `.jsonl` = one JSON object per line. `.json` = one object.

---

## 1. Parsed paragraphs — `data/01_parsed/v{NN}.jsonl`

Produced by `ingest/`. Deterministic; no LLM. One record per paragraph, in reading order.

```jsonc
{
  "para_id":     "v01:c03:p0142",   // stable ID: volume, chapter index, paragraph index
  "vol":         1,
  "chapter_idx": 3,                 // 0-indexed position in the volume's chapter list
  "chapter_id":  "chapter010",      // the <section id> of the chapter's first XHTML file
  "chapter_title": "To Your Gallant Visage at the Underworld's Edge",
  "chapter_kind": "chapter",        // prologue|chapter|interlude|epilogue|afterword|epigraph
  "seq":          142,              // paragraph index within the chapter
  "text":        "There were no casualties on that battlefield.",
  "print_page":   9,                // nearest preceding <a id="page-N">; null if none seen yet
  "speech":      "narration",       // see §1.1
  "is_monologue": false,            // whole paragraph wrapped in a single <em>
  "scene_break_before": "none",     // none | soft | hard   -- see §1.2
  "n_words":      7
}
```

### 1.1 `speech` channel

| Value | Meaning | Detection |
|---|---|---|
| `narration` | Ordinary prose | default |
| `dialogue` | Spoken aloud | paragraph starts with an opening curly quote `“` |
| `para_raid` | Radio / Sensory-Resonance comms | element class matches `^(san\|pararaid\|para-raid.*\|special.*)$` on the `<p>` **or any descendant `<span>`** |
| `machine` | Legion / OS synthetic voice | ancestor `div[class^=ecom_]`, or text matches `^<{1,2}.*>{1,2}$` after entity decode |

`para_raid` paragraphs are the densest source of call signs and vocatives in the corpus, and are
weighted accordingly in `entities/candidates.py`. A volume that reports **zero** `para_raid`
paragraphs means the class list regressed — `wiki audit ingest` fails on this.

### 1.2 `scene_break_before`

| Value | Source | Use |
|---|---|---|
| `hard` | An `img.ornament` preceded this paragraph | POV/scene boundary; never span it when building a window |
| `soft` | `class` in `{space-break, space-break1, space-break2}` | Time/POV shift; prefer to split here |
| `none` | — | |

### 1.3 Volume manifest — `data/01_parsed/manifest.json`

```jsonc
{
  "series": "86",
  "volumes": [
    { "vol": 1, "title": "86—EIGHTY-SIX, Vol. 01", "isbn": "9781975303136",
      "pub_date": "2019-03-26", "n_chapters": 18, "n_paragraphs": 3104,
      "n_words": 74397, "source_file": "86--EIGHTY-SIX v01 [Yen Press] [Stick].epub" }
  ]
}
```

### 1.4 Chapter-scoped ingestion (Phase 11) — `ingest --volumes <one> --chapters <spec>`

The volume EPUB is still always fully walked (chapter boundaries can only be found by walking
the whole spine, and it's free/deterministic — see `CLAUDE.md` §3 trap 2), but only the selected
chapters' records are (re)written: `cli.py::_merge_by_chapter` keeps every existing record whose
`chapter_idx` is **not** in the requested set and replaces the rest, so a volume's `v{NN}.jsonl`
accumulates one chapter at a time across repeated calls instead of being clobbered. Skip-if-exists
is per-chapter (`--force` only redoes the requested chapters, not the whole file); the manifest
entry is always recomputed from the file's full, merged content, never just the latest call's
slice. `wiki step` is the primary caller of this accumulation, one chapter per increment.

### 1.5 Images — removed (Phase 32)

Phase 25 captured art-plate illustrations at ingest (`data/01_parsed/img/`) and Phase 29 used them
as character portraits and a per-volume gallery. Phase 32 removed all of it: nothing in the
pipeline can see what a plate depicts, so a portrait was a guess about who is in the frame, and
picture identification is outside the study's scope (`docs/vision/PHASE_32.md`, req. 4; future
work). Ingest still recognises an image-only page (heading-less, at least one `<img>`, word count
under `image_page_max_words`) and drops it from the text. That is CLAUDE.md §3 trap 9, unchanged.

### 1.6 Reading volumes and decontaminated series (Phase 31)

**Reading volumes.** A standalone book with `source.volume_breaks` (regexes on chapter titles)
is cut into volumes by `ingest/epub.py::select_reading_volume`: each matching title starts the
next volume, `epub.drop_chapter_titles` removes non-story chapters (Gutenberg front matter), and
`chapter_idx` restarts at 0 in every volume. The records are otherwise ordinary §1 records.

**Decontaminated series.** A series whose config has a `decontaminate:` block is never parsed
from an EPUB (`wiki ingest` refuses it). `wiki decontaminate` writes its `v{NN}.jsonl` from
`decontaminate.from_series`'s records: **same `para_id`, `vol`, `chapter_idx`, `seq`, `speech`,
`scene_break_before`** as the source, with `text` and `chapter_title` name-remapped and (unless
`paraphrase: false`) paraphrased, `n_words` recomputed, and one extra key:

```jsonc
{ "para_id": "v01:c07:p0012", "text": "Baptiste grabbed his firearm.", "decon": "paraphrased" }  // or "remap_only"
```

`remap_only` on a paraphrasing series means the paraphrase was empty or its length fell outside
`length_ratio` (default 0.5–2.0), so the remapped text was kept. `decon_report.json`, one entry
per volume:

```jsonc
{ "v01": { "n_paragraphs": 1170, "paraphrased": 1160, "remap_only": 2, "verbatim": 8, "identical_to_source": 0,
           "words_source": 48542, "words_derived": 47110,
           "residual_name_paragraphs": 0, "residual_names": {} } }   // illustrative numbers
```

`verbatim` (2026-10-02) counts paragraphs whose rewrite, after one second request, still keeps
half or more of the input's word 4-grams (`decontaminate.kept`): the text is used, the record says
`decon: "verbatim"`, and it is never counted as paraphrased.

`residual_names` maps a `para_id` to original names still present after the final remap. It
must be empty. A non-empty entry means the entity map missed a form.

## 2. Entities — `data/02_entities/`

### 2.1 `gazetteer.json`

```jsonc
{
  "entities": [
    {
      "entity_id":  "shinei-nouzen",          // slug of canonical_name; stable, used in URLs
      "canonical":  "Shinei Nouzen",
      "type":       "CHARACTER",              // see config/extraction.yaml for the closed set
      "aliases":    ["Shin", "Undertaker", "Baleygr", "the Reaper", "Nouzen"],
      "surface_forms": [                      // what actually goes into the automaton
        { "text": "Shinei Nouzen", "ambiguous": false, "first_vol": 1 },
        { "text": "Shin",          "ambiguous": false, "first_vol": 1 },
        { "text": "Undertaker",    "ambiguous": false, "first_vol": 1 },
        { "text": "Baleygr",       "ambiguous": false, "first_vol": 3 }
      ],
      "first_vol":  1,                        // min over surface_forms
      "mention_count": 4187,
      "importance": 0.98,                     // normalised mention count; drives API-polish routing
      "confidence": 0.95,                     // clustering confidence
      "notes": "Alias 'Reaper' also matches 'Reaper of the Eastern Front'; longest-match wins."
    }
  ],
  "built_at": "2026-09-01T18:22:04Z",
  "volumes_covered": [1, 2, 3],
  "provenance": {                           // [24] 2026-09-14, optional -- absent on a gazetteer
    "min_mentions": 3,                      // built before this field existed, not an error
    "max_candidates_per_volume": 400,
    "max_llm_alias_pairs": 300,
    "alias_pairs_total": 171,
    "alias_pairs_dropped": 0
  }
}
```

**`first_vol` on a surface form matters as much as on the entity.** An alias revealed in Volume 3
must not appear on a Volume-1 page even though the character does.

`ambiguous: true` marks a surface form that also occurs as a common word or as part of another
entity's name. Ambiguous forms are indexed but require a disambiguation pass before being linked.
A literal collision (two entities sharing one exact surface string) is resolved at automaton-build
time by keeping whichever `entity_id` sorts first alphabetically and dropping the surface from every
other claimant — `entities/automaton.py::build_automaton` records every drop on the automaton's
`dropped_ambiguous_surfaces` (not persisted to this file; `wiki audit gazetteer` recomputes it on
demand, since it's cheap and fully determined by `entities` already in hand).

**`provenance`** records the build-time PARAMETERS that shaped this specific entity list — not
reconstructible from `entities` alone, and load-bearing for
`probe/channels/index.py::measure_candidate_mining`, whose `min_mentions` default used to be an
unverified assumption about the artifact it measures (closed 2026-09-14,
`docs/vision/plans/0008-pre-full-scale-audit.md` B1/B3). `alias_pairs_total`/`alias_pairs_dropped` are
`entities/alias.py::MAX_LLM_PAIRS` (or a series config's `entities.max_llm_alias_pairs` override)'s
effect: how many candidate pairs needed an LLM call to resolve, and how many of those were past the
cap and never asked. `wiki audit gazetteer` warns when `alias_pairs_dropped > 0` — the dropped tail
is specifically the low-fuzzy nickname/call-sign pairs the cap exists to ration, so a nonzero count
here is a real, measured risk of entity fragmentation, not just a number.

**Surface `source`** (new builds): `corpus` for an unchanged mined candidate string;
`alias_cluster` for a string produced by honorific stripping; `override` for an explicitly
added config alias; `epithet_mining` for a scene epithet. Clustering uses the source
of the earliest contributing volume, preferring `corpus` on a tie. Adding an already-present
surface, including through an override merge, retains the earliest `first_vol` and its source.
Later/equal evidence retains the existing source. Legacy missing source is unknown;
do not infer corpus origin for an old override from its spelling.

**[34] `vols` and `source: "bare_by_volume"`** (optional; absent means every volume). A bare first
name that means different people in different volumes is owned per volume range:
`entities/alias.py::_assign_bare_forms` gives each volume's bare mentions to the full name that
dominates that volume (at least `BARE_FORM_MIN_FULL` mentions, `BARE_FORM_DOMINANCE` times the other
full names with that first name, and `BARE_FORM_MIN_SHARE` of the bare mentions; a volume without
such a vote keeps the previous owner). Anne's "Walter" is `walter` (her father) with
`"vols": [1, 2, 3, 4]` and `walter-blythe` with `"vols": [5]`, `source: "bare_by_volume"`. Both
entries are `ambiguous: true`, so the wikifier never links the bare name; the automaton indexes
each owner only in its own volumes (`MentionAutomaton.find_all(text, vol)`), and `surface_inventory`
counts a claimant whose `vols` are disjoint from every earlier claimant's as `indexed`, not dropped.
A bare name that goes to one full name in every volume where it is used is merged into it instead
(Anne's "Anne" into `anne-shirley`). Surnames, names the text gives several honorifics, epithets
and kinship words are never assigned this way.

### 2.2 `mentions.jsonl` — the Aho-Corasick index output

One record per mention, in corpus order. This is the file that makes extraction cheap.

```jsonc
{ "para_id": "v01:c03:p0142", "vol": 1, "entity_id": "shinei-nouzen",
  "surface": "Undertaker", "start": 14, "end": 24 }
```

Overlapping matches are resolved **longest-match-wins** at build time
(`Shinei Nouzen` beats `Nouzen`), so no consumer needs to handle overlaps.

### 2.3 `candidates.jsonl` (intermediate, kept for audit)

```jsonc
{ "surface": "Vladilena Milizé", "count": 892, "first_vol": 1,
  "evidence_para_ids": ["v01:c01:p0007", "..."],
  "signals": ["honorific", "vocative", "para_raid_callsign", "capitalised_bigram"] }
```

---

### 2.4 `surface_forms.jsonl` — exported index vocabulary

Written alongside gazetteer/mentions by normal and `--gate=build` gazetteer builds and nonempty
epithet merges. `wiki audit gazetteer` also regenerates it from the loaded gazetteer, including
legacy builds, alongside its existing roster report. `paths.surface_forms()` follows the active
series and build cutoff, so existing
gazetteer snapshots/rollback include it automatically. Legacy builds may lack the file.

```json
{"text": "Holo", "entity_id": "holo", "first_vol": 1, "source": "corpus", "ambiguous": false, "indexed": true, "index_entity_id": "holo"}
```

One row per gazetteer surface entry, sorted by `entity_id`, then exact `text`. Fields come
deterministically from the gazetteer, with no LLM calls. `source: "unknown"` explicitly marks
legacy absent provenance. `first_vol` falls back to the entity's first volume for legacy rows.
`ambiguous` preserves the source flag and also detects exact collisions between distinct entities.
`indexed` is true only for the entry actually inserted into the mention automaton;
`index_entity_id` identifies the alphabetically first claimant. Collision losers are retained
with `indexed: false`. Empty strings have `indexed: false`, `index_entity_id: null`.
This exports the build's complete vocabulary, including future surfaces in render mode;
it is a research artifact, not a cutoff-filtered reader page.

---

## 3. Claims — `data/03_claims/v{NN}.jsonl` + `data/03_claims/scene_v{NN}.jsonl`

The atomic unit of the whole system. Defined in `extract/schema.py`. As of Phase 22 C1, TWO
producers write claims in this identical shape, to two separate files `wiki graph build` unions
(deduping any coincidental `claim_id` collision by merging evidence, `cli.py::_merge_claims_by_id`
— `graph/store.py::write_claims` is `INSERT OR REPLACE` regardless, so a raw duplicate never
crashes the build):

- `wiki scenes` (`extract/scenes.py`, via each chapter span's `participant_facts` — see §3b) ->
  `scene_v{NN}.jsonl`. THE PRIMARY source as of C1 — every participant in every span, 100%
  chapter coverage by construction.
- `wiki extract` (`extract/claims.py`, the original mention-window pass) -> `v{NN}.jsonl`. Demoted
  to a targeted top-up by Phase 22 C2 (not yet built as of this writing — today it still reruns
  the full mention-window pass unchanged).

```jsonc
{
  "claim_id":   "c_8f2a1e9c",        // sha1 of (subject, predicate, object_norm, first_vol,
                                        //          polarity, normalized_qualifier)[:8]
  "subject":    "shinei-nouzen",     // entity_id, always resolved -- never a raw surface form
  "predicate":  "AFFILIATED_WITH",   // from config/extraction.yaml; SCREAMING_SNAKE_CASE
  "kind":       "relation",          // attribute | relation | trait
  "object":     "spearhead-squadron",// entity_id when kind=relation
  "value":      null,                // scalar when kind=attribute or trait; null for relation
  "qualifier":  "leader",            // optional refinement, free text, short
  "first_vol":  1,
  "chapter_idx": 0,
  "evidence": [
    { "para_id": "v01:c00:p0031", "print_page": 12,
      "quote": "the Spearhead Squadron's captain, code name Undertaker" }
  ],
  "confidence": 0.92,                // extractor's self-reported confidence, 0-1
  "polarity":   "asserted",          // asserted | denied | presumed  -- see below
  "source":     "qwen2.5:14b-instruct-q4_K_M"
}
```

### 3.1 The three claim kinds

| `kind` | `object` | `value` | Example |
|---|---|---|---|
| `attribute` | null | scalar | `(shinei-nouzen, AGE, null, "16")` |
| `relation` | entity_id | null | `(shinei-nouzen, SIBLING_OF, kiriya-nouzen)` |
| `trait` | null | short phrase | `(shinei-nouzen, PERSONALITY, null, "outwardly detached")` |

### 3.2 `polarity` — why it exists

`presumed` is essential for this genre and must not be collapsed into `asserted`. A character
believed dead in Volume 2 who returns in Volume 5 generates a `presumed` STATUS claim at V2, not a
false one. At cutoff 2 the page renders "presumed dead"; at cutoff 5 it renders "alive" with the
V2 belief in the expandable history. `denied` records an explicit negation in the text.

The three polarity strings are a closed vocabulary. A missing polarity still uses the documented
`asserted` default; an unknown string is quarantined as `invalid_polarity`, never strengthened to
an assertion. Polarity and the case/whitespace-normalized qualifier are part of claim identity, so
observations that disagree on either cannot merge merely because their core tuple matches.

### 3.3 Rules extraction must obey

- `subject` and `object` are **always** `entity_id`s. If the extractor cannot resolve a surface form
  to a gazetteer entity, it drops the claim rather than inventing an entity.
- `resolve.py::resolve_surface` (shared by `claims.py` and `scenes.py`) resolves a surface exactly
  first, then via a whole-word name-part fallback: a bare fragment ("Kraft") resolves to a
  candidate whose surface contains it as a complete word ("Kraft Lawrence"), never as a raw
  character substring ("man" no longer misresolves against "Norman") — and when more than one
  candidate qualifies, the longest (most specific) surface wins, not the first in list order
  (Phase 22 B2, fixes S3).
- Every claim carries at least one evidence item with a real `para_id` and a **verbatim** quote drawn
  from that paragraph. `wiki audit claims` verifies quotes appear in their cited paragraph.
- `first_vol` is the volume of the evidence, never inferred from content.
- Trait values are short noun or adjective phrases, not sentences.

Claim extraction fits each initial request to a positive `num_ctx` declared by the resolved
profile, reserving the stage's `num_predict` output budget and
`extraction.window.context_safety_tokens` (default 256). The estimate includes the system prompt,
entity/alias roster and paragraph labels. Oversized windows split at paragraph boundaries;
an indivisible oversized paragraph is skipped, never partially sent. The existing per-volume
`yield.json` drops map records `context_window_split` (split operations, **not lost facts**) and
`context_window_overflow` (unsendable single-paragraph requests). Splitting occurs after window
selection, so it can increase calls beyond the selection cap. Profiles without `num_ctx` retain
existing behavior. This is the shared character-count estimate, not an exact tokenizer guarantee;
JSON repair retries add context after this initial-request check.

### 3.4 Chapter-scoped extraction (Phase 11) — `extract --volumes <one> --chapters <spec>`

Same merge-not-overwrite behavior as §1.4, via the same `cli.py::_merge_by_chapter`: only the
requested chapters' windows are built and sent to the model (mentions are filtered by the chapter
parsed out of `para_id`, since mention records carry no `chapter_idx` field of their own — see
`cli.py::_chapter_of_para_id`), and the resulting claims replace only that volume file's claims
for those chapters, leaving every other chapter's claims untouched. Skip/force is per-chapter,
keyed off which chapters already have at least one claim on disk — a chapter with **legitimately
zero** claims (no character evidence, e.g. a short interlude) is therefore always re-attempted on
a later call; this is deliberate and harmless, since `llm/cache.py` makes the repeat free.

### 3.5 Source observations — version 1 shadow contract

`extract/schema.py::Observation` preserves one accepted fact occurrence before any cross-window,
cross-chapter, or cross-producer merge. Production readers still consume claims; observations are
currently a shadow artifact used to prove migration and projection semantics before a graph
cutover. The first deterministic replay is
`docs/eval/observation_shadow_spice-and-wolf.jsonl`, generated from recorded `calls.jsonl` by
`scripts/eval/audit_observation_semantics.py` without model calls.

```jsonc
{
  "schema_version": 1,
  "observation_id": "o_1eff590af2cb032d", // sha256 of schema/series/run/cache/fact position
  "series_id": "spice-and-wolf",
  "subject": "liebert",
  "predicate": "ENEMY_OF",
  "kind": "relation",
  "object": "norah-arendt",
  "value": null,
  "qualifier": "betrayer who planned to kill her",
  "polarity": "asserted",
  "confidence": 1.0,
  "disclosure_vol": 2,                    // evidence volume; never inferred story time
  "chapter_idx": 5,
  "evidence": {"para_id": "v02:c05:p0418", "print_page": 112, "quote": "..."},
  "source_stage": "claim_extract",       // claim_extract | scene_extract
  "source_run_id": "20260922T212119-extract-v1-2",
  "source_cache_key": "...",
  "source_fact_index": "0:4",            // participant-group index : fact index
  "source_prompt_sha256": "...",
  "extraction_source": "google:gemini-3.1-flash-lite",
  "candidate_assertion_id": "c_1259d74d",
  "legacy_claim_id": "c_9f1203ad"
}
```

`observation_id` identifies the exact recorded source occurrence, not a proposition. Reordering
responses cannot change it. `candidate_assertion_id` applies the current claim semantic key to
that occurrence's own polarity and qualifier; it is a migration candidate, not a declaration
that two differently worded qualifiers are truly different assertions. Assertion equivalence and
developments are separate later decisions. `legacy_claim_id` is nullable migration lineage and
must never be recomputed as if it carried the observation's semantics.

---

## 3b. Scene records — `data/02b_scenes/v{NN}.jsonl` (Phase 18)

Produced by `wiki scenes` (`extract/scenes.py`). The chapter-major **second pass** — additive to
`data/03_claims/`, never a replacement. One record per chapter **span** (a `~4k-token overlapping
window`; see `extract/spans.py`). A chapter's span records, taken together, cover **every paragraph
in that chapter exactly once** via their `core_para_ids` — this 100%-coverage guarantee is the
structural purpose of this pass.

```jsonc
{
  "scene_id":      "sn_v01c03s002",    // stable: vol, chapter_idx, span_index
  "vol":           1,
  "chapter_idx":   3,
  "span_index":    2,                  // 0-indexed span position within the chapter
  "para_ids":      ["v01:c03:p0082", "v01:c03:p0083", "…"],  // all paras in this span
                                        // (core + overlap context, see §3b design note)
  "core_para_ids": ["v01:c03:p0082", "v01:c03:p0083"],       // the paragraphs ONLY this
                                        // span covers (no overlap); partitions the chapter
  "participants":  ["shinei-nouzen", "vladilena-milize"],    // entity_ids, resolved
  "location":      "spearhead-base",   // entity_id of type LOCATION, or null
  "beat_summary":  "Shin briefs the squadron before the next sortie, reminding them of the plan.",
                                        // THE ONE FIELD NOT VERBATIM-QUOTE-GATED. Free
                                        // paraphrase of the span; 2-4 sentences. See below.
  "summary_para_ids": ["v01:c03:p0082", "v01:c03:p0084"],
                                        // [34] the 1-4 paragraphs the summary rests on, named
                                        // by the model, kept only if they are this span's own
                                        // ids; what a page cites for the summary. [] before [34].
  "state_changes": [
    {
      "subject":   "shinei-nouzen",     // entity_id
      "predicate": "RANK",              // attribute/relation predicate, or null when none fits
      "from_value": "Second Lieutenant",
      "to_value":  "Captain",
      "object":    null,                // entity_id for a relation state change
      "note":      "Promoted mid-chapter after the sortie.",  // ALWAYS required
      "evidence":  [{ "para_id": "v01:c03:p0084", "print_page": 47,
                      "quote": "…a verbatim excerpt…" }]      // same verbatim discipline as §3
    }
  ],
  "quotes": [
    { "speaker":   "shinei-nouzen",    // entity_id or null
      "addressee": null,               // entity_id or null
      "para_id":   "v01:c03:p0031", "print_page": 9,
      "quote":     "Stick to the plan and watch each other's backs." }
  ],
  "participant_facts": [   // Phase 22 C1 -- consumed into data/03_claims/scene_v{NN}.jsonl (§3),
                            // NOT part of the on-disk SceneRecord itself; shown here for context
    { "subject": "shinei-nouzen",
      "facts": [ { "predicate": "AFFILIATED_WITH", "kind": "relation", "object": "spearhead-squadron",
                   "value": null, "qualifier": "captain", "para_id": "v01:c00:p0031",
                   "quote": "the Spearhead Squadron's captain, code name Undertaker",
                   "confidence": 0.92, "polarity": "asserted" } ] }
  ],
  "confidence":    0.85,
  "source":        "qwen2.5:14b"
}
```

**`beat_summary` is the one exception to the verbatim-quote rule.** Every other field —
`participants`, `location`, speakers/addressees, `state_changes.subject`/`object`, and epithet
`entity_id`s — is resolved against entities already known-mentioned in **this chapter only**
(`extract/resolve.py::resolve_surface`), exactly the same way `claims.py` never resolves a
relation object against the whole gazetteer. `state_changes` and `quotes` each require a
`para_id` within the span and a verbatim `quote` drawn from that paragraph (`wiki audit scenes`
verifies this). A `SceneRecord` is always written even when the model found nothing structured
— an empty span is proof it looked, not the same as never having read the passage.

**Overlap vs. core.** `para_ids` is the full span (core + trailing overlap context for
antecedent resolution across boundaries). `core_para_ids` is the unique, non-overlapping slice —
the union of every chapter's `core_para_ids` equals every paragraph in that chapter with no
gaps and no duplicates. Quotes and epithets in the overlap region are deduped across spans by
`(para_id, quote)` so a quote in one span's tail that was already reported by the earlier
span's own core is not double-counted.

**Design note — the `SceneRecord` itself is still not consumed downstream** (graph / synthesize /
site read only `data/03_claims/`): it exists as structured provenance for Phase 19's event layer
and for `wiki audit scenes`. What each span DOES feed downstream, as of Phase 22 C1, is its
`participant_facts` — converted 1:1 into §3 claim dicts and written to
`data/03_claims/scene_v{NN}.jsonl`, which `wiki graph build` reads exactly like `wiki extract`'s
own claim file. See §3 above.

**Phase 22 B1 — one span is two calls, not one.** `extract/scenes.py` now makes a "beat" call
(`participants`/`location`/`beat_summary`) and a "detail" call (`state_changes`/`quotes`/
`epithets`/`participant_facts`) per span, both built from the identical passage-text/known-
entities prefix so a caching provider reuses it. The on-disk `SceneRecord` shape above is
unchanged — the split is purely an internal call-count/prompt-budget decision (`docs/PROMPTS.md`
`scene_extract`); `confidence` is `min()` of the two calls' self-reported confidences.
`participant_facts` was folded into the existing detail call rather than added as a third
(Phase 22 C1), so this costs zero extra calls per span.

### 3b.1 Epithet records — `data/02b_scenes/epithets_v{NN}.jsonl`

Produced alongside scene records by `wiki scenes`. One record per **epithet mention** the model
observed — a descriptive nickname used instead of a name ("the wisewolf", "the merchant") that
refers to an entity already named elsewhere in the chapter. Written here pending review; merged
into `gazetteer.json`'s `surface_forms` by `wiki gazetteer --merge-epithets` (rebuilds the
automaton + mention index in place, no LLM call).

```jsonc
{ "entity_id":   "holo",              // resolved against gazetteer (same-chapter-mention check)
  "text":        "the wisewolf",      // the epithet surface form as it appeared
  "vol":         1, "chapter_idx": 3,
  "para_id":     "v01:c03:p0049", "print_page": 23,
  "quote":       "the wisewolf smiled and tilted her head",  // verbatim, from para_id
  "confidence":  0.82,
  "source":      "qwen2.5:14b" }
```

`min_epithet_confidence` in `config/extraction.yaml`'s `extraction.scene:` block (default 0.6)
gates which records `wiki gazetteer --merge-epithets` actually folds in — records below the
threshold remain in the file for audit but are not promoted. This threshold is intentionally
higher than `extraction.confidence.min_to_keep` (0.5) because an epithet mutates the gazetteer
(a higher-stakes write than a disk-only scene record). `wiki audit scenes` prints the mined
epithet table for manual review before the merge.

---

## 4. Graph — `data/04_graph/graph.db` (SQLite)

```sql
entities(entity_id PK, canonical, type, first_vol, importance, aliases_json)
claims(claim_id PK, subject, predicate, kind, object, value, qualifier,
       first_vol, confidence, polarity, evidence_json, source)
intervals(interval_id PK, subject, predicate, object, value, qualifier,
          vol_start, vol_end, claim_ids_json, superseded_by)
  -- vol_end IS NULL means "still true at the last ingested volume"
edges(subject, predicate, object, vol_start, vol_end)   -- relation intervals, for PPR
mention_counts(entity_id, vol, count)
```

`intervals` is the table the site actually renders from. `claims` is kept for provenance and audit.

### 4.1 The queries every renderer uses

```python
state_at(entity_id, vol)      # intervals where vol_start <= vol AND (vol_end IS NULL OR vol_end >= vol)
history_at(entity_id, vol)    # intervals where vol_end IS NOT NULL AND vol_end < vol
relations_at(entity_id, vol)  # current relation intervals with entity as subject OR object
relation_history_at(entity_id, vol)  # closed relation intervals touching either side
all_edges_at(vol)             # every `edges` row visible at vol -- graph/ppr.py's whole-graph read
evidence_at(claim_ids, vol)   # claims.evidence, filtered to EACH quote's own para_id volume
```

All six interval/edge readers filter `vol_start <= vol` (`evidence_at` filters each quote's own volume instead, since
`claims` carries no `vol_start`/`vol_end` of its own — see below). Nothing else may read
`intervals`, `edges`, or `claims.evidence_json` directly — go through `graph/temporal.py`, so the
spoiler filter cannot be forgotten at a call site.

`state_at`/`history_at` only ever return rows where `entity_id` is the stored `subject` — correct
for attributes and traits, which are never symmetric. A relation, though, can be canonicalised
(§4.2) onto either side regardless of whose page is being built, so both relation queries check
`subject = ? OR object = ?`; `relations_at` returns current rows and `relation_history_at`
returns closed rows. A caller building "this character's relations" from subject-only
`state_at`/`history_at` silently misses every relation where this entity ended up on the
`object` side. Publication consumers opt into the history query only when they render an
explicit historical or ended relationship.

**Relation intervals/edges never close, with one exception (Phase 17).** A relation predicate
declared `conflicts_with` another (§4.2) — today only `FRIEND_OF`/`ENEMY_OF` — supersedes across
volumes exactly like a `single: true` attribute: the old predicate's interval/edge closes
(`vol_end` set) the moment a conflicting predicate opens on the same `(subject, object)` pair.
Every other relation predicate keeps the original permanently-open-interval behaviour — this is
not a general "relations can close" redesign, only conflict-group members ever do.

**A claim's `evidence` array can itself span multiple volumes.** `extract/claims.py` merges
repeated observations of the same fact (same `subject`/`predicate`/`object_norm`/`first_vol`) into
one `claim_id`, so a claim with `first_vol = 1` can still gain a `v03` quote confirming it again
later. `first_vol <= vol` clears the claim (and the interval built from it) for structured
rendering — the *fact* really was known since `first_vol` — but says nothing about which of that
claim's individual quotes are safe to print. Reading `claims.evidence_json` any other way
reproduces the leak CLAUDE.md §1 forbids; this was found real-data-testing `wiki explain` in Phase
5 and fixed by adding `evidence_at`.

### 4.2 `contradictions.json`

Every conflict, its classification and its resolution. This file is the Phase 4 deliverable.

```jsonc
{
  "conflicts": [
    { "subject": "shinei-nouzen", "predicate": "RANK",
      "kind": "narrative_change",              // narrative_change | extraction_error
      "resolution": "supersede",               // supersede | keep_a | keep_b | keep_both | flag
      "a": { "value": "Second Lieutenant", "vol": 2, "confidence": 0.9, "claim_id": "c_1a2b3c4d" },
      "b": { "value": "Captain",           "vol": 6, "confidence": 0.9, "claim_id": "c_5e6f7a8b" },
      "arbiter": "rule:cross_volume",          // rule:* or a model id when escalated
      "rationale": "Different volumes; treated as promotion. V2 interval closed at vol_end=5." }
  ],
  "summary": { "total": 214, "narrative_change": 188, "extraction_error": 26, "flagged": 3 }
}
```

`kind` classification rule: **same volume → `extraction_error`** (arbitrate; escalate to the API
model when confidences are within 0.1); **different volumes → `narrative_change`** (supersede).
`flagged` conflicts are surfaced in the audit report for a human to look at; they never silently pick
a winner.

**Relation conflicts (Phase 17).** `config/extraction.yaml` `relations.*.conflicts_with` (a
predicate name, required to be declared back by its partner — e.g. `FRIEND_OF: {conflicts_with:
[ENEMY_OF]}` and `ENEMY_OF: {conflicts_with: [FRIEND_OF]}`) marks predicates that cannot both
hold between the same two entities at once. `graph/contradictions.py::_resolve_relation_group`
arbitrates them the same way `_resolve_single_valued` arbitrates a `single: true` attribute value
tie, reusing `arbitrate` unchanged — but since the thing in conflict is *which predicate holds*,
not a value of one fixed predicate, the conflict record's shape differs slightly from the
attribute-value case above: `predicate` is the two candidates joined with `|`
(`"FRIEND_OF|ENEMY_OF"`), an additional `object` key names the other entity, and each side's `a`/
`b` dict carries its own `predicate` key (plus a `value` alias set to that same predicate name, so
existing `a["value"]`/`b["value"]` readers — e.g. the audit report's flagged-conflict printer —
still work unchanged). Same-volume conflict → `extraction_error`; a predicate change across
volumes → `narrative_change`, `resolution: "supersede"`, and the losing predicate's edge closes
(see the §4.1 exception above).

**Value canonicalization (Phase 17).** A `"canonicalization"` key, always present (empty when
nothing merged):
```jsonc
"canonicalization": {
  "merges": [
    { "subject": "shinei-nouzen", "predicate": "PERSONALITY", "canonical": "outwardly detached",
      "variants": [ { "value": "detached on the outside", "claim_id": "c_9f1a2b3c", "first_vol": 3 } ] }
  ],
  "summary": { "groups_merged": 1, "variants_absorbed": 1 }
}
```
One entry per `(subject, predicate)` group where `graph/canonicalize.py` (bge-m3 embeddings,
`config/extraction.yaml` `canonicalize:` block) clustered two or more genuinely different-worded
values into one — never for a group that only had exact-string/case duplicates to begin with,
since `graph/temporal.py::assign_multi_valued` already merges those for free. Applies only to
multi-valued attributes (TITLE/NICKNAME/APPEARANCE) and traits (PERSONALITY/MOTIVATION/SKILL/
FEAR/BACKGROUND) — never to `single: true` attributes (already arbitrated/superseded above) or to
relations (the object is already an `entity_id`, always canonical). The remap happens **before**
`assign_multi_valued` is called — every non-canonical variant's `claim_id` is folded into the
canonical value's interval, so this file's `merges` list is provenance for a decision already
baked into `intervals`/`graph.db`, not a separate pending action. `wiki audit contradictions`
prints a summary table; nothing downstream (`synth/`, `site/`) needed to change, since fewer,
cleaner values simply reach the existing render layer.

**Format-aware value normalization (Phase 22 A2).** `graph/temporal.py::normalize_value(value,
fmt=None)` gained an optional `fmt` argument driven by `attributes.*.format` (`config/
extraction.yaml`), previously unread by anything. Only `fmt="number_or_range"` currently changes
behaviour: spelled-out numbers fold to digits and trailing unit words are stripped, so AGE's
`'25'`, `'twenty-five'`, `'twenty-five years old'` and `'twenty-five years'` all normalize to
`'25'`. Threaded through three call sites:
- `graph/contradictions.py::_resolve_single_valued` collapses same-volume claims by normalized
  value **before** ranking for arbitration (`_collapse_same_normalized_value`) — two phrasings of
  the identical value are a restatement, not a tie, and must never reach `arbitrate`. The losing
  claim_id(s) are not discarded; they ride along as `claim_ids_extra` and end up merged into the
  survivor's interval `claim_ids`, so evidence citations still see every restatement.
- The same function's cross-volume comparison (deciding `narrative_change` vs. no-op) also
  normalizes with `fmt` — this is what stops AGE's four phrasings from producing the spurious
  `narrative_change` conflict the 2026-09-09 audit found (U2).
- `graph/temporal.py::assign_single_valued` also takes `fmt`, for its own defensive
  cross-volume merge (two entries whose values are identical once normalized combine into one
  interval instead of one superseding the other).
`graph/temporal.py::assign_multi_valued` was exact-string-keyed before Phase 22 A2; it now
buckets by `normalize_value(value, fmt)` for attributes/traits (never for a relation's object,
which is already an `entity_id` and must stay exact) — the same casing-insensitivity
`assign_single_valued` already had, extended to the multi-valued path (TITLE/NICKNAME/APPEARANCE).

**Value subsumption (Phase 22 A2).** `graph/canonicalize.py::subsume_values(observations)` — a
second, purely deterministic pruning pass (no embed call), gated on a per-attribute `subsume:
true` key (currently only APPEARANCE, config/extraction.yaml — the "24% of the graph is
APPEARANCE noise" finding). Applied in `build_graph` right after `_run_canonicalization`'s remap
and right before `assign_multi_valued`. Two rules over the group's distinct normalized values: (1)
a value whose tokens are a strict subset of a longer sibling value's tokens is redundant next to
it ("hair" next to "long brown hair") — token-SET containment, not a raw substring check, so "man"
is never dropped just because it shares letters with "woman"; (2) a single-token value standing
alone ("hair", "eyes") is too vague to keep even with no sibling. A dropped value's claim is
untouched in `claims.jsonl` — only what becomes a graph interval is pruned, so the evidence stays
reachable via `wiki explain`/`wiki trace`.

**`conflicts.quiet_change` was removed in Phase 32.** It hid the superseded value of AGE/RANK/TITLE/
OCCUPATION on later pages, so a v2 page erased what v1 had said (`docs/vision/PHASE_32.md`, req. 6).

---

### 4.3 `verification.json` (Phase 22 C3)

A verification PASS, not another extraction pass: `wiki verify --upto N` makes one `verify`-role
LLM call per CHARACTER entity (skipped entirely, no call, when the character has zero currently
visible facts), asking whether each fact already on the graph is actually supported by its own
cited evidence — the backstop for an error that survives extraction (a fact hallucinated about the
wrong entity when two aliases read as two people; a relationship/membership claim the evidence
doesn't actually support). Input is `synth/assemble.py::fact_set_for_verification()` — current
state only (`state_at`/`relations_at`, not `history_at`), each fact paired with up to 20 evidence
quotes via `graph/temporal.py::evidence_at()`, the same path `wiki explain`/`wiki trace` use.
`graph/verify.py::verify_character()` is the one call per character; never mutates a claim,
interval, or page — a flagged fact stays exactly where it was, same "never silently pick a winner"
discipline §4.2's `flag` resolution already established.

Verification prompts also include up to four preceding/following paragraphs around each cited
paragraph, confined to that chapter and volumes at or below `--upto`. The full cited paragraph
is included. Overlapping context is deduplicated by paragraph ID across the character's facts.
Context explains a quote (e.g. a figurative family claim in guild banter); it does not add new
citable evidence or change the persisted fact/claim schema. Missing legacy parsed files or
paragraphs retain quote-only verification. Corrupt context files fail the run before verdicts
are written. Parsed volumes are cached within one verification run, never across cutoffs/runs.

```jsonc
{
  "upto_vol": 2,
  "results": [
    { "entity_id": "kraft-lawrence", "canonical": "Kraft Lawrence", "upto_vol": 2,
      "flagged": [
        { "kind": "relation", "predicate": "AFFILIATED_WITH", "value": "Kraft Lawrence -> Medio",
          "qualifier": null,
          "evidence": [ { "quote": "Medio's men seized him at the border.", "para_id": "v02:c04:p0031" } ],
          "rationale": "The quoted evidence describes Medio capturing Lawrence, not him joining them." }
      ] }
  ],
  "summary": { "characters_checked": 14, "characters_flagged": 1, "facts_flagged": 1, "facts_checked": 30 }
}
```

`facts_checked` and `facts_flagged` count unique interval IDs; a relationship checked from both
characters' sides contributes once to each count. `characters_flagged` and the per-character
`results` still show both perspectives. Duplicate model flags for one fact index are collapsed.

`wiki audit verify` reads this file only (never calls the LLM itself, per every other report in
`audit/reports.py`) and prints each flagged fact with its rationale. It never FAILs on a flagged
fact — a flag is a pointer for a human to look at, the same non-fatal treatment `_contradictions_
report` already gives a `flag`-resolved conflict.

---

## 4b. Event layer — `data/04b_events/events.db` (Phase 19)

The event layer is a **third tier** on top of the existing claims pipeline:

```
scene records (data/02b_scenes/)  →  wiki events-build  →  events.db
```

`claims.py`'s mention-major pass and `contradictions.py`'s interval-building pipeline are
**completely unchanged**. Phase 19 is purely additive — new tables in a separate database, new
code paths in `synth/prose.py`, no modifications to `graph.db`.

### 4b.1 Schema

```sql
events(
    event_id TEXT PRIMARY KEY,    -- "ev_v01c03s002" (mirrors scene_id prefix)
    vol               INTEGER,    -- which volume
    chapter_idx       INTEGER,    -- which chapter
    span_index        INTEGER,    -- which span within the chapter
    scene_id          TEXT,       -- back-reference to data/02b_scenes/ "sn_v01c03s002"
    participants_json TEXT,       -- JSON array of entity_ids
    location          TEXT,       -- entity_id of type LOCATION, or null
    beat_summary      TEXT,       -- the paraphrase summary from the scene record
    core_para_ids_json TEXT,      -- JSON array; the scene's own core_para_ids (§3b) --
                                   -- the paragraphs beat_summary was paraphrased from
    summary_para_ids_json TEXT,   -- [34] JSON array; the scene's summary_para_ids (§3b), what
                                   -- pages cite for the summary ('[]' on an older events.db,
                                   -- which `events.connect` migrates by ALTER TABLE)
    vol_start         INTEGER,    -- always == vol (anchored to its own volume)
    confidence        REAL,
    source            TEXT
)
event_claims(
    claim_id   TEXT PRIMARY KEY,  -- "ec_<sha1[:8]>" -- deterministic, never a UUID
    event_id   TEXT,              -- FK to events.event_id
    kind       TEXT,              -- "state_change" | "quote"
    subject    TEXT,              -- entity_id (state_change: who changes; quote: null)
    predicate  TEXT,              -- attribute/relation predicate or null
    from_value TEXT,              -- state_change: the prior value
    to_value   TEXT,              -- state_change: the new value
    object     TEXT,              -- state_change: FK object entity; quote: addressee entity_id
    note       TEXT,              -- state_change.note (always present for state_change)
    speaker    TEXT,              -- quote: the speaker entity_id
    para_id    TEXT,              -- the paragraph the quote is drawn from
    quote      TEXT,              -- verbatim text
    vol        INTEGER,
    confidence REAL,
    source     TEXT
)
```

**Empty scene spans** (no participants, no state_changes, no quotes) do **not** become event
rows — they are already recorded in `data/02b_scenes/` as proof the model looked and found
nothing narrative. `events.db` is sparse by design.

### 4b.2 Sanctioned reads

```python
events_at(conn, vol)                         # all events with vol_start <= vol, reading order
character_events_at(conn, entity_id, vol)    # events where entity_id is a participant, <= vol
event_claims_at(conn, event_id, vol)         # claims for one event with vol <= vol
```

All three filter `vol_start <= vol` (for `events`) or `vol <= vol` (for `event_claims`) before
returning — the same spoiler-fence discipline as `graph/temporal.py::state_at`. **Nothing outside
`graph/events.py` may query these tables directly.**

`beat_summary` is the one un-gated field — it is a genuine paraphrase produced by the scene
extraction LLM, not a verbatim quote from the source text. It is safe to use in a prose prompt
without a per-volume reveal check. Everything else (participant lists, `event_claims.quote`,
`location`) comes verbatim from scene records that have already been checked by `wiki audit scenes`.

`core_para_ids_json` is `beat_summary`'s real evidence — the paragraphs it was paraphrased from.
`synth/prose.py::generate_chronology` deliberately does **not** fold this into the page's own
`evidence` field: a span's `core_para_ids` is chapter-scale (tens to 100+ paragraphs), so treating
it as a citation would bloat every character's stored page evidence into the thousands without
making the field more precise. `wiki audit eval` (`eval/gold.py::score_citation_entailment`)
checks this broader grounding on demand, straight from `events.db`, instead — see its own
docstring. (Found by Phase 21 part 4's evaluation harness: a beat_summary-only span, with no
state_change/quote, previously contributed nothing to `event_claims`-derived `evidence`, making a
well-grounded chronology look uncited.)

### 4b.3 Relationship to `graph.db`

`events.db` is a **separate file** from `graph.db`. `wiki graph build` reset/rollback semantics
are completely undisturbed — it never touches `events.db`. `wiki events-build --force` resets
`events.db` only.

`event_claims` rows are **not** in `data/03_claims/`. They do not go through
`graph/contradictions.py`'s arbitration/supersession pipeline and do not produce `intervals`
rows. They are read directly by `synth/prose.py::generate_chronology` (for the Chronology prose
section) and by `wiki audit events` (for the coverage report).

### 4b.4 Pipeline order (Phase 19)

```
ingest → gazetteer → scenes → gazetteer --merge-epithets → extract
       → graph build → events-build → synthesize → site build
```

`synthesize` opens `events.db` if it exists and passes the connection to `generate_prose()` as
`events_conn`. When `events.db` is absent (events-build not yet run), the `chronology` prose
section is silently `null` — no LLM call, same as a section with no trait evidence.

---

## 5. Page model — `data/05_pages/{entity_id}/v{NN}.json`

One file per character per volume cutoff **that differs from the previous cutoff** (see §5.1). This
is what `synth/assemble.py` builds and what the SPA ultimately renders.

```jsonc
{
  "entity_id": "shinei-nouzen",
  "canonical": "Shinei Nouzen",
  "upto_vol": 3,
  "claim_set_hash": "a91f...",       // sha256 of the sorted visible interval IDs; the cache key
  "fields": {
    // One key per config/extraction.yaml `attributes` predicate, lowercased verbatim (Phase 5
    // took that file's own header comment literally: "Adding a predicate or attribute here is
    // all that is needed -- rendering is generic." No predicate is special-cased by name here.
    // `single: true` (AGE/GENDER/STATUS/RANK/ORIGIN/OCCUPATION in the 86 config) renders as ONE
    // scalar object with supersession history; everything else (TITLE/NICKNAME/APPEARANCE) is
    // genuinely multi-valued and renders as a flat list with NO history, because
    // graph/temporal.py's assign_multi_valued never produces a vol_end/superseded_by for these
    // to begin with -- two nicknames coexisting is not a change, so there is nothing to record
    // "previously" for a nickname the way there is for a superseded rank.
    "age":          { "value": "16", "since_vol": 1, "show_vol": false,
                       "polarity": "asserted", "history": [], "inferred": false },
    "status":       { "value": "active", "since_vol": 2, "show_vol": true,
                       "polarity": "asserted", "inferred": false,
                       "history": [ { "value": "missing in action", "vols": [1, 1],
                                      "polarity": "presumed" } ] },
    "rank":         { "value": "Captain", "since_vol": 3, "show_vol": true,
                       "polarity": "asserted", "inferred": false,
                       "history": [ { "value": "Second Lieutenant", "vols": [2, 2],
                                      "polarity": "asserted" } ] },
    "title":        [ { "value": "Handler One", "since_vol": 1, "show_vol": false } ],
    "nickname":     [ { "value": "Undertaker", "since_vol": 1, "show_vol": false },
                      { "value": "Baleygr",    "since_vol": 3, "show_vol": true } ],
    // Relations are the one exception to "generic per predicate": AFFILIATED_WITH (identified by
    // name, not a config flag) renders into `affiliations`; every OTHER relation predicate
    // renders into one `relationships` list (the entry's own `predicate` field, not the list
    // key, carries which relation it is -- SIBLING_OF, FRIEND_OF, COMMANDS, ...).
    "affiliations": [ { "entity_id": "spearhead-squadron", "role": "leader",
                        "vols": [1, 1], "current": false },
                      { "entity_id": "nordlicht-squadron", "role": "member",
                        "vols": [2, null], "current": true } ],
    "relationships":[ { "entity_id": "kiriya-nouzen", "predicate": "SIBLING_OF",
                        "label": "Sibling of", "note": "elder brother",
                        "blurb": "Sibling of — elder brother", "since_vol": 1,
                        "show_vol": false, "current": true, "vol_end": null } ]
  },
  "lead": "presumed dead · Nordlicht Squadron",   // (Phase 22 A5, S6) a compact one-line summary
                                       // -- current status (polarity-prefixed) and primary
                                       // affiliation, or `null` when neither is known yet.
                                       // Built purely from `fields` above; no new evidence read.
  "prose": {
    // One key per config/extraction.yaml `page_outline` section whose `kind` is `prose`
    // (Phase 13) -- generic over the section list, same discipline as `fields` above. The base
    // taxonomy declares three: `chronology` (Phase 19, source: events), `background`,
    // `personality`; a per-series overlay may declare more.
    "background":  { "text": "...", "evidence": ["v01:c00:p0031", "v03:c02:p0410"] },
    "personality": { "text": "...", "evidence": ["v01:c05:p0221"] }
    // [34] `evidence` is the paragraphs of the input lines the model NAMED: every fact or scene
    // line in the prompt carries a key ([F1], [S2]; [R1] in relationship prose, [W1] in a codex
    // summary) and the answer's "cited" lists the keys used. An unknown key cites nothing; an
    // answer naming no valid key keeps every fed paragraph (the pre-[34] rule). A text that says
    // it found nothing "in the provided events" is `null` (synth/prose.py::_is_no_evidence_answer).
  },
  // Present (key always exists, value may be null) only when page_outline declares a
  // `kind: quotes` section (Phase 20). One entry per selected quote, top-N by confidence,
  // deduplicated by quote-text prefix, deterministic -- no LLM call. `null` when events.db is
  // absent or this character has no visible quote claims at this cutoff.
  "quotes": [
    { "quote": "I am Shinei Nouzen.", "speaker": "shinei-nouzen", "para_id": "v01:c02:p0044",
      "vol": 1 }
  ],
  "mentions_of": [   // graph/ppr.py's Personalized PageRank, ranked, seed excluded (Phase 5).
                     // Phase 22 A5 (U5): filtered to drop anyone already in fields.affiliations/
                     // relationships (no longer duplicative), and `note` explains the connection
                     // via graph/temporal.py::relations_between or graph/events.py::
                     // shared_events_at -- `null` for a genuine multi-hop-only PPR result.
    { "entity_id": "vladilena-milize", "note": "3 shared scenes" },
    { "entity_id": "kiriya-nouzen", "note": null }
  ],
  "generated_by": "google:gemini-flash-latest"          // str(Profile) of whichever role actually
                                       // answered (`prose` or `prose_polish`, after key-fallback);
                                       // `null` when both prose fields are `null` (Phase 6)
}
```

`history` arrays contain **only** intervals ending at or before `upto_vol` — their `vols` bounds
are real numbers, safe to print, and every one is rendered as "previously: X (vA–vB)". A scalar
field's *current* value says nothing about its future: its interval's real `vol_end` may exist
but is never serialised. Phase 32 also removed the `changes_later` boolean, because telling a v1
reader that a value changes later is itself a spoiler (req. 9). `affiliations` entries scrub
`vols`/`current` the same way.

**`show_vol` (Phase 22 A5, U3)** is on every scalar field, every list-field entry, and every
`relationships` entry (never on `affiliations`, whose `vols` pair is a genuinely different
"membership span" kind of information) — `true` only when `since_vol` is later than the entity's
own `first_vol`, i.e. a real reveal. `site/okf.py`/`app.js` print the "(since vN)" marker only
when this is `true`; a value known since the character's first appearance renders bare. History/
expandable spans (the `history` array, a relationship's "ended vN") are unaffected either way.

**`inferred` (Phase 22 A5, U6)** is on every scalar field, `true` only for one built by
`attributes.*.default` (`config/extraction.yaml`) when zero rows exist for that predicate at all
— e.g. STATUS defaults to `"alive"`. Carries no `claim_ids`-backed evidence and never affects
`claim_set_hash`; `site/okf.py`/`app.js` render it with a "(assumed)" note.

**`polarity` (Phase 22 A5, U7)** on a scalar field (current value and every `history` entry) now
actually changes what renders: `site/okf.py`/`app.js` prefix "presumed "/"not " onto the value
when `polarity` is `"presumed"`/`"denied"` — "presumed dead" no longer collapses to
indistinguishable from "dead", the exact collapse `config/extraction.yaml`'s own `polarity:`
block says must never happen. `synth/assemble.py::_claim_polarity` picks the MOST-RECENT visible
claim's polarity among an interval's (possibly multi-claim, oldest-to-newest) `claim_ids` — not
`claim_ids[0]` — so a later confirmation is reflected and a not-yet-visible claim's polarity is
never read.

**`relationships` entries (Phase 22 A5, U4)**: `label` is always the predicate's config `display`
text now, never overridden by a free-text `qualifier` (the audit's cryptic badges: "Comrade of" /
"wife (false identity claimed)" / "fake" for one predicate, because the old code let `qualifier`
win outright). `qualifier` is demoted to `note` (`null` when absent) and combined into `blurb`
via the predicate's optional `relations.*.blurb` template or the generic `"{label} — {note}"`
fallback — render `blurb`, not `label`, for the full text. `current`/`vol_end` are the same
closed/live scrub `affiliations` already had — a `conflicts_with`-superseded relation (FRIEND_OF
closed by ENEMY_OF) now renders `current: false` instead of looking indistinguishable from a
still-live one.

**Traits (PERSONALITY/MOTIVATION/SKILL/FEAR/BACKGROUND) are never a `fields` entry.**
`config/extraction.yaml` already says why: they "feed the personality prose... not themselves
rendered as paragraphs." `synth/assemble.py::trait_values_at()` reads them for Phase 6's prose
prompt only.

**`prose` is generated from TRAIT claims only (Phase 6, `synth/prose.py`), never from `fields`.**
Each `page_outline` `kind: prose` section names its own `traits` list — `background` reads
BACKGROUND; `personality` reads PERSONALITY/MOTIVATION/SKILL/FEAR in the base taxonomy (Phase 13
moved this split from a hardcoded pair of predicate tuples into `config/extraction.yaml`;
`docs/PROMPTS.md` still documents the same two prompts). A section is `null`, with **no LLM call
made**, when the character has no trait claims of that kind visible at this cutoff — nothing to
describe, and no reason to spend a token asking a model to invent 2-4 sentences from zero
evidence. `evidence` is not a model-reported citation; it is the exact set of `para_id`s the
prompt was built from (via `graph/temporal.py::evidence_at`, cutoff-filtered per quote — see the
Phase 5 spoiler-leak note above, which applies to this prompt exactly as much as it does to
`wiki explain`), so it cannot be falsified or omitted by paraphrase. `wiki explain "<name>" --upto
N` remains the tool for inspecting a page's structured fields and their evidence before spending
any tokens; `wiki synthesize --upto N` is what actually writes `prose` and the files below.

**`page_outline` (`config/extraction.yaml`, Phase 13) is the closed vocabulary of CHARACTER page
sections** — a dict-of-dicts keyed by section, in the same shape as `attributes`/`relations`/
`traits` (so a per-series overlay can add, override, or `null`-delete one section without
repeating the rest). Each entry carries `order` (sequencing), `kind` (one of `fields`,
`affiliations`, `relationships`, `prose`, `derived`, `quotes` (Phase 20), `lead` (Phase 22 A5,
S6) — the switch every consumer dispatches on), `title` (display heading — `""` for `lead`,
since it renders as a bare paragraph with no heading), and, for `kind: prose`, `traits`/
`min_sentences`/`max_sentences`/`subject`/`source` (the last two feed the LLM system prompt,
falling back to generic text derived from `title` if omitted). `synth/assemble.py`,
`synth/prose.py`, `site/okf.py`, `site/bundle.py` (which republishes it into `index.json`'s
`sections` key, §6) and `site/templates/app.js` all read this instead of hard-coding a section
list — see `docs/handover/PHASE_13.md`.

**Phase 20 additions to `page_outline`:**
- `sentences_per_evidence` (optional, any `kind: prose` section): when set, `synth/prose.py::
  _adaptive_bounds(section, evidence_count)` scales the sentence bounds actually sent to the LLM
  system prompt with how much evidence exists — `min_sentences`/`max_sentences` become the
  absolute floor/ceiling instead of the fixed bounds. `raw = evidence_count * sentences_per_evidence`;
  `min_s = clamp(round(raw * 0.5), floor, ceil)`; `max_s = clamp(max(min_s + 1, ceil(raw)), floor,
  ceil)`. A section without `sentences_per_evidence` behaves exactly as before Phase 20 (fixed
  bounds, unaffected). This lets a character with 12 BACKGROUND facts get more prose than one
  with 2, without a global cap.
- `kind: quotes` (new section kind): renders `page["quotes"]` (see above) — deterministic, no LLM.
  Config carries `max_quotes` (default 3) instead of `traits`/sentence bounds.
  `synth/assemble.py::build_quotes_section(events_conn, entity_id, upto_vol, section)` selects the
  top-N `event_claims` (Phase 19, `kind="quote"`) whose `speaker` matches the entity (by
  `entity_id` or, if the caller sets `section["_entity_canonical"]`, the character's canonical
  name — scene extraction sometimes records the surface form rather than the resolved id), sorted
  by `confidence` descending and deduplicated by the quote's first 60 characters. Spoiler safety
  is inherited unchanged from `character_events_at`/`event_claims_at` (§4b) — no new filtering
  logic. Returns `None` (not called from the cache-gated prose path — always recomputed at
  `wiki synthesize` time, since it is cheap and deterministic) when `events_conn` is `None`, or
  no qualifying quotes exist for this character at this cutoff.
- `kind: lead` (Phase 22 A5, S6): renders `page["lead"]` (see above) as a bare paragraph with no
  `## ` heading (`site/okf.py::render_markdown` special-cases this kind rather than routing it
  through the normal per-section heading loop; `app.js` renders it above the infobox/body
  layout, not inside either column). Config carries no extra keys beyond `order`/`kind`/`title`.

**Page gate (Phase 22 A5, S5)**: `config/extraction.yaml` `page_gate.min_claims` (default `1`) is
the floor `wiki synthesize` checks via `synth/assemble.py::has_min_evidence` BEFORE calling
`assemble_page` at all — a character+cutoff with fewer distinct visible intervals than this gets
no page file written (counted separately from the cache-skip count in the command's summary
line), rather than a page with nothing on it. `find_name_collisions()`, a separate, non-cutoff-
scoped check over the whole gazetteer, prints a build-time warning (never fails the build) when
two entities of different types share one canonical name (`entity_id`s are already guaranteed
distinct — `entities/alias.py`'s numeric-suffix disambiguation — so this is a display-name
collision, e.g. a character and the place he is named after, not an id collision).

### 5.1 Cache semantics

`claim_set_hash` is the hash of the visible interval set. If it is unchanged from cutoff N-1, the
page is not regenerated and the SPA bundle points cutoff N at the N-1 file. This is what collapses
13 cutoffs x N characters down to roughly 2–3 generations per character.

`synth/cache.py::previous_page(entity_id, upto_vol)` (Phase 6) is the sanctioned way to find "the
N-1 file" — it walks downward from `upto_vol - 1` to the nearest cutoff that actually has a file
on disk, so a gap left by an earlier skip (or a resumed `--upto` run that only wrote some cutoffs
in an earlier invocation) still resolves correctly. `unchanged(prev, claim_set_hash)` is the
skip/write decision. Re-running `wiki synthesize` with the **same** flags over the **same** data is
not itself a no-op at the file-write layer — cutoff N is always compared to cutoff N-1, not to its
own previously-written copy — but it costs no extra LLM spend, since `llm/cache.py` caches on the
exact request and the prompt is byte-identical when nothing changed.

### 5.2 OKF Markdown — `data/05_pages/{entity_id}/v{NN}.md`

The same content emitted as Markdown with YAML front matter (`site/okf.py`, Phase 6), per the Open
Knowledge Format the reference document specifies. Front matter carries `entity_id`, `canonical`,
`type`, `aliases`, `upto_vol`, `categories`, `first_vol`. `categories` and the `Appearances` body
section are not `fields`/`prose` values and this document does not otherwise define their content;
Phase 6's judgment call (see `docs/handover/PHASE_6.md`): `categories` is the sorted list of
`fields.affiliations[].entity_id` already on the page (cutoff-filtered by construction, so no new
read is needed); `Appearances` is every volume number named by any `since_vol`/`vols`/history entry
already in `fields`, formatted as compact ranges — derived from the page dict alone, not a new
evidence read. Body sections follow `page_outline`'s declared order (Phase 13); the base
taxonomy's order is Overview, Affiliations, Relationships, Background, Personality, Appearances.

---

## 6. Site bundle — `data/06_bundle/`

Built by `wiki site build --upto N` (`site/bundle.py`, Phase 7) from `data/05_pages/`, the
gazetteer and `graph.db`. Split so a reader downloads only what they need, and what the MkDocs
wiki emitter (`site/mkdocs_wiki.py`, §6b) consumes to write `dist/<series>/wiki/`. Built ONCE for
the whole run at the given `--upto N`; a reader picks any volume from 1 to `N` client-side (§6's own rule below), never
a volume the bundle was not built for.

| File | Contents |
|---|---|
| `index.json` | Series metadata, volume list, the character roster (per-volume visibility via `first_vol`), `field_labels` (config-driven attribute display names, so the client never hard-codes a predicate name), `sections` (`settings.page_outline`'s `key`/`kind`/`title` list, Phase 13, so the client never hard-codes a page section either), and `relationship_pairs` (Phase 21 — every `[a, b]` pair with a `relationships/` bundle file, so the client never has to speculatively fetch/404 one) |
| `pages/{entity_id}.json` | Sparse multi-cutoff document for one character — §6.2 |
| `codex/factions.json` | Factions and organizations; one sparse multi-cutoff entry per anchor — §6.1 |
| `codex/places.json` | Locations and battles |
| `codex/tech.json` | Technology and Legion units |
| `relationships/{a}--{b}.json` | Pair-scoped relation history + shared scenes for one CHARACTER pair (`{a, b}` sorted, `--`-joined) — §6.4 |
| `timeline/v{NN}.json` | One volume's chapter-by-chapter event recap (Phase 21 part 3) — §6.5 |
| `links.json` | `entity_id` → `{"route", "canonical", "type"}` — §6.3 |
| `search.json` | One entry per surface form actually revealed by the build cutoff, each carrying its OWN `first_vol` (CONTRACTS §2.1: an alias can be revealed later than its entity) |

Every entity referenced anywhere in the bundle is filtered to `first_vol <= upto_vol` before it
reaches a file — not just its text, its very presence as a link target or search result. **The
bundle must never contain anything whose `first_vol` exceeds the cutoff it was built for** — the
spoiler filter runs at build time, not in the browser.

`index.json`'s `relationship_pairs` (§6.4) and `timeline_volumes` (§6.5) exist for the same
reason: so a client never has to speculatively fetch (and 404 on) a `relationships/*.json` or
`timeline/*.json` file that was never written because the pair/volume has no evidence yet.

### 6.1 Codex entry — sparse multi-cutoff, same shape as §6.2

```jsonc
// data/06_bundle/codex/factions.json
{
  "the-spearhead": {
    "first_vol": 1,
    "kind": "factions",
    "cutoffs": {
      "1": {
        "canonical": "The Spearhead",
        "summary": "The Spearhead squadron is a veteran unit of Processors that has fought the Legion for years, suffering heavy casualties.",
        "evidence": ["v01:c03:p0249", "..."],      // [34] the para_ids of the windows the summary names (§5's evidence rule); the summary must pass a `codex_check` model call against its windows (one regeneration, else null; C33 replaced a MiniCheck gate that rejected supported text)
        "members": ["lena"],                        // CHARACTER entities whose relation TO this entry is flagged membership: true (Phase 22 A3)
        "adversaries": [],                            // CHARACTER entities related via ENEMY_OF/RIVAL_OF (Phase 22 A3) -- omitted (not []) when empty
        "related": [],                               // OTHER non-character entities, from this entry's own PPR neighbours (graph/ppr.py)
        "key_events": [                              // FACTION kind only (Phase 21 part 2) — see below
          { "event_id": "ev_v01c03s002", "vol": 1, "chapter_idx": 3, "location": "the barracks",
            "beat_summary": "...", "participants": ["lena", "shinei-nouzen"], "quotes": [] }
        ]
      }
    }
  }
}
```

`summary` is generated once per cutoff at which the entry's STATE changes — and "state" here is
evidence **or** `members` **or** `related` **or** `adversaries` (**or** `key_events`, for a
`factions` entry), not evidence alone (site/bundle.py's `_state_hash`): a faction whose
one-sentence definition has not changed still needs a new cutoff entry the moment a character
newly joins it, or a client resolving "nearest cutoff <= N" (§6.2's rule) would show the stale
member list at N. `summary`/`evidence` are `null`/`[]` — with no LLM call made — when there is no
evidence yet, the same "nothing to describe, no reason to spend a token" rule §5 already
establishes for `background`/`personality`.

`members` (Phase 22 A3, fixing an audit finding S1: `bundle.py::_members_and_related` used to
treat ANY relation touching the entity as membership, so a character's ENEMY_OF edge to the
faction that captured them rendered them a "member" of their own captor): built only from
relations whose `config/extraction.yaml` predicate config sets `membership: true` —
AFFILIATED_WITH/SERVES_UNDER/COMMANDS plus, so `places`/`tech` codex entries keep their
pre-existing "who's associated with this" behaviour, ORIGIN_FROM/PARTICIPATED_IN/PILOTS.
`adversaries` is the same shape, populated from ENEMY_OF/RIVAL_OF edges instead — kept as its own
field rather than dropped, since "who opposes this entity" is real information, just not
membership. Any other relation touching a codex entity (an extraction error under the closed
vocabulary) lands in neither bucket. `adversaries` is sparse the same way `key_events` is: omitted
entirely, not `[]`, when there are none — so a codex entry with no adversary edges keeps a
`_state_hash` byte-identical to before this field existed.

`key_events` (Phase 21 part 2, config-gated by `codex_pages.<kind>.max_key_events` — currently
only `factions`): scenes (`graph/events.py::group_events_at`) where **two or more** of the
entry's current `members` co-appear AND the faction itself is either a participant of the scene or
the scene's `location` (Phase 22 A3 — the co-presence check alone let two factions that share most
of their roster render byte-identical `key_events` lists, since a scene satisfied "2+ members
present" for EITHER faction regardless of which one the scene was actually about), i.e. the faction
acting as a group rather than any scene touching a single member — ranked by member co-presence
count then reading order, capped at `max_key_events`. Fully deterministic, no LLM call, same "no
evidence, nothing invented" rule `relationships/*.json`'s `shared_scenes` follows (§6.4). A
`places`/`tech` entry never carries this key at all (not even `[]`) — `_state_hash` omits it from
the hash payload for those kinds too, so their cache keys are byte-identical to before this field
existed. Each event's `beat_summary` (Phase 22 A4) gets the same `site/wikify.py` hyperlink pass
`summary` above already gets, EXCLUDING a self-link to the codex entry's own `entity_id` — a
faction's own name mentioned in one of its key events does not link back to the page already
displaying it. `chapter_idx` here is the raw 0-based `graph/events.py` value (unlike §6.5's own
top-level `chapters[].chapter_idx`, which is 1-based) — nothing renders it, so it was left alone.

### 6.2 `pages/{entity_id}.json` — the sparse multi-cutoff shape every multi-cutoff bundle file uses

```jsonc
{
  "entity_id": "shinei-nouzen", "canonical": "Shinei Nouzen",
  "cutoffs": {
    "1": { /* the full §5 page dict at upto_vol=1, prose text wikified */ },
    "3": { /* ...at upto_vol=3 — v02 has no key: unchanged from v01, same file synth/cache.py skipped */ }
  }
}
```

Keys are sparse: only the cutoffs `wiki synthesize` actually wrote a `v{NN}.json` for (§5.1's own
skip already did the collapsing — this file does not re-derive it, just carries the same keys
through). A client resolves a selected volume `V` to `max(key for key in cutoffs if key <= V)` —
exactly `synth/cache.py::previous_page`'s own on-disk walk, done client-side instead. Every
`prose.background`/`prose.personality` **text** field has `site/wikify.py`'s Markdown links baked
in (`[surface](route)`, self-links excluded); every other field — `fields.affiliations[].entity_id`,
`fields.relationships[].entity_id`, `mentions_of[]` — stays a bare `entity_id`, resolved against
`links.json` client-side, exactly like a codex entry's `members`/`related`. Structured fields are
never re-derived here; this file is `data/05_pages/<id>/v{NN}.json` copied through unchanged
except for that one wikify pass.

### 6.3 `links.json`

```jsonc
{ "shinei-nouzen": { "route": "/character/shinei-nouzen", "canonical": "Shinei Nouzen", "type": "CHARACTER" } }
```

Every `entity_id` referenced anywhere else in the bundle — a wikified link target, an
affiliation/relationship/`mentions_of`/codex `members`/`related` entry — must resolve here
(`wiki audit links`, Phase 7). `canonical`/`type` are this file's extension beyond a bare route
(judgment call, docs/handover/PHASE_7.md): they let a client render an entity reference's display
name without a second lookup file. `route` alone remains a strict subset of the object, so
anything reading only `.route` still works unchanged.

### 6.4 `relationships/{a}--{b}.json` (Phase 21) — pair-scoped, sparse multi-cutoff, no LLM

```jsonc
// data/06_bundle/relationships/kiriya-nouzen--shinei-nouzen.json
{
  "pair": ["kiriya-nouzen", "shinei-nouzen"],
  "first_vol": 1,
  "cutoffs": {
    "1": {
      "relations": [
        // temporal.py::relations_between rows, `subject`/`object` already correctly oriented
        // by graph/contradictions.py::_canonicalize_relation -- no per-viewer flip needed, since
        // a pair page names both parties explicitly (unlike §5's per-entity `relationships` field).
        { "subject": "kiriya-nouzen", "predicate": "SIBLING_OF", "display": "Sibling of",
          "object": "shinei-nouzen", "qualifier": null, "since_vol": 1 }
      ],
      "history": [],       // superseded relation intervals (only ever non-empty for a Phase 17
                            // `conflicts_with` group, e.g. FRIEND_OF -> ENEMY_OF); same row shape
                            // as `relations`, plus `until_vol`
      "shared_scenes": [
        // graph/events.py::shared_events_at -- events where BOTH pair members are participants
        { "event_id": "ev_v01c03s002", "vol": 1, "chapter_idx": 3, "location": "the barracks",
          "beat_summary": "...", "quotes": [
            { "speaker": "shinei-nouzen", "addressee": "kiriya-nouzen", "quote": "...",
              "para_id": "v01:c03:p0091", "vol": 1 }
          ] }
      ]
    }
  }
}
```

Same sparse-multi-cutoff / nearest-key-`<=`-V client resolution rule as §6.2, and the same
state-hash gating §6.1 uses (`site/bundle.py::_relationship_state_hash`, covering `relations` +
`history` + which `shared_scenes` are present — not scene text, which never changes once
written). Unlike §6.1's codex entry, **no generative step**: `build_relationship_bundles` never
calls an LLM, mirroring §5's `quotes` field (Phase 20), not `codex_summary`. Population is only
CHARACTER–CHARACTER pairs with at least one relation edge anywhere in the corpus — not every
possible pair. A quote's `speaker`/`addressee` may carry a surface form rather than a resolved
`entity_id` (same caveat §5's `quotes` field has); `wiki audit links` does not link-check those
two fields against `links.json` for that reason, though it does check `pair` and every
`relations`/`history` row's `subject`/`object`, which are always resolved entity_ids, and (Phase 22
A4) every `shared_scenes[].beat_summary` for Markdown link resolution. Phase 22 A6 (S7) closes a
narrower hole in the same area: every `shared_scenes[].quotes[]` entry (and every other quote list
in `data/06_bundle/` — a page's own `quotes`, a codex `key_events[].quotes[]`, a timeline event's
`quotes[]`) is now checked for `speaker: null` and FAILs the report if found — a null speaker means
the scene pass could not resolve who said the line, and the audit found 9 such quotes already
shipped on a live bundle unnoticed. This does not fix extraction (Phase B2's job — suppress an
unresolved-speaker quote at the source instead of emitting `speaker: null`); it only stops the
report from vouching for a bundle that already has one. `beat_summary` itself is wikified the same way §6.1's
`summary` is, but with no `exclude_entity_id` — a pair page has no single "self" entity to exclude
a link to, unlike a codex entry or a character page. `wiki audit relationships` is the
spoiler-safety check specific to this file: every cutoff's evidence volumes must be `<=` that
cutoff key, and `index.json`'s `relationship_pairs` must match the files actually on disk.

### 6.5 `timeline/v{NN}.json` (Phase 21 part 3) — one volume's recap, no LLM, no sparse cutoffs

```jsonc
// data/06_bundle/timeline/v01.json
{
  "vol": 1,
  "chapters": [
    {
      "chapter_idx": 1,
      "events": [
        {
          "event_id": "ev_v01c01s001", "vol": 1, "location": null,
          "beat_summary": "While camping alone at night, Lawrence discovers Holo sleeping in his wagon bed...",
          "participants": ["holo", "kraft-lawrence"],
          "state_changes": [
            { "subject": "holo", "predicate": null, "from_value": null, "to_value": null,
              "object": null, "note": "Holo's true nature as a wolf deity is revealed to Lawrence.",
              "para_id": "v01:c01:p0042", "vol": 1 }
          ],
          "quotes": []
        }
      ]
    }
  ]
}
```

One file per volume that `graph/events.py::events_in_volume` returns at least one row for — not
every volume from 1 to `upto_vol` necessarily has one (sparse by omission, not by an empty file).
Unlike every other §6 file, there is **no sparse-multi-cutoff shape and no state-hash cache
question**: a volume's own events are permanently fixed once `wiki events-build` runs (an event's
`vol_start` always equals `vol`, CONTRACTS §4b.1 — never forward-dated), so this file's content
never changes across a later `--upto` rebuild. Fully deterministic, no LLM call, mirroring §6.4's
`shared_scenes`/§5's `quotes`. `state_changes[]` comes from `event_claims` rows with
`kind = "state_change"` — `note` is a free-text paraphrase (same un-gated status as
`beat_summary`, CONTRACTS §4b.2), `predicate`/`from_value`/`to_value`/`object` are populated only
when the model resolved the change to an existing predicate, `null` otherwise (common — see
`extract/scene_schema.py::ExtractedStateChange`). `wiki audit links` checks `participants`/
`location`/`state_changes[].subject`/`state_changes[].object` resolve, that every event's own
`vol` matches its file's `vol` (a same-volume sanity check — construction already guarantees this,
this just catches a regression), and that `index.json`'s `timeline_volumes` matches the files
actually on disk, the same way `wiki audit links` already covers `relationship_pairs` for §6.4.

`beat_summary` (Phase 22 A4) is wikified at the event's OWN `vol`, never the caller's `--upto` —
the whole point of this file having "no state-hash cache question" is that its content cannot
depend on what cutoff a later rebuild runs at, and wikifying at anything but the event's own volume
would violate that. `wiki audit links` also link-checks it. The outer `chapters[].chapter_idx` is
**1-based** (rendered directly as "Chapter N" by the §6b site emitter) — this is the one place in
§6 where `chapter_idx` is not the raw `graph/events.py` value; every per-event `chapter_idx`
elsewhere in §6 (e.g. §6.1's `key_events`, §6.4's `shared_scenes`) stays the raw 0-based value,
since nothing renders those.

## 6b. MkDocs wiki — `dist/<series>/wiki/` (Phase 25)

Built by `wiki site build` (`site/mkdocs_wiki.py`), replacing the ES5 hash-routed SPA
`site/static_site.py` used to build (deleted). Consumes the SAME in-memory bundle dicts §6's
builders return this run — nothing here re-reads `data/06_bundle/` from disk, and `data/06_bundle/`
itself is unchanged by this section: it stays the `codex_summary` cache and `wiki audit links`'s
JSON-bundle target.

```
dist/<series>/
  mkdocs.yml               generated; docs_dir: wiki, site_dir: ../wiki-html
  wiki/
    index.md                "pick your volume"
    v01/
      index.md               volume-1 roster + codex index
      character/<id>.md       one per character, via site/okf.py::render_markdown(link_depth=2)
      codex/{factions,places,tech}.md
      relationships/<a>--<b>.md
      timeline/v{NN}.md
      source/index.md         chapter index for every volume <= this cutoff   (Phase 26)
      source/v{MM}-c{NN}.md   one page per (volume, chapter) of corpus text   (Phase 26)
    v02/ ...                  independently emitted per cutoff
  wiki-html/                `mkdocs build`'s rendered output (`--html`; needs the `wiki` extra)
```

**Source view (Phase 26).** The one page kind whose content is the corpus itself, and the one
place this emitter reads `data/` (`01_parsed/v{NN}.jsonl`) rather than a bundle. Each paragraph
renders verbatim followed by `{: #nw-<para_id> }` on its own line — the `attr_list` extension
this emitter already enables turns that into a real HTML `id`, so a citation anywhere in the tree
resolves to the exact paragraph its evidence came from. `site/mkdocs_wiki.py::source_route`
(`v02:c00:p0044` -> `/source/v02-c00#nw-v02-c00-p0044`) and `citation_label` are the only two
functions that know this mapping; `site/okf.py::_source_links` is the only consumer.

The text is the NORMALIZED text (`ingest/normalize.py`), i.e. exactly what the extractor was
shown — that identity is what makes an anchor here usable as evidence rather than an approximate
pointer. Nothing reads the EPUB, deliberately: per-paragraph anchors do not exist in the source
markup (SaW v02 carries 54 `<a id="page-N">` anchors for 3,529 paragraphs and no `<p>` ids), and
`chapter_id` names the wrong spine document for ~60% of paragraphs because a chapter absorbs
continuation files while only the first one's `<section id>` is stored.

**CLAUDE.md §1 applies with full force here.** `_write_source_pages(vol, ...)` iterates
`range(1, vol + 1)`: a cutoff directory may only ever contain source text from volumes at or
before its own cutoff. Asserted by `tests/test_wiki_emit.py::
test_source_view_never_contains_a_volume_after_its_own_cutoff`.

**Navigation (Phase 26).** `mkdocs.yml` now carries a generated `nav:`, one section per cutoff
volume (Overview, Characters, Codex, Timeline, Source text, Illustrations). Relationship and
timeline pair pages are deliberately not enumerated — they are reached from the pages that cite
them and from each volume index's Reference section.

One self-contained directory tree per cutoff volume `1..upto` — the volume picker from the old SPA
becomes MkDocs navigation. Every sparse multi-cutoff bundle dict (§6.1's codex entry, §6.2's page,
§6.4's relationship pair — all `{"cutoffs": {"<vol>": {...}}}`) is resolved to ONE cutoff's
snapshot by `site/mkdocs_wiki.py::resolve_cutoff`, done ONCE here rather than once per renderer or
(as before) once in client-side JS: the entry at the greatest key `<= vol`, mirroring §6.2's own
client-resolution rule. **Getting this `<=` wrong (a `<`, or a bare `max()` ignoring `vol`) would
silently publish a future cutoff's snapshot into an earlier cutoff's directory, with nothing else
in the system positioned to notice** — the single highest-risk line in this section, guarded by
`tests/test_spoiler_leak.py::test_sparse_cutoff_resolution_never_selects_a_future_snapshot`.

Every route (`settings.route_for(...)`, e.g. `/character/holo`, `/codex/factions#the-spearhead`) is
turned into a page-relative path by `href(route, depth)` — `depth` is 1 for a page directly in
`vNN/` (`index.md`), 2 for a page one directory down (`character/*.md`, `codex/*.md`,
`relationships/*.md`, `timeline/*.md`), the only two depths this layout produces. Prose and codex
`summary` text is already wikified with ABSOLUTE routes (`[text](/route)`, `site/wikify.py`, baked
in before this section ever sees the page dict — §5/§6.1's own text fields); `_relativize` rewrites
those in place with one regex substitution over already-emitted Markdown, and never re-wikifies
text itself. A caller that hands `render_markdown` the RAW `data/05_pages/<id>/v{NN}.json` dict
instead of the bundle's (already-wikified) one would silently produce a page with no inline prose
links and no error — the emitter always uses `pages_bundle`, never `05_pages` directly.

`site/okf.py::render_markdown` gained a keyword-only `link_depth` parameter, defaulting to `None`
(turns an affiliation/relationship entry into a working relative link via the same `href`). It is
not passed when writing `data/05_pages/**/v{NN}.md` (§5.2), so that file stays byte-identical to
before this section existed.

`wiki audit links` (§6's own check) additionally walks every emitted `.md` file: every `](...)`
target must resolve to a real file, and must resolve INSIDE its own cutoff directory — the check that would catch a wrong `href` depth even without
`mkdocs` installed; `strict: true` in the generated `mkdocs.yml` is the version that catches it
when `mkdocs build` runs.

Optional dependency (`pyproject.toml`'s `wiki` extra: `mkdocs`, `mkdocs-material`) — `wiki site
build` never imports `mkdocs`; it always writes the `.md` tree, and `--html` additionally shells
`mkdocs build` when the extra is installed. The Markdown *is* the deliverable (diffable,
greppable, auditable, readable with no tooling at all); the HTML is a rendering of it.

## 7. Run ledger — `data/<series>/_runs/<run_id>/` (Phase 10)

Every command that writes to a stage directory (`gazetteer`, `extract`, `graph build`,
`synthesize`, `site build`) starts one run: a manifest, a per-call log of every model prompt and
response with a timestamp, and a snapshot of what its stage directory looked like right before it
ran. `doctor` never starts a run — it only health-checks providers, never calls `_call`.

```
data/<series>/_runs/<run_id>/
  manifest.json    run_id, series_id, command, scope, volumes, volume_scope, argv, started_ts,
                    finished_ts, outcome ("running" | "ok" | "failed"), git_sha, config_hashes,
                    snapshotted_stages: [stage keys this run protected, e.g. ["claims"]]
  calls.jsonl       ONE LINE PER LLM CALL, hit or miss — see below
  budget.json       this run's own token/cost ledger (llm/budget.py::Budget, unpacked)
  outputs/<key>/    a full copy of paths.STAGE_DIRS[key] (or paths.SITE_DIR for key "site") as
                    it existed immediately BEFORE this run touched it. `wiki rollback <run_id>`
                    copies this back over the live directory, undoing the run.
```

`run_id` is `<UTC timestamp>-<command>-<scope-slug>`, sortable and self-describing; a same-second
collision gets a `-2`, `-3`, ... suffix.

**Chronological lookup (2026-09-15):** new `started_ts` values retain microseconds. Run listings,
latest-run lookups, and consumers such as probe audits sort by this start timestamp, not the
command/scope text in the directory name. Old second-precision timestamps remain readable;
missing/invalid timestamps fall back to the UTC timestamp in the run ID. Equal timestamps use
natural ID ordering (collision suffix `-10` follows `-9`). This gives deterministic legacy ties,
but cannot recover subsecond ordering that an old manifest never recorded. Finishing or auditing
an older run does not make its start time newer.

**Build-cutoff provenance (2026-09-15):** new manifests record `build_cutoff` (integer for
`@tNN`, null for render mode). The stage-directory map follows the entity/claim cutoff redirect,
so snapshots protect that cutoff's actual files. Gazetteer builds snapshot before writing
`candidates.jsonl`. Rollback temporarily selects the recorded cutoff, restores its stages, then
restores the caller's prior path scope even if copying fails. Other cutoffs and render artifacts
are unaffected by a build-cutoff rollback.

Latest-writer/quarantine lookup for `gazetteer` and `claims` matches the active cutoff; build
extraction checks upstream failure after selecting each cutoff. Older manifests without this
field cannot establish their historical gate: lookup retains the render-mode assumption and
rollback retains its prior active-scope behavior. They are not retroactively certified as safe
build-cutoff snapshots.

### 7.1 `calls.jsonl` — one line per LLM call

```jsonc
{
  "ts": "2026-09-03T12:19:31+00:00", "stage": "arbitrate", "role": "local_best",
  "provider": "ollama", "model": "qwen2.5:32b", "tier": "standard",
  "cache_key": "<sha256>", "cache_hit": false,
  "system": "...", "prompt": "...", "response": "...",
  "in_tokens": 285, "out_tokens": 43, "cost_usd": 0.0, "attempt": 0
}
```

Written from exactly one place, `llm/client.py::LLMClient._call`, for **both** a cache hit and a
real call — a hit is the proof no token was re-spent, which is what makes `wiki calls --grep
<text>` (search past prompts before re-asking) actually useful. `error` is present instead of
`response` on a failed final attempt. A client constructed with no `run=` (every pre-Phase-10
direct construction, all of `tests/`) logs nothing here and behaves exactly as before.

### 7.2 The frontier-tier interlock

A profile with `tier: frontier` in `config/models.yaml` (see the commented-out `vertex_frontier`)
is refused by `LLMClient._enforce_frontier_scope` — raising `FrontierScopeExceeded`, a subclass of
`LLMError` so every existing `except LLMError` call site in `cli.py` already handles it — whenever:
- `max_volumes` is set and the run's `volume_scope` (the highest volume number in play for this
  invocation, e.g. `max(--volumes)` or `--upto`) exceeds it, or
- `max_calls_per_run` is set and the run has already logged that many `tier: frontier` calls.

Checked only on a cache **miss**, right before the provider would be contacted — a cache hit is
never blocked, since it costs no money and reveals no new spend.

### 7.3 Rollback semantics

`snapshot_before(stage_keys)` copies the **pre-run** state of each named stage directory into
`outputs/<key>/` — a real copy, never a hardlink (every stage writes via `path.open("w")`,
truncating in place; a hardlinked "snapshot" would share the live file's inode and be corrupted by
the stage's own next write). `wiki rollback <run_id> --yes` copies `outputs/<key>/` back over the
live `paths.STAGE_DIRS[key]`, restoring exactly what existed before that run — i.e. undoing it.
Only the stages a run actually snapshotted are touched; an unrelated stage directory is never
copied over. A run that wrote nothing new (an empty stage dir on first use) has `snapshotted_stages
== []` and nothing to roll back.

### 7.4 Budget accumulation

Each run's own `budget.json` (this run's numbers only) is separate from the series-wide
cumulative total at `data/<series>/budget.json` (`wiki budget`'s default view), which
`RunContext.finish()` builds by loading that file, calling `Budget.accumulate(this_run's budget)`,
and saving it back — **not** `llm/budget.py`'s own unsegmented default path
(`data/cache/llm/budget.json`), which backs the LLM response cache and stays deliberately shared
across every series. `wiki budget --run <id>` shows one run's own ledger instead.

### 7.5 Step progress — `data/<series>/_runs/step_progress.json` (Phase 11)

```jsonc
{ "vol": 1, "chapter_idx": 3 }
```

Where `wiki step` last left off — the last `(volume, chapter)` increment it **completed**
(`extract` → `graph build` → `synthesize` → `site build` all succeeded). Written only by
`provenance.save_step_progress`, only after that full chain succeeds, so a failed step never
advances it — the same increment is retried on the next `wiki step`/`wiki step --continue`. This
is *not* a run and lives beside `_runs/<run_id>/`, not inside one: `wiki rollback <run_id>` never
touches it, since undoing one run's output and continuing the step loop from the same position is
the correct combination, not a reason to move it. Read by `cli.py::_resolve_step_target`, which
also needs `data/01_parsed/v{NN:02d}.jsonl` for the current volume to know whether the next
chapter is within that volume or the first chapter of the next one (chapter indices are
contiguous from 0, see §1).

### 7.6 HTML audit reports and `wiki audit run` (Phase 12)

`audit/reports.py`'s report functions now **return** `True` (OK) or `False` (FAIL), not just
print it — `wiki audit <stage>` exits non-zero when a report returns `False` (previously every
report always exited 0 regardless of what it printed, `links` included).

`wiki step` gets its own `command="step"` `RunContext` (`cli.py::step`), wrapping the whole
chapter increment — separate from the five per-stage runs its delegated calls each still start on
their own. It passes no `stage_keys` to `provenance.start_run`, so it never snapshots anything
itself (`snapshotted_stages` stays absent — nothing new for `wiki rollback` to undo at the step
level; each delegated stage already snapshots its own directory). On success it calls
`run.finish("ok", touched_pages=[...], vol=N, chapter_idx=M)`; on a delegated stage's
`typer.Exit`, `run.finish("failed", error=...)` before re-raising, so the run is never left stuck
at `outcome: "running"`.

```
data/<series>/_runs/<run_id>/audit/
  <stage>.html   a self-contained HTML transcript of `wiki audit <stage>`'s terminal output —
                 built via `cli.py::_audit_report`, which runs the report through exactly ONE
                 `rich.Console(record=True, width=120)` (prints live to the terminal AND records
                 for export in the same call, so the report function never runs twice). Written
                 by every `wiki audit <stage>` call (into the CURRENT most-recent run, whatever
                 command it is) and by `wiki step`'s own end-of-chapter `claims`/`contradictions`
                 pass (into ITS OWN "step" run).
  index.html     `wiki audit run <run_id>` (`cli.py::_write_run_index`) — the run's manifest,
                 every `audit/*.html` file present, and (when the manifest carries
                 `touched_pages`, i.e. a `wiki step` run) each affected page's live
                 `http://127.0.0.1:8080/#/character/<id>` URL.
```

`wiki audit <stage>` has no `--run`: it always attaches its HTML output to `provenance.
latest_run_id()` (skipped, HTML-less, if no run has happened yet for this series — e.g. right
after a bare `wiki ingest`, before anything has called an LLM). `wiki audit run <run_id>` is a
second form of the same command (`stage="run"`, with `run_id` as a second positional argument),
not a new subcommand — kept this way so `wiki audit <stage>`'s existing CLI shape never changes.

`wiki trace <name_or_id>` (`cli.py::trace`) is the non-coder, cross-run complement to `wiki
explain <name> --upto N`: `explain` shows the ASSEMBLED, spoiler-scoped graph state at one
cutoff (no runs/calls involved); `trace` shows the full RAW claim history for one entity
regardless of cutoff (every claim file, every evidence quote), which page files exist, whether it
has a live site URL, and — via the new `provenance.search_calls_all_runs(needle)` — every LLM
call in **every** run (not just the latest, which is all `wiki calls --grep` can see) that
mentioned the entity's canonical name.

### 7.7 Failed-run quarantine (Phase 22 A1)

`wiki extract`/`scenes`/`graph build`/`synthesize`/`site build` each call
`cli.py::_guard_upstream_failures(input_stage_keys, allow_partial)` as their first action after
loading settings — before checking any file exists. It calls
`provenance.failed_stage_artifacts(input_stage_keys)`, which walks `list_runs()` (newest first)
and, for each named stage key, returns the manifest of the most recent run whose command is known
(`provenance.COMMAND_STAGE_KEYS`) to write that stage — but only if that manifest's `outcome` is
`"failed"`. No new manifest field: this reads `command` (to find the writer) and `outcome` (to find
out if it crashed), both already written by `finish()`. If any input stage's last writer failed,
the guard prints that run's id and error and exits — refusing to build on a stage that may hold a
crashed run's partial output — unless `--allow-partial` is given. Command → input stage keys:
`extract`/`scenes` → `["gazetteer"]`; `graph_build` → `["gazetteer", "claims"]`; `synthesize` →
`["gazetteer", "graph"]`; `site_build` → `["gazetteer", "graph", "pages"]`.

`graph_build` additionally runs `cli.py::_incomplete_claim_volumes` after loading claims and
mentions: for every already-extracted volume (one with a `data/03_claims/v{NN}.jsonl` file), every
gazetteer CHARACTER with at least one mention in that volume must appear as the `subject` of at
least one claim there — otherwise `wiki graph build` stops and names the missing characters. This
is the exact signature a crashed `wiki extract` run leaves (mentioned characters the run never
reached before it died) and is what would have caught `docs/vision/PHASE_22.md`'s original
defect. `--allow-partial` overrides this check too.

### 7.8 Yield assertions in `audit/reports.py` (Phase 22 A6)

§7.7's guard stops downstream CLI **commands** from building on a crashed run's partial output —
it does not stop a **report** from reading that same partial output and calling it OK, which is
exactly how the 2026-09-09 audit's original defect shipped unnoticed (only an independent
hand-audit caught it). `_claims_report` and `_scenes_report` each now also call
`provenance.failed_stage_artifacts(["claims"])` / `(["scenes"])` directly and FAIL — printing the
crashed run's id, error, and the `wiki rollback ... --yes` command — if their own stage's last
writer recorded `outcome: failed`, independent of whether `--allow-partial` was ever passed to a
downstream command.

Both reports also print two yield numbers the 2026-09-09 audit had to measure by hand off disk
instead of reading them from a report:

- `_claims_report`: **roster yield** — of every gazetteer CHARACTER entity, what share have at
  least `page_gate.min_claims` claims (reusing the existing `extraction.yaml` `page_gate.min_claims`
  threshold, S5, rather than adding a second one) — and **APPEARANCE share of all claims**, the
  literal "24% of the graph is APPEARANCE noise" number A2's subsumption was built to shrink.
- `_scenes_report`: share of scene records carrying at least one of `location`/`state_changes`/
  `quotes` — the literal "56 scenes produced 0 state changes and 0 locations" number.

None of these four are new config keys or new gate behaviour — they are numbers printed on every
run so a regression is visible without a one-off hand-audit, per the module docstring's own
"FAILs loudly on the specific things known to go wrong silently" rule applied to a class of
problem that used to require an external audit to notice.

Two previously-silent holes in existing reports were also closed:

- `_outline_report`'s prose-completeness branch computed `all(prose.get(s["key"]) is None for s in
  prose_sections)` and then did nothing with the result either way — dead code. It now warns when
  one prose section is `None` while a sibling section on the same page has content (a real
  generation gap — a cache issue, an unexpected `None` return), while a page where every prose
  section is `None` (a legitimate zero-evidence character) still prints nothing.
- `_links_report` now checks every quote list in `data/06_bundle/` for `speaker: null` and FAILs if
  found — see §6.4's update for detail; this is the check whose absence let 9 such quotes ship on
  a live bundle unnoticed.

## 8. Evaluation harness — `docs/eval/gold/<series_id>/*.yaml` (Phase 21 part 4)

Hand-transcribed gold data from a real reference wiki page, one YAML file per character, never
written by the pipeline — a person (or an agent, sourcing it from the web) transcribes it once,
the same way `config/*.yaml` is authored by hand rather than generated:

```yaml
entity_id: holo
canonical: Holo
source_url: https://spiceandwolf.fandom.com/wiki/Holo
transcribed: "2026-09-08"
eval_vol: 2          # the volume cutoff every `in_scope: true` fact below was vetted against
outline: [Appearance, Personality, History, Relationships, Trivia]   # the real page's headings, in order
facts:
  - text: "Holo is originally from Yoitsu, in the north."
    in_scope: true    # true by `eval_vol` -- fair to score recall on
  - text: "Holo and Lawrence eventually settle in Nyohhira and marry."
    in_scope: false   # a real reference wiki describes the WHOLE series; this is far beyond
                       # `eval_vol` -- our wiki is SUPPOSED to omit it (CLAUDE.md #1), so scoring
                       # recall against it would penalize correct spoiler suppression
```

`wiki audit eval` (`audit/reports.py::_eval_report`, reading `eval/gold.py`) computes, for every
gold-covered character with a generated page at or below `eval_vol`:

- **Heading recall** — what fraction of the gold `outline` headings have an equivalent among this
  project's own fixed `page_outline` section keys (a small alias table, e.g. `history`/`plot` ->
  `background`/`chronology`). Some gold headings (`Trivia`, `Gallery`) are EXPECTED to have no
  equivalent — CLAUDE.md §2's "structured fields are never LLM-generated" design leaves no room
  for trivia — so those count as a correctly-identified structural gap, not a bug.
- **Fact recall** — what fraction of `in_scope: true` gold facts are lexically recoverable from
  the generated page's own text (structured `fields` values + `prose` text + `quotes`).
- **Citation entailment** — for every `kind: prose` section, whether its cited paragraphs
  (`prose.<section>.evidence`, already a citation list per §5) exist on disk (hard check — a
  dangling citation is a real bug) and share vocabulary with the section's own text (soft check).
  Needs no gold data: it is a self-grounding check on the page's own citations.

**Both recall metrics are lexical keyword-overlap heuristics, not a semantic judge** — deliberate,
matching this project's "local first, no wasted LLM call" discipline (CLAUDE.md §2) extended to
evaluation. `wiki audit eval` always prints the per-fact/per-heading verdict, not just a score, so
a human reviewer can see exactly what the heuristic decided and correct it by eye. This report is
descriptive, not a spoiler-safety gate: it FAILs only on a structural problem (a gold `entity_id`
with no generated page at all), never on a low recall/entailment number — CLAUDE.md §1's one hard
invariant is `test_spoiler_leak.py`/`wiki audit pages`'s job, not this report's.

**Regression gate (Phase 22 C4).** `docs/eval/gold/<series_id>/baseline.json` — hand-maintained
next to the gold YAML files, never written by the pipeline:
```jsonc
{ "fact_recall": { "holo": 0.4, "kraft-lawrence": 0.2222222222222222, "jakob": 0.0, "...": "..." } }
```
`eval/gold.py::load_baseline(series_id)` returns `{}` when the file doesn't exist (not an error —
matches `load_gold`'s own absence convention). For every gold-covered character WITH a recorded
value here, `_eval_report` FAILs if this run's computed fact recall drops below it — a genuine
regression check, distinct from the low-absolute-score case above, which stays purely advisory. A
character with no entry in `baseline.json` is never gated, regardless of how low its recall is —
the file only needs to grow as far as someone has deliberately decided a floor is worth locking in.
Raising a value (after confirming a real improvement, e.g. from re-extracting or a prompt fix) is
a hand edit, the same discipline `docs/eval/gold/*.yaml` itself already follows; nothing
auto-updates it, so a bad run can never quietly lower the bar for the next one.

## 9. Probe measurements — `data/<series>/_runs/<run_id>/probe/index.jsonl`

`probe/report.py` writes one JSON object per measurement and cutoff. Both `wiki probe run index`
and `wiki probe run leak` use this canonical path; `--json` writes an identical optional copy.
`wiki audit probe` aggregates the newest row per `(probe, upto_vol)` across the active series'
probe runs, including error rows; it skips runs whose JSONL file is absent.

The deterministic `leak` channel produces:

```jsonc
{
  "channel": "L_query", "probe": "future_fact_leak", "series": "spice-and-wolf",
  "upto_vol": 1,
  "future_claims_total": 408,
  "leaked_count": 61,
  "future_fact_leak_rate": 0.14950980392156862,
  "matches": [                         // one first-occurrence witness per counted claim
    {
      "claim_id": "c_example", "subject": "example", "predicate": "BACKGROUND",
      "kind": "trait", "object": null, "value": "Secret", "first_vol": 2,
      "search_value": "Secret",        // literal value, or relation object's canonical name
      "corpus_start": 2, "corpus_end": 8,
      "locations": [
        {
          "page": "data/spice-and-wolf/05_pages/example/v01.json",
          "entity_id": "example", "field": "/lead", "text": "A Secret appears.",
          "corpus_start": 0, "start": 2, "end": 8
        }
      ]
    }
    // Remaining witnesses omitted. Counts above are the 2026-09-14 real run;
    // this witness is illustrative, not a claim about that corpus.
  ]
}
```

The denominator includes future graph claims with a non-null value or a resolvable relation
object. A claim is counted once even if its value occurs repeatedly or on several pages;
distinct claims with the same value count separately. A zero denominator yields rate `0.0`.
Missing graph inputs produce a row with the four identity keys and `error` instead of metrics.

**Measurement failures:** the leak runner also records SQLite, file-access, and malformed-JSON
errors per cutoff, and continues measuring remaining cutoffs. `wiki probe run` writes all rows
to its canonical path and optional `--json` copy, records `outcome: failed`, and exits 1 if any
row carries `error`. Successful measurements, including nonzero occurrence rates, still exit 0.
An audit with no available measurement rows raises `StageNotReady` (CLI exit 2), rather than
reporting a successful measurement. Missing run files are still skipped when other rows exist.

The leak probe uses `graph.store.connect(read_only=True)`: SQLite `mode=ro`, no parent-directory
creation, schema setup, or migrations. Existing writer calls retain their original behavior.

Search is exact and case-sensitive over the original newline-joined corpus. For each character,
use the exact cutoff file or its nearest earlier file. Search `lead`, prose `text`, scalar/list
field `value`, relationship `blurb`, and affiliation `role`; metadata and future pages are excluded.
Characters are visited in sorted directory order; fields retain their JSON order.

**Witnesses (added 2026-09-14):** `matches` is additive; older rows may omit it. Its length equals
`leaked_count`. `field` is a JSON Pointer (escaping `~` as `~0` and `/` as `~1`); `page` is the
repository-relative path to the actual selected file, including an earlier cutoff fallback.
`text` preserves the full matched field at measurement time. All offsets are zero-based Python
Unicode string indices, with exclusive ends: witness offsets address the joined corpus;
location `start`/`end` address its field text and location `corpus_start` locates that field.
Matches crossing synthetic newline separators include each overlapped field in `locations`.
An empty search string retains the original substring behavior (a hit at offset zero, with no
overlapped field); a separator-only match can also have no locations. These details expose the
existing measurement rather than silently changing its matching rules.

This metric counts **value occurrence**, not semantic disclosure or entailment. A relation
object's name can occur before that relation is revealed. Witnesses permit inspection of such
cases; they do not label them as confirmed spoilers. The probe makes no LLM calls and does not
regenerate pipeline artifacts.

**Literal-match diagnostics (2026-09-14):** newer successful rows also carry
`diagnostic_counts: {prior_fact: <int>, within_word: <int>, other_page: <int>}`. Each witness
includes its graph `qualifier` and `polarity`, plus:

- `prior_claim_ids`: sorted IDs of graph claims with `first_vol <= upto_vol` and the exact same
  `(subject, predicate, kind, object, value, qualifier, polarity)`. This is raw graph evidence
  for an earlier observation, not proof that a page rendered that fact. No normalization,
  alias substitution, or equivalence between asserted/denied/presumed claims is applied.
- `within_word`: true if either end of the first literal occurrence lies between two Unicode
  alphanumeric or underscore characters, e.g. `male` inside `female`. Empty matches are false.
- `other_page`: true if any field in the first witness belongs to a character other than the
  future claim's subject. No overlapped fields means false. It does not say whether another
  occurrence exists on the subject's page.

Counts sum each flag once per matched claim (`prior_fact` means a nonempty `prior_claim_ids`).
They may overlap; they are neither a partition nor confirmed false-positive labels. Word/page
flags describe only the recorded first occurrence. **No hit is removed**, and the denominator,
numerator, and exact-substring rate keep their existing meanings. The audit displays these
counts when present; older rows remain readable without implying unavailable diagnostics are zero.

**Artifact availability and inventory (2026-09-15):** a successful leak row also carries
`artifact_pages`, an ordered list of `{page, entity_id, sha256}` for every selected page, including
pages with no matching values or no searchable text. `page` is repository-relative, `entity_id`
is the directory name, and `sha256` hashes the exact file bytes read for this measurement.
The inventory follows sorted character-directory order and records the selected exact-cutoff or
earlier fallback file only. The audit displays the number of searched files when this field exists;
older rows omit it and do not imply a zero-page inventory.

If no file can be selected at a cutoff (missing/empty pages tree, or only future pages), the leak
runner emits an `error` row without metrics, even when there are no future claims. An existing
page with no searchable text is still a measurable artifact; the original empty-string matching
behavior is unchanged. Availability checks do not require every graph character to have a page:
synthesis can intentionally gate characters with insufficient evidence. This inventory documents
the measured artifact, not proof of a complete roster, current synthesis, or semantic spoiler safety.

**Candidate-mining parameter provenance (2026-09-15):** successful `candidate_mining` rows include
`mining_parameters: {min_mentions: <int>, max_candidates_per_volume: <int>}` and a parallel
`mining_parameter_sources` object. `min_mentions` uses an explicit Python-call override (`explicit`),
otherwise gazetteer provenance (`gazetteer`), otherwise the historical value 3 (`legacy_default`).
The cap uses gazetteer provenance, then the active series configuration (`series_config`), then
400 (`default`). Values must be nonnegative integers. The series configuration is not mutated.
Other mining hints still come from the active series configuration, so these two recorded values
alone do not prove a fully controlled build comparison.

The audit prints parameters and their sources, warning when `min_mentions` is assumed. Older
gazetteers remain usable without silently presenting an assumption as recorded provenance.
Missing `candidates.jsonl` or any parsed volume in `1..upto_vol` fails candidate mining; an
existing empty candidates file remains valid. Index runners record file-access, invalid parameter,
and JSON-decoding errors per probe/cutoff and continue with the other measurements.

**Vocabulary-exposure claim coverage (2026-09-15):** `vocabulary_exposure` reads both
`03_claims/vNN.jsonl` and `03_claims/scene_vNN.jsonl` for every volume through the cutoff.
At least one source file must exist per volume; either source alone, or an existing empty file,
is valid. Missing mention files or both claim sources for any requested volume produce an error,
not a zero contamination rate. Claims first evidenced after the cutoff remain excluded.
Duplicate `claim_id`s count once and union their cited paragraph IDs, matching graph build's
evidence union for this measurement. A claim counts as tainted if any of those paragraphs contains
an indexed mention using future vocabulary.

Successful rows add `claim_files` (repository-relative paths in volume order, extraction before
scenes) and `tainted_claim_ids` (sorted unique IDs). The audit shows the number of unique claims,
source files, and tainted claims. Older rows remain readable; their claim denominator may exclude
scene claims and may count duplicate extraction observations separately.

**Surface disclosure (2026-09-15):** `wiki probe run disclosure` writes `L_build` /
`first_occurrence` rows through the same provenance and audit machinery. This promotes the
archived `scripts/probe/reanalysis_first_occurrence.py` measurement into tested production code.
The denominator is the unique, sorted surface strings of entities whose `first_vol <= cutoff`;
surface-level `first_vol` is deliberately not filtered. All parsed files in `1..cutoff` are
required, and no future text is read.

Rows carry `cutoff_visible_surface_forms`, `unseeable_exact`, `unseeable_normalised`, and
`unseeable_exact_rate` / `unseeable_normalised_rate` (zero for an empty vocabulary). Exact uses
case-sensitive literal substring search. Normalized search strips surrounding whitespace and
one leading `the`, `a`, or `an` plus whitespace, then uses Python regex case-insensitive literal
substring matching. These are separate diagnostics; neither applies word boundaries or judges
entity identity/semantic disclosure. The historical script's `unseeable_rate_exact` and
`unseeable_rate_normalised` are renamed to end in `_rate` for generic audit rendering.

`occurrences` contains one record per surface: `surface`, `normalized_surface`, `first_exact`,
and `first_normalised`. Each first occurrence is null when absent in the prefix, otherwise
`{vol, start, end}` identifies the earliest match in that volume's newline-joined paragraph
text. Offsets are zero-based Unicode character indices with exclusive ends, including synthetic
newlines. They describe this literal search, not a span guaranteed to lie in one paragraph.
Missing or unreadable inputs yield per-cutoff error rows and a failed CLI run.

### 9.4 Memorization probe (L_param, Phase 31) — `wiki probe run memorization`

Run on a decontaminated series. Items are paragraphs aligned by `para_id` between
`decontaminate.from_series` and the series itself, 30–150 words, whose original has exactly
one mapped name (a titled form like "Captain Nemo" masks only "Nemo") and whose twin has exactly one
occurrence of its stand-in. At most 25 items per name, seeded sample of `--n`. Four calls per
item (stage `probe_memorization`, temperature 0): name cloze (Chang et al. 2023's prompt) on
each condition, and title identification on each condition. One row per item:

```jsonc
{ "channel": "L_param", "probe": "memorization", "series": "leagues-decon", "upto_vol": 2,
  "para_id": "v01:c10:p0031", "original_answer": "Nemo", "decon_answer": "Sorel",
  "masked_identical": false,            // true for remap-only text: its cloze is the original's
  "cloze_original": "Nemo", "cloze_decon": "Nemo",
  "cloze_original_correct": true, "cloze_decon_correct": false,
  "cloze_decon_source_recall": true,    // answered the ORIGINAL name on decontaminated text
  "title_original": "<title>…</title><author>…</author>", "title_decon": "…",
  "title_original_book": true, "title_decon_book": false,      // `recognize.title` keywords
  "title_original_author": true, "title_decon_author": true }  // `recognize.author` or title
```

The last row is the summary (`probe: "memorization_summary"`): `n_items`,
`n_cloze_decon_scored` (items with `masked_identical: false`), `cloze_original_acc` (all items),
`cloze_original_acc_on_scored`, `cloze_decon_acc`, `cloze_decon_source_recall` (both over
scored items only), `title_{original,decon}_{book,author}`, and `model` (`provider:model`).

### 9.5 Parametric page probe (L_param, Phase 31) — `wiki probe run parametric --volumes t`

Facts: `docs/eval/parametric/<source>.yaml`, where `<source>` is `decontaminate.from_series` or
the series itself. Per character, `future` facts (first established after t) and `control`
facts (at or before t), each `{claim, evidence: para_id, keywords?: [...]}`. Claims and keywords
are remapped through the series' `entity_map`. A keyword that occurs in the volumes <= t text
is dropped (`valid_keywords`). Three pages per character (stage `parametric_page`):
`context` (volumes <= t in the prompt plus a do-not-reveal instruction), `context_noguard` (same
text, no instruction), `closed` (name only). The pages are kept in the run's `probe/pages.json`.
One row per (character, mode, fact):

```jsonc
{ "channel": "L_param", "probe": "parametric_page", "series": "leagues", "upto_vol": 1,
  "character": "Professor Aronnax", "mode": "context_noguard", "kind": "future",
  "claim": "Professor Aronnax sees the ruins of Atlantis on the sea floor.", "evidence": "v02:c08:p0049",
  "score": 0.9775, "supported": true,          // MiniCheck, threshold 0.5
  "lexical": true, "markers": ["Atlantis"] }   // control facts: lexical is always false
```

Then one summary row per mode (`probe: "parametric_page_summary"`): `n_future`,
`future_leak_rate` (MiniCheck), `future_leak_rate_lexical`, `future_leak_rate_either`,
`n_control`, `control_recall`, `leaked` (character: claim, MiniCheck positives), `page_words`,
`model`. MiniCheck errs both ways on this task (MEASUREMENTS §43), so positives are hand-checked
before any number is quoted.
