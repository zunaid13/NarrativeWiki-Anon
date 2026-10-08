# USER_GUIDE.md — every command, and the order to run them in

All commands are subcommands of `wiki`. Run `wiki --help` or `wiki <command> --help` for flags.

Two things to know before you start:

- **Stages are resumable.** Every stage writes its output to `data/NN_<stage>/` and skips work that
  is already done. Interrupting `wiki extract` after nine volumes and rerunning it costs you nothing
  for those nine. Use `--force` to redo work deliberately.
- **`--upto N` is the spoiler boundary, `--volumes` is the work list.** `--volumes 1-13` says which
  books to process; `--upto 3` says which cutoff to render. Processing 13 volumes and rendering the
  site at cutoff 3 is normal and correct.

---

## Where things live — the five questions everyone asks first

| Question | Answer |
|---|---|
| Where do I put the books? | Drop the EPUB files into `corpus/<series-id>/` (e.g. `corpus/spice-and-wolf/Spice and Wolf v01 [...].epub`). No renaming — `config/series.<id>.yaml`'s `source.dir`/`source.glob` finds them and reads the volume number out of the filename itself. |
| Where do I put an API key? | Copy `.env.example` to `.env` (repo root) and fill in whichever key(s) you have — every one is optional. `.env` is git-ignored, so it never gets committed. See §0 below for which key does what. |
| Where does each stage's output go, and how do I audit it? | Every stage writes to `data/0N_<stage>/` and has a `wiki audit <stage>` command — see the table in §2 for the exact path and audit command per stage. `wiki status` alone tells you which stages are done and which is next. |
| How do I look at the finished website? | `wiki serve` (after `wiki site build`), then open **http://localhost:8080** in a browser. The volume slider top-right is the spoiler dial. The same `dist/site/` folder is plain static files, so you can also open `dist/site/index.html` directly or copy it anywhere that serves static files. |
| I don't want to wait hours for extraction — what's the fastest path to *something* on screen? | The smoke test in §4: one volume, `--limit 10` characters, all the way to `wiki serve`. Minutes, not hours. |

---

## 0. One-time setup

```bash
pip install -e .                              # installs the `wiki` command
cp .env.example .env                          # then edit .env if you have API keys
ollama pull qwen2.5:14b-instruct-q4_K_M       # ~9 GB; the local workhorse
wiki doctor
```

**Vertex AI is the default as of 2026-09-23** and is the one credential that is not an API key:

```bash
gcloud auth application-default login         # ADC; no key to paste anywhere
# then in .env:
#   GOOGLE_CLOUD_PROJECT=<your project id>
#   GOOGLE_CLOUD_LOCATION=global              # REQUIRED -- gemini-3.x 404s on regional endpoints
```

With that set, every generative stage runs on `gemini-3.1-flash-lite` and a full two-volume build
costs about $1.65 (`wiki budget` shows the running total against the $20 ceiling in
`config/models.yaml`). Without it, the same stages fall back to the OpenRouter free tier, and
without any credential at all, to local Ollama — see `models.yaml`'s `routing:` block, where each
stage lists that chain explicitly.

`.env` is optional. With no API key at all the pipeline runs entirely on the local model; the only
difference is that the final prose is less polished. Every key slot in `.env.example`
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`,
`GROQ_API_KEY`) is independent — set only the ones you actually have. **`GROQ_API_KEY`
(console.groq.com/keys) is the easiest to get started with**: free, fast, and generous rate limits
for the prose-sized requests this pipeline makes. Which key is actually used for which stage is
decided entirely by `config/models.yaml`'s `routing:` section, never by code — open that file to
see or change the mapping.

**If `wiki` is not found after installing**, pip put the script somewhere that is not on your PATH
(on this machine, `~/AppData\Roaming\Python\Python314\Scripts`). Either add that
directory to PATH, or use the equivalent form everywhere in this guide:

```bash
python -m narrativewiki doctor      # identical to: wiki doctor
```

---

## 1. The short version

```bash
wiki ingest --volumes 1 && wiki gazetteer --volumes 1   # once, to seed the gazetteer -- audit it
wiki step                                                # then: one chapter, then it stops
wiki step --continue                                     # ...repeat, reading the audit each time
wiki serve
```

**`wiki step` is the default path**, and does one chapter at a time: ingest → scenes →
gazetteer --merge-epithets → extract → graph build → synthesize → site build, then it prints
the affected character page(s) and the terminal audit, and stops. Advancing again always needs
`--continue` (next chapter) or `--redo` (repeat the last one) — a bare `wiki step` after the
first never silently continues, so you always see what changed before deciding to go further.
See "`wiki step`" in §2.

For an unattended full run with no per-chapter pause (batch/CI, or once you already trust the
gazetteer and extraction quality), `run-all` still chains every stage below in order, stopping at
the first failure — but a multi-volume scope now requires saying so explicitly:

```bash
wiki run-all --volumes 1-13 --upto 13 --i-accept-unaudited
```

Without `--i-accept-unaudited`, `run-all` refuses more than one volume and points you at
`wiki step` instead. **The first time through a new series, prefer `wiki step` (or the long,
stage-by-stage version below)** — you want to audit the gazetteer before spending hours of GPU
time on extraction built from it, and `wiki step`'s per-chapter pause is exactly that habit.

---

## 2. The full pipeline, in order

Each stage depends on the one above it. Run them in this sequence — or let `wiki step` (below)
run `extract → graph build → synthesize → site build` for you, one chapter at a time.

### `wiki doctor`

Checks Python version, installed packages, the Ollama server and model, API keys, the `corpus/` EPUBs
and disk space. Prints a readiness table and exits non-zero if a required item is missing.

Run this first, and again any time something behaves oddly. It exists so you find out about a missing
model in five seconds rather than three hours into an extraction run.

### `wiki ingest --volumes 1-13`

EPUB → paragraph records. Deterministic, no model involved, takes under a minute for all 13 volumes.

**Output:** `data/01_parsed/v{NN}.jsonl` + `manifest.json`
**Then run:** `wiki audit ingest`

The audit prints per-volume chapter counts, word counts and speech-channel breakdown. Check that word
counts look sane (~936k total for 86) and that **no volume reports zero Para-RAID paragraphs** — that
means the publisher changed a CSS class and the parser needs a new pattern in `config/series.86.yaml`.

### `wiki gazetteer --volumes 1-13`

Discovers who and what exists: mines name candidates from prose, types them with the local model,
clusters aliases onto canonical entities, then builds the Aho-Corasick automaton and indexes every
mention in the corpus.

**Output:** `data/02_entities/gazetteer.json`, `mentions.jsonl`, `candidates.jsonl`, `surface_forms.jsonl`
**Then run:** `wiki audit gazetteer` — writes a reviewable HTML roster to `data/02_entities/roster.html`
and refreshes the exact surface vocabulary inventory. The inventory records first volume,
origin, ambiguity, and actual index ownership, including collision losers. It is also produced
under each `@tNN/02_entities/` build cutoff. Old surfaces without recorded origins export
`source: "unknown"`; exporting the inventory requires no LLM calls.

**Audit this one by hand.** Everything downstream inherits its mistakes: a character split into two
entities gets two half-empty pages, and a bad alias merge silently mixes two people's facts together.
Open the roster, sort by mention count, and check the top ~50. There are no per-series overrides
(Phase 32): record the right answer in `docs/eval/roster/<series>.yaml`, measure it with
`python scripts/eval/roster_gold.py <series>`, and fix the general rule that got it wrong.

Useful flags: `--min-mentions N` (default 3) to control how aggressively rare candidates are kept.

### `wiki scenes --volumes 1-13` — chapter-major second pass (Phase 18; Phase 22 C1)

Reads every chapter end-to-end in overlapping ~4k-token spans, extracting scene records
(participants, location, beat summary, state changes, quoted dialogue), mining epithet aliases
("the wisewolf", "the merchant"), AND — as of Phase 22 C1 — the PRIMARY attribute/trait/relation
claims for every participant. Writes to `data/02b_scenes/` (scene + epithet records) and
`data/03_claims/scene_v{NN}.jsonl` (claims); never touches `wiki extract`'s own
`data/03_claims/v{NN}.jsonl` or `graph.db` directly. `wiki graph build` reads both claim files.
Coverage is 100% by construction: the union of every chapter's span records covers every
paragraph in that chapter, which is what lets this pass reach every character regardless of
mention density — the gap `wiki extract`'s mention-window pass structurally cannot close alone.

**Output:** `data/02b_scenes/v{NN}.jsonl` (scene records), `data/02b_scenes/epithets_v{NN}.jsonl`,
and `data/03_claims/scene_v{NN}.jsonl` (claims)
**Then run:** `wiki audit scenes` — verifies paragraph coverage and verbatim-quote discipline,
and prints the mined epithet table for review before the merge.

Chapter-scoped: `--chapters '1-4'` (requires `--volumes` to select exactly one volume) re-runs
only those chapters' spans, merging the result with whatever is already on disk — same
accumulate-not-overwrite behavior as `wiki ingest --chapters` and `wiki extract --chapters`.

### `wiki gazetteer --merge-epithets`

After reviewing `wiki audit scenes`'s epithet table, run this to fold high-confidence epithet
mentions (above `extraction.scene.min_epithet_confidence` in `config/extraction.yaml`, default 0.6)
into `gazetteer.json`'s `surface_forms` and rebuild the Aho-Corasick automaton and mention
index. Local, free — **no LLM call**. Run before `wiki extract` so the automaton picks up the
new aliases: a character previously only mentioned by epithet in a chapter will then generate
mention windows, and `wiki extract` will emit claims for them.

`wiki step` calls `wiki scenes --chapters` and `wiki gazetteer --merge-epithets` for you (in
the order: `ingest → scenes → gazetteer --merge-epithets → extract → graph build → synthesize →
site build`). `wiki run-all` does the same for a whole-volume scope.

### `wiki step` — the default path, once the gazetteer is audited

Runs `extract → graph build → synthesize → site build` for exactly **one chapter**, prints the
real, working local URL of every character page that changed
(`http://127.0.0.1:8080/#/character/<id>` — start `wiki serve` separately if it isn't already
running), runs the `claims`/`contradictions` audit reports (saved as HTML too — see `wiki audit
run <id>` in §3), then stops.

```bash
wiki step                # the very first chapter
wiki step --continue     # the next chapter (or the first chapter of the next volume, once
                          # the current one is finished)
wiki step --redo         # repeat the last completed chapter (e.g. after fixing a prompt/config)
```

A bare `wiki step` after the first is **refused** ("pass --continue to advance or --redo to
repeat it") rather than silently continuing — advancing always needs a deliberate flag, so you
always see the audit for what just changed before doing more. Progress is tracked in
`data/<series>/_runs/step_progress.json`; `wiki rollback <run_id> --yes` undoes one run's output
without moving this position, so rolling back a bad chapter and then `wiki step --redo`-ing it is
the normal recovery path.

### `wiki ingest --volumes N --chapters '1-4'` / `wiki extract --volumes N --chapters '1-4'`

`wiki step` calls these for you; call them directly for more control. `--chapters` (`'3'`, `'1-4'`,
`'2,5'`) narrows either stage to specific chapters **within one volume** — `--volumes` must resolve
to exactly one volume when `--chapters` is given. Both stages merge chapter-scoped output with
whatever is already on disk rather than overwriting the whole volume file, so `wiki extract
--volumes 3 --chapters 5` only touches chapter 5's claims; every other chapter's claims in
`data/03_claims/v03.jsonl` are left exactly as they were. `--force` redoes only the requested
chapters, not the whole volume.

### `wiki extract --volumes 1-13`

The long one. For each character in each volume, gathers the passages that actually name them and
extracts typed claims with evidence. Runs on the local model; expect **several hours** for 13 volumes
on a 5070 Ti. Fully resumable and safe to interrupt. As of Phase 22 C1, `wiki scenes` is the
PRIMARY facts source (see above) — a full `wiki extract` sweep over every volume is no longer the
expected way to run this stage; prefer the targeted top-up below. (Phase 22 C2 is only the top-up
path itself — `wiki extract` still defaults to a full mention-window sweep when `--entities` is
omitted; there is no automatic "sweep only where scenes came up short" mode yet.)

**Output:** `data/03_claims/v{NN}.jsonl`
**Then run:** `wiki audit claims`

The audit verifies that every cited quote really appears in the paragraph it cites, and reports
claims per character, predicate distribution and confidence spread. The `wiki extract` run itself
prints drop-reason counts per volume (unresolved relation objects, non-verbatim quotes, unknown
predicates, low confidence) since those never make it into the written claims file for the audit
to see.

Useful flags: `--entities "Shinei Nouzen,Vladilena Milizé"` to extract for specific characters while
tuning prompts; `--limit N` to cap characters per volume for a quick smoke test; `--attr`/`--trait`/
`--relation`/`--only`/`--skip` to narrow or widen what gets extracted without editing config — see §8.

**Targeted top-up (Phase 22 C2).** `--entities` doubles as a top-up selector: if a volume's claim
file already exists, `--entities` no longer skips it the way a bare re-run would — instead it
re-extracts only the named character(s) and merges the result in (`cli.py::_merge_by_entity`),
leaving every other character's claims on disk untouched. `--force` is still whole-volume and still
requires either an empty `--entities` filter or explicit intent to redo everyone; you do not need
`--force` alongside `--entities` to top up. `wiki audit eval` prints the exact top-up command for
any gold-covered character whose fact recall falls below 50% (`audit/reports.py::_eval_report`,
`_TOP_UP_RECALL_THRESHOLD`) — run that audit after a `wiki synthesize`, then paste its suggested
`wiki extract --volumes N --entities "..."` line rather than guessing which character/volume to
redo:

```
wiki extract --volumes 2 --entities "Holo" && wiki graph build && wiki synthesize --upto 2
```

**Gold data and the regression gate (Phase 22 C4).** `docs/eval/gold/<series>/*.yaml` — one
hand-transcribed file per character, ten for spice-and-wolf as of this phase — is never written
by the pipeline; add a new character by transcribing a real reference wiki page's outline and
fact list into a new file there (copy an existing one for the exact shape). `docs/eval/gold/
<series>/baseline.json` is a second, also hand-maintained file recording each gold character's
last-known-good fact recall; `wiki audit eval` FAILs if a character WITH a recorded baseline
drops below it (a real regression), while a character with no entry there stays purely advisory
no matter how low its score is. If a drop is genuinely expected (a deliberate trade-off), update
`baseline.json` by hand rather than re-running until it passes — nothing auto-updates it.

### `wiki infobox --volumes 1-2` — infobox fields the windowed pass misses (Phase 30)

One long-context call per character per volume, over every paragraph in that volume that names
the character, asking only for `extraction.infobox.attributes` (GENDER, AGE, ORIGIN), each with a
verbatim quote. The same gates as `wiki extract` apply. Writes `data/03_claims/infobox_v{NN}.jsonl`,
which `wiki graph build` reads alongside the other claim files; never touches their output.
`--entities "Holo"` re-runs one character and replaces only their rows; `--force` redoes a volume.
About $0.03 per volume on v1–2 (MEASUREMENTS §34). `--pass backstory` runs the second pass: BACKGROUND facts, phrased in the past tense, into `backstory_v{NN}.jsonl` (about $0.08 per volume, §37). **Then run:** `wiki graph build`.

### Replaying a stage for $0: `NARRATIVEWIKI_CACHE_ONLY=1`

With this variable set, any LLM call that misses the cache raises instead of being sent. Use it to
re-run a stage after a parser or gate change (`NARRATIVEWIKI_CACHE_ONLY=1 wiki extract --volumes
1-2 --force`): it either completes from stored responses for $0 or stops before writing that
volume, never silently re-rolling facts with fresh calls.

### `wiki graph build`

Assembles claims into the temporal knowledge graph: groups them, assigns validity intervals, and
resolves conflicts. Same-volume conflicts are extraction errors and get arbitrated; cross-volume
conflicts are treated as the character changing and supersede the earlier interval.

Two things happen automatically here, no flags needed (Phase 17): near-duplicate phrasings of the
same multi-valued attribute or trait ("outwardly detached" / "detached on the outside") are
clustered via local bge-m3 embeddings and collapsed to one canonical value before rendering, using
the same local, free `embed` role `wiki doctor` already health-checks; and relation predicates
declared mutually exclusive in `config/extraction.yaml` (`relations.*.conflicts_with` — only
`FRIEND_OF`/`ENEMY_OF` out of the box) are arbitrated the same way a same-volume attribute
contradiction is, and supersede across volumes if the relationship genuinely changes.

**Output:** `data/04_graph/graph.db`, `contradictions.json` (now also carrying a
`canonicalization` section)
**Then run:** `wiki audit contradictions`

Read the `flagged` section. Those are conflicts the rules and the arbiter would not settle, and they
are usually either a genuine ambiguity in the text or a sign that two people got merged in the
gazetteer.

### `wiki verify --upto N`

A verification pass, not another extraction pass (Phase 22 C3): one cheap LLM call per character,
checking whether each fact currently visible on the graph is actually supported by its own quoted
evidence. This is the backstop for the two failure patterns extraction-level fixes can reduce but
not guarantee away — a fact hallucinated about the wrong entity, and a relationship/membership
claim the evidence doesn't actually support (e.g. being captured by a faction read as joining it).
It never edits a claim or interval; a flagged fact stays on the graph and on the page exactly as
before, it is only logged for review.

**Output:** `data/04_graph/verification.json`
**Then run:** `wiki audit verify`

A character with no visible facts at `--upto` costs no call. Useful flags: `--entities` to check
only a few characters, same shape as `wiki synthesize --entities`.

### `wiki explain "<character>" --upto N`

Not a pipeline stage — an inspection tool, and the best one in the toolkit. Prints the exact evidence
set a page would be generated from at cutoff N: the visible intervals, the multi-hop passages
Personalized PageRank pulled in, and the token count that would be sent to the model.

Run it before `synthesize` on a couple of characters. If the evidence looks wrong, the page will be
wrong, and finding that out here costs nothing.

### `wiki synthesize --upto 13`

Builds a page model per character per cutoff, renders structured fields directly from graph rows, and
generates the short background and personality prose. Caches on the visible claim set, so a character
whose facts did not change between volumes is not regenerated.

**Output:** `data/05_pages/{entity_id}/v{NN}.json` and `.md`
**Then run:** `wiki audit pages`

Useful flags: `--polish` routes major characters' prose to the API model configured in
`config/models.yaml` (costs tokens, noticeably better prose); `--entities` to regenerate a few pages.

### `wiki site build --upto 13`

Hyperlinks every entity mention in the generated text, assembles the JSON bundle (`data/
06_bundle/`, CONTRACTS §6), generates one-sentence codex definitions for factions/locations/
battles/technology (`codex_summary` — the same evidence-gated, "no evidence, no LLM call, no
hallucination" rule Phase 6's prose follows), and builds the static site.

**Output:** `data/06_bundle/`, then `dist/site/`
**Then run:** `wiki audit links` — verifies every generated link resolves to a real page or codex anchor

Useful flags: `--force` regenerates a codex entry's summary even where nothing about it has
changed since the previous cutoff; `--entities` limits which non-character (codex) entities get
(re)processed, for a cheap smoke test — mirrors `wiki synthesize --entities`.

The site itself is a small dependency-free HTML/CSS/JS viewer (`src/narrativewiki/site/
templates/`) — no Node/npm needed. `CLAUDE.md`'s file map still describes `web/` as the eventual
Vite+React+TS SPA; it is not built yet (docs/handover/PHASE_7.md), and building it needs no
change to `data/06_bundle/`'s contract.

### `wiki serve`

Serves `dist/site/` on `http://localhost:8080`. Add `--port` to change it. The built site is plain
static files: you can also open it directly or host it anywhere.

---

## 3. Checking on things

| Command | What it tells you |
|---|---|
| `wiki status` | Which stages are complete, how fresh each output is, and what to run next |
| `wiki audit <stage>` | The per-stage report, printed AND saved as a self-contained HTML file into the most recent run's `_runs/<run_id>/audit/<stage>.html`. Stages: `ingest`, `gazetteer`, `scenes`, `claims`, `contradictions`, `verify`, `events`, `outline`, `pages`, `eval`, `links`, `relationships`. A real FAIL now exits non-zero (e.g. in a script/CI check) |
| `wiki audit run <run_id>` | An HTML index for one run — its manifest, every `audit/*.html` report saved into it, and (for a `wiki step` run) the character page(s) it touched with their live URLs |
| `wiki explain "<name>" --upto N` | The ASSEMBLED, spoiler-scoped evidence a page would be built from at one cutoff — run before synthesizing |
| `wiki trace "<name>"` | The full RAW provenance for one character, cutting across every volume and every run: every claim ever extracted about them (with its evidence quote), every page file, the live site URL, and every LLM call in any run that mentioned them |
| `wiki budget` | Tokens and estimated cost per stage, cumulative for this series. `--run <id>` shows one run only |
| `wiki runs` | Every past run for this series, newest first: command, scope, outcome, what it snapshotted |
| `wiki calls [--run <id>] [--stage s] [--grep text]` | The exact prompt and response for every model call in a run, with a timestamp. Defaults to the most recent run. Search here before re-asking the model something it may already have answered — a repeated question shows up as a cache hit at zero cost |
| `wiki rollback <run_id> --yes` | Restores stage output to what it was immediately *before* that run, undoing it |

Every command that writes to a stage directory (`gazetteer`/`extract`/`graph build`/`synthesize`/
`site build`) starts a run automatically — nothing to opt into. `wiki doctor` never does, since it
only health-checks providers and never calls a model for real. `wiki step` additionally starts its
own `command="step"` run wrapping the whole chapter increment, so `wiki audit run <that id>` has
one place to point at for the chapter's `claims`/`contradictions` HTML reports and its affected
pages. See `docs/CONTRACTS.md` §7 for the exact on-disk shape (`data/<series>/_runs/<run_id>/`)
if you need to read it directly.

---

## 4. Getting to a deliverable

### The whole series

```bash
wiki doctor
wiki ingest     --volumes 1-13   &&  wiki audit ingest
wiki gazetteer  --volumes 1-13   &&  wiki audit gazetteer     # review roster.html here
wiki scenes     --volumes 1-13   &&  wiki audit scenes        # review epithet table before merge
wiki gazetteer  --volumes 1-13 --merge-epithets               # local, free; no LLM call
wiki extract    --volumes 1-13   &&  wiki audit claims
wiki graph build                 &&  wiki audit contradictions
wiki synthesize --upto 13 --polish
wiki site build                  &&  wiki audit links
wiki serve
```

### Only the first X volumes

Process only what you have read. Nothing later is ever ingested, so nothing later can leak.

```bash
wiki ingest --volumes 1-5 && wiki gazetteer --volumes 1-5 && wiki extract --volumes 1-5
wiki graph build && wiki synthesize --upto 5 && wiki site build && wiki serve
```

### A fast smoke test before committing to a long run

```bash
wiki ingest --volumes 1 && wiki gazetteer --volumes 1
wiki extract --volumes 1 --limit 10
wiki graph build && wiki synthesize --upto 1 && wiki site build && wiki serve
```

### Adding a volume later

Incremental by design — earlier volumes are not reprocessed.

```bash
wiki ingest --volumes 14 && wiki gazetteer --volumes 1-14   # gazetteer re-runs over all volumes
wiki extract --volumes 14 && wiki graph build
wiki synthesize --upto 14 && wiki site build
```

The gazetteer step covers the full range because a new volume can introduce an alias for an existing
character; extraction and synthesis only do the new work.

---

## 5. When something goes wrong

| Symptom | Cause and fix |
|---|---|
| `wiki doctor` says Ollama unreachable | `ollama serve` not running, or `OLLAMA_HOST` wrong in `.env` |
| Audit reports 0 Para-RAID paragraphs in a volume | Publisher changed a CSS class. Add it to `para_raid_classes` in `config/series.86.yaml` and re-ingest |
| Two people's facts on one page | Bad alias merge. Add the pair to `different_entities` in `docs/eval/roster/<series>.yaml`, then fix the clustering rule in `entities/alias.py` |
| A character has almost no claims | Their aliases are missing from the gazetteer, so mentions were never indexed. Check `roster.html` mention count; record the missing alias in the roster gold and fix the general rule |
| Extraction is very slow | Expected — it is the bulk stage. Check `nvidia-smi` shows GPU use; if not, the model fell back to CPU |
| A page shows something from a later volume | A real bug in the invariant. Run `pytest tests/test_spoiler_leak.py` and report which claim leaked |
| Out of VRAM | Use a smaller quant (`q4_0`) or lower `num_ctx` in `config/models.yaml` |

Deleting `data/cache/llm/` is always safe. It only costs you a rerun.

---

## 6. Using it on a different series

1. Put the EPUBs in `corpus/<series-id>/` and point `source.dir` at it (omit `source.dir` and
   it defaults to exactly that); `config/series.spice-and-wolf.yaml` uses
   `corpus/spice-and-wolf/` as a real example.
2. Start from the minimal config, `config/series.probe-gutenberg.yaml`: an id, a title and a
   folder. The defaults cover a generic novel: the chapter heading tag is detected from the book
   (`<h1>` for Yen Press, `<h2>` for Project Gutenberg), a single EPUB with no volume number is
   volume 1, and common English titles (`Mr.`, `Dr.`, `Sir` ...) count as honorifics. Every prompt
   says "novel", whatever the source. Then run `wiki ingest` and `wiki audit ingest`: a WARN about a volume parsed as
   too few chapters means set `epub.heading_tags` by hand. Only then **inspect the actual EPUBs
   before adding quirk sections** (`epub`/`speech`/`scene_breaks`); `config/series.spice-and-wolf.yaml`
   and `config/series.overlord.yaml` show what a real inspection pass records.
3. If this series has no equivalent of 86's Para-RAID/radio-comms channel, leave
   `speech.require_para_raid` unset (or `false`) — otherwise `wiki audit ingest` will FAIL every
   volume for correctly having zero of a channel that was never going to exist. Set it `true`
   only for a series that really does have such a channel, the way `series.86.yaml` does.
4. Run with `--series <name>`. Every stage's output lands under its own `data/<name>/` and
   `dist/<name>/site/` subtree (`paths.py::set_active_series`) — a second series never
   interleaves with or overwrites another's data. The one exception is the LLM response cache
   (`data/cache/llm/`), which is shared across every series on purpose: it is content-addressed,
   so a cached answer is valid no matter which series asked.

No code changes. If a series does need a code change, that is a bug in the ingest layer worth fixing
rather than working around.

---

## 7. Choosing a model from the command line

Every command that resolves a stage (`doctor`, `gazetteer`, `extract`, `graph build`, `verify`,
`synthesize`, `site build`, `run-all`) accepts a repeatable `--model`/`-m` flag. This is the
supported way to try a different model — editing `config/models.yaml` is for a change you want to
keep as the default; `--model` is for one run.

```bash
# One stage, a role already defined in config/models.yaml:
wiki extract --model claim_extract=groq_fast

# Every stage in the command, same role (multi-volume run-all needs --i-accept-unaudited too,
# see §1 -- a single volume, as here, doesn't):
wiki run-all --volumes 1 --upto 1 --model google_best

# A model with no profile yet -- clones a same-provider profile's options/pricing ad hoc,
# and prints a warning that the cost estimate is inherited, not measured:
wiki synthesize --upto 2 --model prose=google:gemini-2.5-pro

# Repeatable -- combine a global default with a per-stage exception:
wiki run-all --model google_fast --model claim_extract=local_fast --i-accept-unaudited
```

`vertex_pro` (`gemini-3.1-pro-preview`) is defined but deliberately unrouted — reach for it as
`--model verify=vertex_pro` only when an audit shows the default is actually wrong, since it
measured no better per dollar on every stage tested (`docs/vision/PHASE_28.md`).

A **role name** (`groq_fast`, `google_best`, ...) is the safe form — it carries its pricing,
`api_key_env`, `base_url` and provider-shaped request options with it, all defined once in
`config/models.yaml`. An **ad-hoc `provider:model`** is for trying an id that has no profile —
useful for a one-off, but its cost estimate and request options are borrowed from an existing
profile for that provider and may not match exactly; add a real profile in `config/models.yaml`
once you know it is the one you want.

`wiki doctor --model ...` is the fastest way to check an override before spending anything: it
shows the override in the "Stage -> model routing" panel and health-checks it like any other
profile. An override always wins over the normal fallback-on-missing-key behaviour — if you name
a model explicitly and its key is not set, it fails at call time rather than silently using
something else.

`run-all` forwards `--model` to every stage command it chains, so one flag covers a whole run.

## 8. The extraction taxonomy and per-run tuning

`config/extraction.yaml` is the one taxonomy every series uses (age, gender, kinship, affiliation,
the concepts every narrative has). The per-series overlay `config/extraction.<series>.yaml` was
removed in Phase 32: a schema tuned for one series is exactly what req. 8 forbids. A change that
helps one series goes into `extraction.yaml` for all of them, and is re-checked on the others.
`wiki doctor` prints the effective taxonomy.

**`--attr`/`--trait`/`--relation`/`--only`/`--skip` — ad-hoc, for one `wiki extract` run.** The
same "try it without committing to it" ergonomics as `--model` (§7). Nothing here is written to
any config file.

```bash
# Add a predicate just for this run, to see if it's worth adding to extraction.yaml:
wiki extract --volumes 9 --attr 'KARMA=short_text:"Karma"'

# A trait (feeds personality/background prose) and a relation:
wiki extract --trait 'CATCHPHRASE="Catchphrase":6'
wiki extract --relation 'ALLY_OF="Ally of":symmetric'

# Narrow a run to exactly these predicates -- everything else is skipped:
wiki extract --only AGE --only OCCUPATION

# Or skip a whole kind:
wiki extract --skip relation   # attributes and traits only, no relation extraction this run
```

`--attr` format: `NAME=format:"Display"[:multi]` (single-valued unless `:multi` is given).
`--trait` format: `NAME="Display"[:max_words]` (default 10). `--relation` format:
`NAME="Display"[:symmetric|:inverse=OTHER_NAME]`. `--only`/`--skip` each take a predicate name
(`AGE`) or a kind word (`attribute`/`relation`/`trait`), and are repeatable.

A predicate added with `--attr`/`--trait`/`--relation` only affects the `wiki extract` invocation
it was given to — claims for it land in `data/03_claims/`, but `wiki synthesize`/`wiki site build`
need the SAME predicate in `config/extraction.yaml` to render it as a page field (their
config-driven rendering, `synth/assemble.py`/`site/okf.py`, reads that file, not a prior run's
ad-hoc flags). Add a predicate there once `--attr` shows it is worth extracting for every series.

**Adding a whole page section — `page_outline`.** The predicate axis (above) controls what gets
extracted; `page_outline` (same file) controls the character page's own section list — which
sections exist, their order, their titles, and, for an LLM-generated section, which trait
predicates feed it. A new prose section (e.g. an "Abilities" summary built from a new `ABILITY`
trait) declares both in `extraction.yaml`:

```yaml
# config/extraction.yaml
traits:
  ABILITY: { display: "Ability", max_words: 10 }
page_outline:
  abilities: { order: 6, kind: prose, title: "Abilities", traits: [ABILITY]
             , min_sentences: 2, max_sentences: 4
             , subject: "a short summary of abilities", source: "ability facts" }
```

No `--attr`-style CLI override exists for `page_outline` yet. There is
nothing to edit in `synth/assemble.py`, `synth/prose.py`, `site/okf.py`, or `app.js` for this —
every one of them reads the section's `kind` generically (docs/CONTRACTS.md §5).

Two Phase 20 knobs worth knowing about: a `kind: prose` section may add `sentences_per_evidence`
(e.g. `0.5`) so its `min_sentences`/`max_sentences` become the floor/ceiling instead of the fixed
bounds — the actual sentence count sent to the model scales with how much evidence exists for that
character. A section may also use `kind: quotes` (config carries `max_quotes` instead of
`traits`/sentence bounds) — a deterministic, no-LLM section that pulls top verbatim quotes for the
character from `data/04b_events/events.db` (requires `wiki events-build` to have run; the section
is simply omitted for a series/cutoff with no events.db or no quotes).

## 9. Inspecting future-fact matches in cutoff pages

Run the deterministic probes against the current working scope:

```bash
wiki probe run index --series spice-and-wolf --volumes 1-2
wiki probe run disclosure --series spice-and-wolf --volumes 1-2
wiki probe run leak --series spice-and-wolf --volumes 1-2
wiki audit probe --series spice-and-wolf
```

These commands read existing artifacts and make no LLM calls. The audit combines the newest
measurement for each probe/cutoff across runs. Each run prints its canonical JSONL path;
`--json <path>` on `probe run` also saves a copy.

`disclosure` checks whether the index's surface strings appear in the text available at each
cutoff. It reports exact occurrence separately from case-insensitive occurrence with a leading
article removed. JSONL includes first-occurrence offsets and the unseen strings. This distinguishes
strings absent from the reading prefix from strings that occur there but fail candidate mining.
Neither check proves that a reader knows the corresponding entity identity or future fact.

Candidate-mining rows record the threshold and candidate cap used for replay, with their sources.
The probe prefers the gazetteer's recorded build parameters. For older gazetteers without a
recorded threshold, it retains `min_mentions=3` and the audit warns that this is assumed.
Missing candidate files or a gap in parsed volumes produce an error instead of a zero rate.

Vocabulary exposure includes both extraction and scene claims, merging evidence for duplicate
claim IDs. Its JSONL rows list `claim_files` and `tainted_claim_ids`; the audit shows the source
file count and unique claim denominator. Missing mentions or both claim sources for a volume
fail the measurement. Existing empty files remain valid.

If an input cannot be read, inspect its JSONL `error` row. The probe saves its diagnostic output,
marks the run failed, and exits 1; other cutoffs can still have valid results in the same file.
An audit with no measurement rows exits 2 (not ready). A nonzero match rate alone does not fail
either command. The leak probe opens the existing graph read-only and never applies migrations.

The leak probe also fails when no page file is available at or before a cutoff, even if there
are no future facts to search. Each successful row records `artifact_pages`: the paths, character
IDs, and SHA-256 hashes of every searched file, including pages with no matches. The audit shows
the file count. This lets you inspect what was measured; it does not guarantee that synthesis
finished for every character. Earlier-page fallback and intentional evidence gating still apply.

The leak rows include `matches`: one first-occurrence witness for each counted future claim.
Use `claim_id`, `subject`, `predicate`, and `first_vol` to identify the future claim. Each
`locations` entry identifies the actual page file, a JSON Pointer to its field, the field's
recorded `text`, and the matching `start`/`end` character offsets. A cutoff that reused an earlier
page points to that earlier file. Full schema: [CONTRACTS.md §9](docs/CONTRACTS.md#9-probe-measurements--dataseries_runsrun_idprobeindexjsonl).

The rate measures literal value occurrence. For example, a person's name may appear in Volume 1
even though a relationship involving that person is only established in Volume 2. Inspect the
witness and claim together before treating a hit as a spoiler; this probe does not judge meaning.

The audit also prints first-match diagnostics: how many matched claims have identical earlier
graph evidence, match inside a longer word (such as `male` in `female`), or match on another
character's page. The witness JSON includes the earlier claim IDs and the two flags. Counts can
overlap and do not change the score. These are inspection aids, not automatic spoiler verdicts;
word and page flags describe the first occurrence only.
