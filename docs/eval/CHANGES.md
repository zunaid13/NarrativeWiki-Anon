# CHANGES.md — every change to the frozen system or its evaluation, with reason and values

**Append-only, newest last.** One entry per change that can move a reported number: what changed,
why (the observation that triggered it), the values before and after, which cells it invalidated
and how they were redone, and whether it bears on an ablation (appendix G), a reproducibility
statement (appendix J) or a limitation. Numbers here must also have a row in `docs/MEASUREMENTS.md`
(the ledger); this file is the narrative that ties rows to the change that caused them.

Maintainer instruction 2026-09-30: "note the changes made and their reason including values if
necessary for the ablation study".

Kinds: **SYS** (system: changes what the wiki contains), **RENDER** (system output format only; no
claim, verdict or prose changes), **EVAL** (measurement protocol), **BASE** (baseline harness),
**INCIDENT** (a run failed or was repeated; no code change).

---

## C1 — 2026-09-29 — BASE — baseline cast at cutoff t (freeze `anne-v1f`, commit 4edf972)

- **What:** `scripts/baselines/common.py::present` replaces `mentioned`. A gold character is in a
  baseline's cast at t iff a gold fact about them is evidenced by t, or the prefix names them in
  full. Before: full name **or any single word of it**. Stale pages are deleted before a cell writes.
- **Why:** single-word matching let "Aunt", "Little", "Mary", "Marshall" admit future characters.
  At t=1, 11 characters never named in full in v1 got pages (Aunt Jamesina, Little Jem, Mary Vance,
  Marshall Elliott, Walter/Rilla Blythe, Susan Baker, Faith/John Meredith, Lavendar Lewis, Anthony
  Pye): the page *title* disclosed a future name, and v1 facts were filed under them ("Mr. Marshall"
  the candidate minister -> Marshall Elliott).
- **Values:** Anne cast t=1..5: 25/33/38/41/43 -> **14/21/25/33/43**. Recall denominators unchanged.
- **Invalidated / redone:** every baseline cell rendered before v1f (X1, B2, B3 t=1..3); re-rendered
  by `anne_resume.sh` (surviving pages were cache hits). B3 t=1 re-render verified: 14 pages.
  Middlemarch tuning cells used the old rule (development only; not re-run).
- **Paper:** appendix D (baseline cast rule); a baseline-harness fix, not an ablation.

## C2 — 2026-09-29 — RENDER — page-less characters named, not linked; out-of-scope leftovers removed (freeze `anne-v1g`, commit 940bfae)

- **What:** `site/mkdocs_wiki.py` codex rosters and timeline participant lists link a character
  only if it has a page at that cutoff (the rule relationship pages already had). The bundle step
  deletes `timeline/v*.json` outside the run; the wiki step deletes cutoff dirs above `--upto`.
- **Why:** S01 `site` failed twice on the strict MkDocs build: places roster links to Wilson, Blythe,
  Margaret (6 dead links, v01–v04). A `timeline/v06.json` and `dist/anne/wiki/v06/` left by an
  `--upto 6` build gave 15 `audit links` errors and 1 "spoiler-safety violation" (index vs disk
  timeline count) and were still being published.
- **Values:** after: `audit links` OK, 2110 Markdown pages, 242 character bundles, 130 codex
  entries, 376 relationship bundles, 5 timelines.
- **Invalidated:** nothing measured (no claim, verdict or prose changed). A1/A6 unaffected.

## C3 — 2026-09-29 — INCIDENT — site rebuild overlapped the t=1 leak screen

- The $0 site rebuild for C2 (18:46–18:48 UTC) ran while S01's `leak-t1` (18:45–18:58) read the
  pages. Re-run at 20:34: **identical** apart from run id (0/34 future, control screen 0.391).

## C4 — 2026-09-30 — INCIDENT — B4 t=5 leak screen crashed

- Native crash after MiniCheck loaded (no traceback), most likely GPU memory with Ollama resident.
  Re-run alone 03:56 UTC: rc 0, future 0/5, control screen 0.4505, 30,849 page words.
- B4 t=4's precision *screen* reported 9/40 because the local judge timed out (Ollama 300 s) on
  27/40 rows; the sample rows are valid, the screen value is not a measurement.

## C5 — 2026-09-30 — EVAL — M3 precision needs the all-assertion population (runner `anne_m3.sh`)

- **What:** per cell, `assertion_inventory.py <s> --upto t` then
  `assertion_precision.py <s> --upto t --n 40 --population all` (local atomizer, $0).
- **Why:** the chain's `score` used the default `--population cited`. M3 (setup §5) is over *all*
  sampled assertions, uncited included, and M8 counts an uncited assertion as insufficient. X1
  (closed book: every citation dropped by construction) has cited pool **0 at t=1..5**.
- **Values (cited pools, for contrast):** B2 300/522/645/843/1150; B3 185/355/472/638/837;
  B4 270/499/619/847/1127; B1 310/488 (t=1,2); X1 0 everywhere.
- **Paper:** the cited-only sample becomes a secondary figure (precision of cited lines); M3 comes
  from `--population all` for every system.

## C6 — 2026-09-30 — EVAL — gold character scored on the system's own page(s) (freeze `anne-v1h`, commit 10f7c91)

- **What:** `probe/parametric.py::gold_pages` + `docs/eval/parametric/pages/anne.yaml` (17 gold
  names -> B5 page slugs, scored as the union). Used by `pipeline_page_leak.py`,
  `recall_review.py`, `retention_trace.py`, `leak_screen_audit.py`. Baselines are unaffected
  (their cast is the gold names, so the slug always matches; no map file for `anne@*`).
- **Why:** B5's canonical names differ from the gold's ("Diana" for Diana Barry, "Susan", "Walter",
  "Jem", "Phil Gordon", ...), and some people are split over two pages ("anne" 2,584 words +
  "anne-shirley" 2,089 at t=1; "matthew" + "matthew-cuthbert"). Scored by gold slug, **12 of 43 gold
  characters read as page-less at t=5 for B5 only**; B5's control screen (0.35–0.46) sat below B2,
  B4 and even closed-book X1 at t=1 (0.391 vs 0.435). The leak screen was equally blind: a future
  fact on B5's `walter` page was never screened.
- **Rule:** list every B5 page whose content is about the gold character (read at t=5). A page that
  merges two people stays listed, so its other person's text counts against precision and a later
  person's facts on it count as leaks -- merges are reported, never filtered.
- **Values:** before (by gold slug): future 0/34, 1/25, 1/12, 0/3, 0/4; control screen 0.391, 0.457,
  0.455, 0.382, 0.351 (t=1..5). **After** (rescore 2026-09-30, runs 20260930T045847 v1, T052030
  v2, T054628 v3, T061446 v4, T064942 v5):

  | t | future screened / positives | control screen | page words | gold chars page-less |
  |---|---|---|---|---|
  | 1 | 44 / 1 (was 34 / 0) | 15/23 = 0.652 (was 9/23 = 0.391) | 21,271 (was 12,969) | 26 (those without a page at t=1) |
  | 2 | 35 / 2 (was 25 / 1) | 29/46 = 0.630 (was 21/46 = 0.457) | 34,297 (was 22,945) | 18 |
  | 3 | 21 / 2 (was 12 / 1) | 41/66 = 0.621 (was 30/66 = 0.455) | 41,762 (was 27,613) | 13 |
  | 4 | 10 / 0 (was 3 / 0) | 49/89 = 0.551 (was 34/89 = 0.382) | 53,000 (was 32,640) | 8 |
  | 5 | 5 / 0 (was 4 / 0) | 63/111 = 0.568 (was 39/111 = 0.351) | 70,105 (was 42,208) | 0 |

  More future facts are screened (pages that were invisible now are), and the screen positives
  are all marriage claims that need hand adjudication: t=1 "Anne marries Gilbert Blythe" (page
  says "unspoken romantic speculation"; agent draft: not a disclosure), t=2 "Gilbert marries Anne"
  and "Diana marries Fred Wright" (v2 has Diana's engagement), t=3 both Anne/Gilbert (v3 ends
  with the engagement). Screen values, not M1/M4.
- **B5 entity-resolution errors found while drafting** (report as system errors; candidates for the
  A3 alias ablation and for appendix L): `di` merges Diana Barry's description with Di Blythe (v5);
  `walter` merges Walter Shirley (v1) with Walter Blythe (v5); `marshall` merges Mr. Marshall (v1)
  with Marshall Elliott (v4); `mary` merges Mary Keith (v2) with Mary Vance (v5); `west` gives
  Rosemary West Leslie Moore's grandmother's kinship; `anne` carries v4 aliases ("Mistress Blythe",
  "Mrs. Doctor") on a v1 entity (the build-channel leak A1 measures).
- **Invalidated / redone:** B5 leak screens t=1..5 re-scored; B5 hand recall (`recall_review.py`)
  must use the new lookup (it had not been done yet).

## C7 — 2026-09-30 — BASE — X1's closed-book prompt names the work (freeze `anne-v1i`, commit 9a813c3)

- **What:** `scripts/baselines/common.py::prompt` — the no-passages branch now opens with
  "The novels: {title}, volumes 1-{t} ...". Every system that passes passages is byte-identical.
- **Why (found by auditing API responses):** X1 was never told which novels. Its t=4 answer for
  Leslie Moore was a "Senior Researcher / Cryptographer and Linguistic Analyst"; the repair retry
  then returned all nulls, so the page was 18 words (Reverend Jo 31, Susan Baker 24 words). X1 is
  meant to measure what the model knows of the series (appendix D), so it measured guessing.
- **Values before (invalid, kept for the record):** X1 control screen t=1..5 0.435, 0.326, 0.348,
  0.337, 0.270; future positives 0/37, 1/26, 2/10, 0/4, 3/5; page words 5,075 / 6,800 / 8,539 /
  10,214 / 15,410. After: pending `docs/eval/runners/anne_after.sh` (X1 t=1..5 after the chain, ~$1).
- **Paper:** X1 numbers come only from the re-render. The contamination argument (X1 leaks from
  memory) must be re-checked on it — with the title it should know *more*, not less.

## C8 — 2026-09-30 — RENDER — no page or pair page before any of the entity's names is known (freeze `anne-v1j`, commit 0ba2485)

- **What:** `site/mkdocs_wiki.py` — a character page is emitted at cutoff t only if the entity's
  gazetteer `first_vol` <= t (the volume index already applied this); a relationship page is
  skipped if either party's `first_vol` > t.
- **Why (found in the artifact-exposure report):** Anne v3 "that Senior ... Will Leslie" was
  resolved to Leslie Moore, all of whose surface forms are first seen in v4. The t=3 wiki had a page
  titled "Leslie Moore" (title + slug disclosure). Same rule removed pair pages naming George (v2)
  and West (v4) early.
- **How applied (targeted, not a full rebuild — the M3 runner was reading the tree):** shadow build
  of `wiki site build --upto 5 --html` into a temp dir, diff vs live: exactly 5 Markdown files differ
  (v02 `anthony-pye.md` + `anthony-pye--george`, v03 `leslie-moore.md`, v04 `leslie-moore.md` +
  `leslie-moore--west`); bundle identical. Only those files (and their HTML) were replaced; live
  tree then identical to the shadow build.
- **Values:** exposure t=3 strings hit 4 -> 3 (Leslie Moore gone), slug hits 1 -> 0; `audit links`
  OK. B5 leak screen re-run t=2,3,4 only (the changed cutoffs): t=2 and t=4 unchanged; t=3 future
  screened 21 -> 19, positives unchanged (2), control screen unchanged (41/66). Cited precision
  samples t=2..4 checked row by row: none drew on a changed line, so they stand.
- **Stale by this change:** the M3 inventory of anne t=2 was running on the old v02 tree; it is
  redone after it finishes (atomizer calls are cached).

## C9 — 2026-09-30 — BASE — prompts with passages no longer name the work (freeze `anne-v1k`, commit 118add8)

- **What:** `common.prompt` with passages now opens "Passages from a novel series, volumes 1-t";
  only the closed-book branch (X1) names the work (C7).
- **Why (maintainer question: "won't naming the novel cause some bias?"):** checked every B5 stage's
  prompts (claim/infobox/backstory/scene extraction, verify, prose, codex summary): **none names the
  novel** ("passages of a novel"). Every baseline with passages did ("Passages from Anne of Green
  Gables series"), giving them parametric recall and a parametric leak channel the system under
  test does not have — biasing recall toward the baselines and the leak comparison toward B5.
  X1 keeps the title because it IS the parametric-memory diagnostic (never ranked).
- **Invalidated / redone:** every B1–B4 page (B3's LightRAG index and query are untouched; only its
  page call changes). B1 t=5 was stopped mid-run (process killed 07:59; it had the old prompt) and is
  re-run by the chain's own retry under the new prompt. B1 t=1..4, B2/B3/B4 t=1..5 re-rendered by
  `anne_after.sh` after the chain (≈ $20; B1–B4 page calls cost $19.11 so far). X2 had not started and
  runs under the new prompt. Values before (title-bearing, superseded): MEASUREMENTS rows of
  2026-09-29/30 for these cells.
- **Incident in the same step:** `anne_after.sh` waited for `all done|stopping` in the chain log and
  matched a 2026-09-29 "site: FAILED ... stopping" line, so it started at 07:37 beside B1 (two Vertex
  jobs). X1 t=1 finished (valid: X1's prompt is identical under v1j/v1k); X1 t=2, t=3 were killed
  mid-cell. The waiter now reads only lines written after it starts.
- **Paper:** appendix D — "no system with passages is told the work's title; the closed-book X1 is,
  because it measures what the model remembers of the series."

## C10 — 2026-09-30 — SYS (budget guard) — lifetime cap counts every billed call (freeze `anne-v1l`, commit ad5a614)

- **Incident that led to it:** B1 t=5 (ceiling $10.05) stopped at $10.03 on `budget.hard_stop_usd`
  ($10 per run) at ~38/43 pages; attempt 3 replayed 39/43 pages from the cache and finished (10:15).
  The pre-flight check of queued cells (ceiling vs per-run stop) had not been done; now it is:
  only X2 t=5 (ceiling $10.05) will need the same extra attempt; A1 steps peak at ~$2.8 per run.
- **What:** `llm/budget.py::lifetime_spent` = max(sum of `data/*/budget.json`, sum of `cost_usd` in
  `data/*/_runs/*/calls.jsonl`).
- **Why:** budget.json grows only when a run finishes; crashed, killed and hard-stopped runs never
  added their spend. **Values:** cap saw $96.93 of $114.84 billed (−$17.91); anne@b1 $16.50 vs
  $31.55. Outputs unaffected (guard only).

## C11 — 2026-09-30 — BASE — X2 is told the reader's cutoff (freeze `anne-v1m`, commit 030c425)

- **What:** `common.prompt_future_informed` — X2 sees volumes 1–5 and is told "the reader has read
  ONLY volumes 1–t ... do not reveal anything after volume t"; `run_baseline.py` uses it for X2.
- **Why (found by auditing the first X2 cell):** all 14 X2 t=1 calls were cache hits, at $0: X2 was
  given `prompt(passages, ch, vmax)` — "as known after volume 5" at every t, byte-identical to B1
  t=5. It measured nothing. X2 is meant to test the instruct-don't-filter shortcut that CLAUDE.md
  §1 rules out ("never generate from the full set and redact afterwards").
- **Invalidated / redone:** X2 t=1 (old prompt) → redone by `anne_after.sh`; X2 t=2..5 run in the
  chain under the new prompt (committed before X2 t=2 started).

## C12 — 2026-09-30 — EVAL — the observation exporter reads M3's population and only current verdicts (freeze `anne-v1n`)

- **What:** `scripts/eval/paper_observations.py` — (a) M3 reads `precision/<series>_v<t>_all.jsonl`
  (all assertions, uncited included, paper §5), never the cited-only `_v<t>.jsonl`; (b) a leak-audit
  verdict whose `page_sha` differs from the page on disk is skipped as stale (`leak_screen_audit.py
  summarize` already did this; the exporter did not); a recall/precision file older than the newest
  page at its cutoff is stale too; (c) future = `t < reveal <= --last-vol` (default 5; paper §3 F_t),
  so the five reveal-6 gold facts are no longer assessed; (d) gold facts with no verdict are counted.
- **Why (external review, 2026-09-30, reproduced):** with contradictory labels the exporter emitted
  the cited-only label as M3; all 27 Anne leak-audit verdicts (t=1, agent-read) are on pages whose
  sha has since changed, and the exporter imported 18 of them as M4 observations.
- **Values:** Anne B5 export before 18 M4 observations (all stale) → after **0 observations**;
  skipped: stale 16, past scope 2, unlabelled precision rows 120, unassessed gold 555 (111 × 5).
- **Invalidated / redone:** no paper value was exported from the old code (results.tex keys empty).
  Anne t=1 leak audit must be re-read against the current pages (`leak_screen_audit.py sheet`).
- **2026-09-30 defect in C12 found on real data (fix pending, C13):** `page_sha` returns `None` for a
  character with no page, but `leak_screen_audit.py` records `sha([])` = `e3b0c44298fc1c14`, so the
  49 no-page t=1 verdicts are skipped as stale (M4 t=1 exports 41, should be 88). Fix: return
  `lsa.sha(pages)` unconditionally. Also: `uncertain` labels (5) are skipped as unread; paper §5
  keeps them for a sensitivity analysis — export them as their own verdict. Deferred until
  `anne_after.sh` has passed its `anne-v1n` freeze check (a `scripts/` commit now would stop it).

## C13 — 2026-10-01 — EVAL — exporter: no-page sha, uncertain kept, frame errors out (freeze `anne-v1o`)

- **What:** `scripts/eval/paper_observations.py` — `page_sha` returns `sha([])` for a character with no
  page (as `leak_screen_audit.py` records it); M3 `uncertain` = num 0 (R3: not support, stays in the
  denominator); `frame_error` rows leave the denominator and are counted (R2); every M3 observation
  carries `label`/`label_atom` for the R3 sensitivity analyses. `make_blind_sheet.py` moved to
  `scripts/eval/` (output byte-identical).
- **Why:** the defects listed under C12 (49 no-page t=1 verdicts skipped as stale; uncertain dropped).
- **Values:** no paper value exported yet; the Anne t=1 leak audit must still be re-read on current pages.
- **Invalidated / redone:** none (exporter only; runs after the labels).

## C14 — 2026-10-01 — TRANSPORT — Vertex Standard with Flex on 429 (same model); cap offset to console (freeze `anne-v1o`)

- **What:** `vertex_flash` (gemini-3.6-flash) Standard instead of Flex; `flex_on_429: true` retries a
  Standard 429 once on Flex, same model and prompt (`llm/providers/google.py`); price 0.75/3.75;
  `billed_offset_usd` 2.19 → −19.85. `anne_after.sh` now runs beside A1 with 3 attempts per step.
- **Why:** maintainer 2026-10-01: "work faster and complete the project … use standard"; "flex if 429";
  "the model needs to be the same for consistency". Console $130 vs ledger $149.85.
- **Values:** none move — same model, same prompts, same cache keys (transport options are outside
  the key). **Cost** does: cells built after this are priced at Standard (a Flex fallback is logged
  at the Standard rate, over-counting). Compare systems' cost by tokens repriced at one rate
  (`docs/eval/cost/*.json` keep in/out tokens), never by the logged dollars across freezes.
- **Invalidated / redone:** none.

## C15 — 2026-10-01 — PIPELINE GUARD + DRIVER — graph guard trusts a clean extract; 429 wait 15 min (freeze `anne-v1p`)

- **What:** `wiki graph build`'s "mentioned, zero claims" crash heuristic now skips a volume whose
  newest `wiki extract` run recorded `outcome: ok` (`cli._extract_finished_ok`); `run_series.py`
  waits 15 min (was 60) before re-running a step that failed on 429 (maintainer, 2026-10-01).
- **Why:** A1 t=2 failed 3× and A1 t=3 once at `graph` on "God"/"Elaine" (typed CHARACTER by the
  prefix gazetteer; zero claims is correct). The guard only refuses or allows; it never edits output.
- **Values:** none move — S01 and every baseline passed the guard unchanged; A1 t=2/t=3 graphs are
  the graphs the stage would have built.
- **Invalidated / redone:** A1 t=2 resumed from `graph` (`docs/eval/runners/anne_a1_t2.sh`).

## C16 — 2026-10-01 — DRIVER — a retry from scenes restores the pre-merge gazetteer (freeze `anne-v1q`)

- **What:** `scripts/run_series.py` snapshots `gazetteer.json`, `surface_forms.jsonl`, `mentions.jsonl`
  before `merge-epithets` and restores them before any `scenes-*` step, if the gazetteer is still
  exactly what that merge wrote (a rebuilt gazetteer is never reverted). Test in test_paper_numbers.py.
- **Why (hourly audit, 2026-10-01 08:30 UTC):** `merge-epithets` rewrites the gazetteer in place and
  scene prompts list aliases ("Anne Shirley (also: Anne, Anne of Green Gables, Queen Anne)"), so a
  driver retry restarted at scenes on a merged gazetteer: 0 of 49 scene prompts identical between
  A1 t=3 attempts, every scenes/extract call re-billed (A1 t=2 $21.34, t=3 $26.70 by 08:27 vs the
  runner's $10/$15 estimates), and the build on disk is not the one-merge protocol B5 used.
- **Values:** B5/S01 unaffected (its scenes ran once, before its single merge). A1 t=2 and t=3 as on
  disk are invalid for comparison.
- **Invalidated / redone:** A1 t=2, t=3 rebuilt from a fresh gazetteer (`docs/eval/runners/anne_a1_redo.sh`,
  first attempts' calls are cached; the runner stops if scenes-v1 hits < 90%). A1 t=4 not scheduled (budget).

## C17 — 2026-10-01 — EVAL HARNESS — cited one-line quotes are assertions, not a pooled scene unit (freeze `anne-v1r`)

- **What:** `scripts/eval/assertion_inventory.py::units` skips blockquote lines that carry a citation
  (`> text <sub>[cite]</sub>`), which `assertions()` already counts. `assertion_precision.py
  --resample-from OLD` keeps OLD's rows still in the pool (labels intact) and tops up with seeded draws.
- **Why:** baseline pages write quotes on one cited line with no `— Speaker` line; `units()` only closed
  a blockquote on that line, so every quote on a page pooled into one uncited "scene" unit, which the
  atomizer split into speakerless or speaker-swapped claims (29/720 baseline sample rows were frame
  errors, CHANGES R2) and every quote was counted twice. B5 pages close each quote with `— Speaker —`.
- **Values:** B5 and X1 inventories unchanged (40/40 labels kept every cell). B1/B2/B3/X2 pools shrank
  (e.g. B2 t=5 pool now 2,892); kept labels per cell 29–38/40; 66 replacement rows to label.
  Old samples kept as `precision/<cell>_all_preC17.jsonl`.
- **Invalidated / redone:** every baseline M3 pilot value before this (ledger rows 2026-10-01) is
  superseded by the resampled cells; the uniform-replacement argument: the new pool is a subset of the
  old, so kept rows are a uniform subset of it and top-ups are uniform over the rest.

## C18 — 2026-10-01 night — TRANSPORT + BUDGET — Flex for the overnight batch; cap 318 (~$294 real) (freeze `anne-v1s`)

- **What:** `vertex_flash` back to Flex (same model, same cache keys), price 0.375/1.875;
  `lifetime_cap_usd` 255 → 318. anne-decon/anne-remap map +18 names (Stacy, Phillips, Barry, Josephine,
  Jonas/Jo, George Moore, Dick, Charlie Sloane, Moody Spurgeon MacPherson); remap residuals 0 over v1–5.
  `run_baseline.gold_cast` reads a decontaminated series' source gold with names remapped (freeze `anne-v1t`).
- **Why:** maintainer 2026-10-01 night: decontamination work, budget to $300 real; map review delegated.
- **Values:** none move (transport; the map only affects the not-yet-built decon condition).
- **Invalidated / redone:** anne-remap rebuilt ($0).

## C19 — 2026-10-02 — LABELS — M1 recall pilot re-read under one rule for every system

- **What:** all 30 `docs/eval/recall_hand/anne*_v<t>.json` cells re-checked fact by fact
  (`scripts/eval/recall_evidence.py --diff`, new); 108 labels changed (`anne.audit_20261002.log`).
  The rule is now written once for all systems in `anne.provenance.md` (core proposition; both halves
  of a conjunction; incidental qualifiers may be missing).
- **Why:** the first pass carried labels across cutoffs. A "no" from t=1 was never re-read against a
  later tree that paraphrased the fact, which under-credited the baselines (96 no→yes), and a few
  carried "yes" had lost their sentence or were wrong (12 yes→no). B5 had been read page by page at every t.
- **Values:** t=5 B5 91 → 94, B1 93 → 96, B2 89 → 96, X2 87 → 93, X1 68 → 71, B3 64 → 70 (of 111);
  full table in MEASUREMENTS 2026-10-02. B5's apparent recall lead at t=3..4 is gone: B5, B1, B2 and
  X2 are level; X1 and B3 are lower.
- **Invalidated / redone:** the M1 rows of 2026-09-30 (B5 t=4..5) and 2026-10-01 (baselines t=1..3).
  M3 and M4 are untouched. Bears on: main results table (recall column), the "no recall cost" claim.

## C20 — 2026-10-02 — EVAL HARNESS — M4 observations come from the whole-tree file; `run_cost --final`

- **What:** `scripts/eval/paper_observations.py` reads `leak_audit/<series>_tree.jsonl` (R6: every
  future fact against every page) as the M4 source when it exists, checks its `tree_sha` against the
  tree on disk (both recorded recipes accepted), and keeps `uncertain` as a label that counts 0. The
  per-page audit is read only when no tree file exists. `scripts/eval/run_cost.py --final` keeps the
  last `ok` run per (command, scope) and reports sent + cache-hit tokens as one clean build.
- **Why:** R6 (2026-10-01) made the whole tree the unit for M4, but the exporter still read the older
  per-page audit, which only B5 has; baselines had no M4 observations at all. Cost totals over every
  run mixed in retries and superseded re-renders (B1 121.6M tokens recorded vs 69.2M for the cells on disk).
- **Values:** B5 M4 denominators are now |F_t| = 88 / 65 / 45 / 22 (were the per-page audit's); 0
  stated or implied either way. No label changed.
- **Invalidated / redone:** `docs/eval/observations/anne_B5.jsonl` regenerated; six baseline
  observation files are new. Bears on: T2 (M4 column), T5 (cost).

## C21 — 2026-10-02 — DECONTAMINATION INPUT — name map rebuilt (collisions, split names); audit gate; API probe

- **What:** `config/series.anne-decon.yaml` / `series.anne-remap.yaml` `entity_map` 126 -> 239 entries:
  ten stand-ins that were already names in the books replaced (Marilla Hester -> Thirza, Matthew Silas ->
  Obed, Diana Clara -> Isolde, Susan Hannah -> Tabitha, Jane Myra -> Odette, Paul Ralph -> Cedric, Fred
  Horace -> Gideon, Pye Crabb -> Tulk, Charlie Bertie -> Wilfred, Irene -> Zelda); bare first names and
  surnames of every mapped person added (Ellen, Norman, Douglas, Marshall, Elliott, Moore, Ford, Lewis,
  Gordon, Grant, Blake, George, Fred, Wright, Charlie, Vance, Baker, West in name contexts); recurring
  minor people and the Island's place names added. New `scripts/eval/decon_map_audit.py` (collision /
  leftover / duplicate; exit 1 on a blocking finding) gates `anne_decon.sh` before the paraphrase;
  new `scripts/eval/vertex_probe.py` lets a runner wait out a provider outage with one tiny call per
  10 minutes instead of retry bursts.
- **Why:** auditing the paraphrased v01 showed the treated text would have had Marilla sharing a name
  with Hester Gray and one person under two names ("Ellen" x148 beside "Harriet Ainslie"). The
  2026-10-01 check counted only original keys left in the text and reported 0.
- **Values:** none reported yet for the decontaminated condition, so nothing moves. Audit findings
  34 -> 0; remap residual paragraphs 0 of 8,489.
- **Invalidated / redone:** `data/anne-decon/01_parsed/v01.jsonl` and the partial v02 (paraphrase rerun
  with `--force`; unchanged paragraphs are cache hits). `anne-remap` rebuilt ($0). The original-text
  matrix is untouched. Bears on: the whole decontaminated block (Table 2 lower half, appendix I).

## C22 — 2026-10-02 — DECONTAMINATION INPUT — a copied paraphrase is asked for again and labelled

- **What:** `ingest/decontaminate.py`: `kept(before, after)` = share of the input's word 4-grams that
  survive in the rewrite; a rewrite with `kept >= 0.5` is requested once more with `PARAPHRASE_RETRY`
  (the same prompt plus one sentence saying the first attempt copied the paragraph); if it is still
  copied the text is used and the record is `decon: "verbatim"`. `decon_report.json` gains `verbatim`.
  `anne_decon.sh` checks the report before the gazetteer is paid for (a residual original name or
  more than 1% of paragraphs left unparaphrased stops the run; the verbatim count is logged).
- **Why:** auditing the superseded v01 treated text: 72 of 1,788 paragraphs (8.4% of the words) were
  returned 80-100% verbatim and counted as paraphrased; the accept rule only tested emptiness and
  length.
- **Values:** none reported yet for the decontaminated condition. The paraphrased share will be
  lower and honest; the verbatim share is a new reported quantity (`DECON-verbatim`).
- **Invalidated / redone:** nothing measured. Freeze `anne-v1y`. Bears on: appendix I (treatment
  paragraph and I-treatment), the recognition probe (verbatim paragraphs are the ones a model can
  recognise), Table 2 lower block.

## C23 — 2026-10-02 — EVAL HARNESS — M4 candidate search: "marry" and courtship words; screen run on every system

- **What:** `scripts/eval/m4_tree.py` `_EVENT`: `marri` -> `marr[iy]`, plus `court(ed|ing|ship)`, `bride`,
  `betroth`, `passed away`, `killed`, `drown`. `scripts/eval/artifact_exposure.py` (unchanged) run on the
  six comparison systems; it had been run on B5 and the prefix builds only.
- **Why:** the lexical screen found "Ellen West" on X2's cutoff-4 page of Marshall Elliott: Norman
  Douglas's book-five courtship written onto the wrong man. The M4 search had produced no candidate
  for that fact ("persuaded her to marry him" did not match `marri`), so it was absent by default.
- **Values:** X2 t=4 two facts absent -> uncertain (stated/implied unchanged, 8/220; uncertain 4 -> 6).
  144 new candidate sentences over 27 cells read: no other verdict moved. No prefix-cut system changes.
- **Invalidated / redone:** every `leak_audit/*_tree_candidates_v*.jsonl` regenerated; the paper's
  sentence on uncertain verdicts (X2 "twelve" -> fourteen when all uncertain count) and
  `M4-anne-uncertain` are refreshed with the next `paper_numbers` run. Freeze `anne-v2a`. Bears on:
  Table 2 exposure column, section 6.1 disclosure paragraph, Table 4, appendix A (search description).

## C24 — 2026-10-02 — EVAL HARNESS — the assertion inventory survives an unparsable atomizer answer

- **What:** `scripts/eval/assertion_inventory.py::atomize` catches the client's error for one
  sentence and keeps that sentence whole as a single assertion (stderr line names it).
- **Why:** the `anne@p2` t=2 inventory (A1 precision) ended after 30 minutes of local work on one
  sentence for which qwen2.5:14b returned invalid JSON three times.
- **Values:** none moved; no finished inventory met the error. A kept-whole sentence is one
  assertion where the atomizer might have produced two or three, so N can be lower by a few units
  in a cell where it happens (reported on stderr).
- **Invalidated / redone:** `anne@p2` t=2 inventory rerun (`anne_a1_m3b.sh`). Freeze `anne-v2b`.

## C25 — 2026-10-02 — LABELS — M3 precision pilot: baseline negatives re-read against the whole text

- **What:** 34 agent labels on comparison-system atoms changed from unsupported / uncertain / partial
  to supported (`note` keeps the old label and gives the paragraph): B1 t=4 row 30; B2 t=2 rows 12, 20,
  23, 31; t=3 row 0; t=4 rows 16, 26, 28, 37; t=5 rows 5, 23, 25, 36, 39; B3 t=1 rows 9, 11; t=2 row 13;
  t=3 rows 23, 35; t=4 rows 15, 27, 29; t=5 rows 5, 10, 14, 21, 22; B4 t=1 row 7; X1 t=2 rows 0, 3;
  t=3 rows 16, 36; X2 t=5 row 29. No B5 label changed (its 12 atom-level negatives were re-read too).
- **Why:** while reading citations for M8 the agent met atoms it had labelled unsupported that the
  text states. Cause: the first reading searched for the character's name near the fact (missing
  paragraphs that say "she" or name the person a sentence earlier) and leaned on the six cited
  passages the sampler stores. The audit ranks every paragraph up to the cutoff by IDF-weighted
  overlap with the atom and reads the top ones.
- **Values (mean M3, page level):** B1 0.924 -> 0.929; B2 0.880 -> 0.968; B3 0.885 -> 0.950; B4 0.975 ->
  0.988; X1 0.794 -> 0.814; X2 0.905 -> 0.910; B5 0.822 unchanged.
- **Invalidated / redone:** `docs/eval/observations/anne@*.jsonl`, `paper_numbers.json`, results keys
  and figure F2 regenerated. Bears on: Table 2 precision column, section 6.1 "Support", the setup
  paragraph on label audits, limitations. Not done: an audit of labels already "supported".

## C26 — 2026-10-02 — REPORTING — LightRAG's cost now includes its index construction

- **What:** `COST-anne-B3` 3.11 -> **7.59** (index 4.48 + pages 3.11); new keys `COST-anne-B3-index`,
  `COST-anne-B3-pages`. No call was made; the index stage was recovered from the stored call logs.
- **Why:** plan 0015 / review concern 9. `run_cost.py --final` keeps the last ok run per (command,
  scope); the last B3 runs were page-only over an existing index, so the cost file had no
  `baseline_lightrag` stage while Table 1 called the column one clean build.
- **Invalidated / redone:** Table 1 cost column, Appendix G cost paragraph. No metric cell.

## C27 — 2026-10-02 — REPORTING — M8 intervals are now the bootstrap the paper describes

- **What:** `M8-anne-avg-{B5,B1,B2}` intervals: [0.075, 0.163] -> [0.062, 0.169]; [0.585, 0.716] ->
  [0.578, 0.730]; [0.676, 0.809] -> [0.663, 0.831]. Points unchanged (0.112, 0.653, 0.748).
- **Why:** plan 0015 / review concern 7. The printed intervals were pooled Wilson intervals typed from
  the labelling pass; `paper_numbers.json` had no M8 entry. `paper_observations.py` now emits M8
  observations from the `cite` verdicts, so M8 goes through the same character bootstrap as M1/M3/M4.
- **Invalidated / redone:** Table 1 citation column; observations and `paper_numbers.json` regenerated
  (no M1/M3/M4 key changed).

## C28 — 2026-10-02 — REPORTING — paired differences use the cells both systems have

- **What:** `paper_numbers.py` intersects the (character, cutoff) cells of the two systems before the
  paired bootstrap. `M1-anne-avg-B5-minus-B2` 0.051 [-0.064, 0.129] -> **0.050 [-0.067, 0.127]**.
- **Why:** second review round. The paper says differences are paired by character and cutoff over the
  cutoffs both systems have; the code paired characters and kept B5's cutoff-1 observations against B2,
  which starts at cutoff 2.
- **Invalidated / redone:** `paper_numbers.json`; one key in `results.tex` (section 6.2). M1 and M4
  pairs between systems with all five cutoffs are unchanged.

## C29 — 2026-10-02 — EVAL FIXTURES — seeded disclosures validated; implied-disclosure recall restated

- **What:** `docs/eval/m4_seeded/anne_seeds.yaml` v2: 74 of 88 implying sentences and 2 paraphrases
  rewritten so that each establishes its gold fact (subject, event/relation/attribute, object).
  `SEED-oblique` 0.273 -> **0.714**; `SEED-oblique-pron` 0.145 -> **0.623**; `SEED-para` 0.841 ->
  0.845; `SEED-para-pron` 0.764 -> 0.768. v1 files kept beside (`*_v1`).
- **Why:** second review round: several v1 sentences described a different event or dropped the
  fact's object, so their low retrieval measured the fixtures, not the search.
- **Invalidated / redone:** abstract clause, section 6.1, limitations, discussion, Appendix G text and
  table. No system output or label is involved.

## C30 — 2026-10-02 — LABELS — the last four Anne cells labelled; B2 and B4 reported at five cutoffs

- **What:** hand labels (agent pilot, Claude Opus 5.5, not human, not blinded) for B2 t=1 and B4 t=3, 4, 5:
  recall, the 40-atom precision sample, the whole-tree disclosure read, and citation sufficiency for
  B2 t=1. Twelve keys are new (`M1/M3/M4/M8-anne-t1-B2`, `M1/M3-anne-t{3,4,5}-B4`, `M4-anne-t{3,4}-B4`).
- **Values before -> after:** `M1-anne-avg-B2` 0.815 (cutoffs 2-5) -> **0.834** (1-5); `M3-anne-avg-B2`
  0.968 -> **0.960**; `M8-anne-avg-B2` 0.748 -> **0.768**; `M1-anne-avg-B4` 0.674 (cutoffs 1-2) ->
  **0.726** (1-5); `M3-anne-avg-B4` 0.988 -> **0.980**; `M1-anne-avg-B5-minus-B2` 0.050 [-0.067, 0.127]
  -> **0.032 [-0.089, 0.108]**. Disclosure stays at zero confirmed for both systems, now over four cutoffs.
- **Why:** the cells were built on 2026-10-02 and had samples but no labels; until now the paper reported
  B2 at cutoffs 2-5 and B4 at cutoffs 1-2.
- **Invalidated / redone:** `docs/eval/observations/anne@b2_B2.jsonl`, `anne@b4_B4.jsonl`,
  `paper_numbers.json`, the B2 and B4 keys of `results.tex`, the cutoff plot, and every sentence of the
  paper that says "cutoffs 2-5" for B2 or "two cutoffs" for B4. No key of B5, B1, B3, X1 or X2 moved.
- **Bears on:** the dense-retrieval comparison (section 6.2: the recall difference is smaller with cutoff 1
  in) and Appendix G (B4). One B4 page at t=5 was written without context (OPEN_GAPS G10): the B4 row
  carries that caveat.

## C31 — 2026-10-04 — SYS (version 2) — fix batch: entity resolution, citations, gates; B5 and B4 rebuilt as `@v2`

- **What (system, `src/`):** (1) entity resolution: a full name first seen in a LATER volume than a
  bare form is no longer merged for free (the pair goes to the model); a bare first name is assigned
  per volume to the full name that dominates that volume (`alias._assign_bare_forms`, CONTRACTS 2.1
  `vols`), merged when one owner throughout; the fuzzy tier cannot swap a whole word ("...of the
  West"/"...of the East"); the alias-pair cap 1000 -> 5000 (Anne asked 1,000 of 2,116 pairs);
  hyphenated names and "<Name> of (the) <Name>" titles are mined whole (G1, G9). (2) Back matter
  ("Books by ...", "Transcriber's note") dropped at ingest for every series (G12). (3) Timeline
  sections carry the raw chapter index (G6). (4) A prose answer that it found nothing is `null` (G8).
  (5) Codex summaries must pass the local MiniCheck check against their windows, one regeneration,
  else none (G2). (6) Citations: every prompt line is keyed and each generated text names the keys it
  rests on (prose, relationship blurbs, codex summaries); scene summaries name 1-4 paragraphs
  (`summary_para_ids`), printed on timeline, pair and codex pages; relationship blurbs and codex
  summaries print `_Sources:` lines (G7). (7) Scene participants are those who take part, not those
  only mentioned (G5).
- **What (harness, `scripts/`):** B4 resolves a gold name through the page map and writes no page
  without an entity; no text-reading system gets a passage-less prompt (G10); the atomizer is probed
  before work and a run with more than 1% sentences kept whole fails (G11); the inventory attaches a
  `_Sources:` line to the scene above it.
- **Why:** OPEN_GAPS G1-G12; the maintainer's question 2026-10-04 ("isn't not merging anne and
  anne-shirley a huge red flag?") and instruction "do it. fix it".
- **Values before:** every B5/B4 value of Anne (C1-C30) and of Oz is the version-1 system's and stays
  in the ledger; the version-1 trees are kept (`data/anne`, `dist/anne`, git tag `prefix-v1-20261004`).
  Entity-merge audit, Anne v1-5 gazetteer: 42 flags (span 19, surname 13, split 10).
- **Values after:** recorded in MEASUREMENTS as the version-2 cells finish; added here as an addendum.
- **Invalidated / redone:** B5 and B4 on Anne and Oz (rebuilt as `anne@v2`, `oz@v2`, B4 from the v2
  graph), their labels, the A1 prefix builds' comparability, the decontaminated B5 (stopped before
  pages). B1, B2, B3, X1, X2 on Anne do not read the gazetteer and stand; on Oz their t=3..5 cells are
  redone after the G12 re-ingest (t=1, 2 unchanged).
- **Bears on:** every B5 row of Table 1, the citation-sufficiency comparison, the A1 construction-scope
  appendix, the qualitative appendix, the held-out section.

## C32 — 2026-10-04 — EVAL HARNESS — the atomizer runs four sentences at a time; waits for a free GPU

- **What:** `eval_atomize` routes to a new profile `local_eval`: the same `qwen2.5:14b` (Q4_K_M) and
  sampling as `local_fast`, `num_ctx` 16384 -> 4096 (a prompt is one paragraph and one sentence),
  concurrency 4 with Ollama started as `OLLAMA_NUM_PARALLEL=4`. `assertion_inventory.py` sends a
  page's sentences through `llm/parallel.py::map_calls` (same prompts, results in the same order),
  waits before loading the model until 11 GB of GPU memory is free, and takes `--out`.
- **Why:** maintainer 2026-10-04 asked to speed up the local scoring; OpenRouter's free tier was ruled
  out (about 1,000 calls a day; different weights; evaluation spends no OpenRouter quota). The wait:
  a game held 12.5 GB of the card when the change was made.
- **Values before -> after:** none intended. The parallel run of an already-scored cell is compared
  with its serial inventory (`scripts/eval/inventory_equivalence.py`; MEASUREMENTS row) before any
  version-2 cell is scored with it; the new profile's cache keys differ, so every atom is resampled.
- **Invalidated / redone:** nothing; version-1 inventories stay as built. Every version-2 inventory
  uses the new profile, and so does any rerun of a version-1 cell (which must then be redone whole).

## C33 — 2026-10-05 — SYS (version 2) — the codex-summary gate is a model check, not MiniCheck

- **What:** `site/bundle.py::_generate_codex_summary` checks each summary with one `codex_check` call
  (vertex_flash, like `graph/verify.py`): every statement must be stated in or shown by the passages it
  was written from; overstatement ("married" for "engaged"), added detail and wrong attribution fail.
  One regeneration told the failing part, else no summary. Replaces C31's MiniCheck gate.
- **Why:** the 02:19 UTC audit found the MiniCheck gate rejecting **109 of 157** summaries (median score
  0.125) during the anne@v2 site build, including "The Aid Society, also known as the Aids, ... a new
  carpet for the vestry room ... their missionary box" (0.014) whose every term is in its own passages:
  MiniCheck scores the best chunk of 3.5-7.5k characters, and a one-sentence definition that joins facts
  from several paragraphs fails it. It also took 16.7 s per check on the CPU.
- **Values before -> after:** none reported; the anne@v2 site build was stopped before any page was
  written and resumed from the site step (cached summaries reused; ~$0.2 of checks).
- **Invalidated / redone:** the anne@v2 site step and everything after it (audits, leak screens,
  precision samples, scoring, B4).

## C34 — 2026-10-05 — SYS — a bare-name merge never leaves a name first seen later than the entity

- **What:** `alias._assign_bare_forms`: when a bare name merged into a full name makes the entity start
  earlier than its canonical name is first seen, the entity takes the bare name and its id.
- **Why:** audit-gazetteer on anne@v2 (04:09 UTC): `dick-moore` ("Dick", v3, merged into "Dick Moore",
  v4) -- a page title and URL that would name him before volume 4.
- **Values before -> after:** none. In anne@v2 the entity has no v3 claim, page or mention ("Dick Moore"
  occurs nowhere in the v03 tree; checked 04:19 UTC), so its build stands; the decontaminated build and
  any later gazetteer use the fix.
- **Invalidated / redone:** nothing.

## C35 — 2026-10-05 — SYS (version 2) — a claim window never exceeds the token budget: dense clusters are tiled

- **What:** `extract/windows.py::build_windows` splits a mention cluster whose core exceeds
  `max_tokens_per_request` (6,000) into consecutive sub-clusters that fit; context is trimmed as before;
  every mention paragraph stays inside a window. Replaces the documented Phase 3 gap ("a core that alone
  exceeds budget is left as-is").
- **Why:** labelling anne@v2 recall (04:20 UTC) showed t=2 recall 32/46 against v1 40/46, and claims for
  the merged characters down (Anne 241 -> 186, Diana 162 -> 118, Matthew 66 -> 46). Traced: Anne's merged
  entity had 52 windows (v1: 108 over two entities), median 2,833 tokens, p90 28,660, max **77,699** --
  a chapter per extraction call. Tiled: 115 windows, max ~6,050. Diana and Matthew are NOT this bug
  (89 -> 91 and 49 -> 50 windows): v1 extracted their text twice, once per split entity, which sampled
  more facts; v2 extracts each character once and is reported as such.
- **Values before -> after:** anne@v2 recall labels t=1 (19/23) and t=2 (32/46) were made on the pre-C35
  pages; kept as `docs/eval/recall_hand/anne@v2_v{1,2}.pre-C35.json`, not reported. Extraction calls
  ~2,850 -> ~2,930.
- **Invalidated / redone:** anne@v2 from `extract-v1` on (cache serves every unchanged window), then
  scoring and B4.

## C36 — 2026-10-05 — SYS (after version 2) — "not mentioned in the provided scenes" is a no-evidence answer

**What:** `synth/prose.py::_NO_EVIDENCE_ANSWER` (G8) adds the noun "scenes"; test in `tests/test_synth_prose.py`.
**Why:** the hourly audit found `anne@v2`'s Theodora Dix History reading "Theodora Dix is not mentioned in the
provided scenes." at t=3..5: meta-prose the G8 filter missed because its noun list had "scene summaries" only.
**Values:** anne@v2 pages with such prose: 1 page x 3 cutoffs (Leagues v2: 0). **Not rebuilt:** re-synthesizing
anne@v2 would re-render every page and mark the recall/disclosure labels stale (paper_observations' mtime
check) for one sentence; the residual stays on the frozen anne@v2 tree and is listed here. Applies to every
build made after this change (the decontaminated Anne build).
**Cells invalidated:** none.

## C37 — 2026-10-05 — EVAL — Codex source-read first pass, pending human verification

**What:** At the maintainer's request, annotate the blinded A/B packet, every collected Anne v2
precision sample, and audit each existing Anne v2 recall label. Explicit AI attribution accompanies
all exported answers. Precision files retain their original atoms, units, evidence, passages and
screens, with authored page/atom/citation verdicts and witnesses added. The ten main-system samples
and newly available B4 v2 cutoff-1 sample are frozen in the manifest. Future pipeline outputs and
not-yet-ready packages are outside this collected pass.

**Why:** The maintainer asked for agent annotation first and human verification afterward. Screen
verdicts do not substitute for source-read annotations. Packet items were judged within their supplied
evidence boundaries without opening the answer key; recall was checked afresh at each cutoff.

**Values before -> after:** initially nine main-system sample files unlabelled; the main cutoff-5
all-surface sample and B4 cutoff-1 sample arrived during work. All 440 collected precision rows now
have AI labels, plus 120 A items, 60 B pairs and 335 recall checks (955 decisions). The recall audit
proposes 4 differences: Gilbert's Redmond departure at t=2; Leslie/Owen's intended marriage,
Meredith/Rosemary's intended marriage and Mary being fed at the manse at t=5. These interpretive
proposals and all agreements are available for human review; canonical recall files are unchanged.
Raw label tallies and commands are in the measurement ledger, run `codex-annotation-20261005`.

**Validation:** frozen input/page hashes, complete row coverage, label enums, witness cutoffs,
embedded passage equality and preservation of original precision content pass. Source/manifest
snapshot timing is explicit in the README. The sampler embeds only a subset of B4's attached
citations; the reading helper now resolves every attached ID. The main-system samples have no
omitted embedded attachments. Earlier provisional judgments remain in `revisions.jsonl`.

**Cells invalidated:** none. No wiki rebuild, resampling, paid calls, keyed scoring, original recall
replacement or paper score update. The human-study requirement for independent readers remains
pending. This pass is an AI pilot, and its counts must not be presented as human agreement or
verified precision/recall. Review bundle: `docs/eval/agent_annotation/2026-10-05/README.md`.

## C38 — 2026-10-05 — LABELS + EVAL — after the double check of C37: one pair rule, three recall labels, a new human check package

**What:** (1) The pair rule of the C37 first pass is the standing rule: a shared-scene line on a pair page is
supported in page context only when both people take part in that scene (present and acting or speaking); a
person only mentioned, reported or in another episode of the chapter does not. It replaces R1 "generous:
anywhere in the chapter" (2026-09-30, OPEN_GAPS G5). M3 stays the page verdict (`human`); the factual verdict
(`human_atom`) is reported beside it. (2) Three anne@v2 recall labels changed where both AI readers agree:
t=2 *Gilbert is to leave for Redmond College* no -> yes; t=5 *Leslie and Owen Ford are to be married at
Christmas* yes -> no; t=5 *Mr. Meredith and Rosemary West are to marry* no -> yes (rests on an implication;
flagged for the human reader). *Mary Vance eats ravenously at the manse* stays "no" in all seven systems.
(3) `docs/eval/human_check/` replaces `docs/eval/human/` as the human check: the 554-item allocation of
2026-10-04 (gold 111, recall 235, precision 148, citation 60), version-2 pages for the main system and the
graph baseline, no disclosure part, the AI reader's witness paragraphs shown unmarked, and a `not shown` answer.

**Why:** DOUBLE_CHECK.md. The first pass and the version-1 labels used different pair rules, which alone moved
version 2 from below to above version 1 (149 vs 172 of 200 against 162). The strict rule is the wording the
human readers follow and the reading least favourable to the system. The old packet showed version-1 pages
and, in 7 of 12 negative items checked, none of the paragraphs that support the statement.

**Values before -> after:** anne@v2 recall t=2 33/46 -> 34/46, t=5 87/111 -> 87/111 (one up, one down); mean
over cutoffs 0.783 -> 0.787. Precision labels unchanged (first pass as recorded): page verdict supported
33, 27, 33, 30, 26 of 40 at t=1..5 (149/200); factual verdict 37, 35, 38, 39, 36 (185/200).

**Cells invalidated:** version-1 B5 M3 (0.822, R1 generous) is not comparable with any version-2 M3 and must
not be set beside it; the baselines have no pair pages and are unaffected. The 2026-10-02 packet and the
A/B answers of C37 are a pilot of a superseded packet, not part of the human check. Six rows rejected only
for the duplicate `shirley` page and four "alive (assumed)" rows labelled three ways stay as recorded until
the human check (listed in `double_check_claude.jsonl` and DOUBLE_CHECK.md finding 3).

## C39 — 2026-10-05 — LABELS — one pair rule, one reader, both versions of the main system (comparable M3)

**What:** Every relationship-page row of the all-surface precision samples of version 1 (`anne`, 116 rows) and
version 2 (`anne@v2`, 102 rows) was read against the chapter text by one reader (Claude Opus 5.5; AI) and given
two verdicts, stored beside the samples in `docs/eval/precision/pair_strict_claude.json` and computed by
`docs/eval/labelling_tools/pairstrict.py`. **scene** (the standing rule of C38, now written out): the pair shares
at least one episode of the scene summary the line belongs to, both present and taking part; watching from a
distance, being mentioned, quoted, recalled or dead does not count. **line** (stricter, a sensitivity figure): the
pair takes part in the episode the sampled statement itself describes. The stored `human` fields are untouched.

**Why:** maintainer, 2026-10-05: "precision statement needs to be comparable for both since we are writing a
paper". C38 left version 1 on the retired lenient rule (anywhere in the chapter) and version 2 on a stricter rule
applied by a different pass, so 0.822 and 0.748 could not be set side by side.

**Values before -> after (mean over five cutoffs, page-context precision):**
version 1: 0.822 (lenient rule, Codex) -> **0.751** scene rule (34/40, 28/39, 27/39, 33/39, 26/40), 0.604 line rule;
version 2: 0.748 (first pass, Codex) -> **0.784** scene rule (33/40, 28/39, 35/40, 33/40, 27/40), 0.623 line rule.
Factual verdict alone: 0.949 and 0.929. Shared-scene lines whose pair shares an episode: 76/116 and 75/102.
On version 2 the two AI readers agree on the pair verdict in 90 of 99 rows with a supported statement (kappa 0.79):
8 "Codex no, Claude yes" (4 on the duplicate `shirley` page, 4 borderline co-presence), 1 the other way.

**Cells invalidated / still owed:** the by-page-kind keys of version 1 (M3K) and the prefix-build comparison
(A1 M3 of `anne@p2`, `anne@p3`) are still on the lenient rule; they need the same reading before they are quoted
beside these. The human check decides the nine rows the readers split on.

## C40 — 2026-10-06 — LABELS + PAPER — the paper reports version 2; old-rule leftovers recomputed; timeline citations read from the page

**What:** (1) The two leftovers of C39 are recomputed under the standing pair rule by the same reader: precision by
page kind (`docs/eval/labelling_tools/pagekinds.py`; relationship pages 72/102 on the page, 99/102 as statements)
and the prefix-build comparison (`anne@p2`, `anne@p3` against the first full build; verdicts in
`pair_strict_claude.json`). (2) `docs/paper/results.tex` holds version-2 values for the main system and the graph
baseline (`docs/paper/_build/fill_v2.py` after `fill_results.py`); the first build's values stay available under
`REV-*` and `A1-*-full` keys and are printed in one appendix table. The M3 of Table 1 is the scene-rule value
0.784, with the first pass's 0.748, the line-rule 0.623 and the two-reader agreement stated in the text.
(3) **Timeline citations.** `scripts/eval/assertion_inventory.py` did not attach a timeline line's `_Sources:`
paragraphs to it, so the 22 sampled timeline atoms of `anne@v2` were labelled `cite: none` although the page cites
one to four paragraphs. They were read against those paragraphs (Claude Opus 5.5; AI): 11 sufficient. The verdicts
live in `docs/eval/precision/timeline_cite_claude.json` and enter the observations through
`docs/eval/labelling_tools/apply_pair_rule.py`; the stored `cite` fields are untouched. (4) The human check
package shows those paragraphs for its timeline items (`make_check.py::rows_of`); the 554 items are the same.
(5) Paper text, tables, the per-cutoff figure, the qualitative screenshots and the prompt appendix were redone for
version 2; the prefix-build analysis is labelled as made with the first build.

**Why:** maintainer, 2026-10-05: "Old-rule leftovers ... fix it. Paper: results.tex and the PDFs still show v1
values ... the paper needs to be consistent without any form of contradiction". The timeline gap was found while
rewriting the citation paragraph: a kind of page with 0 of 22 sufficient and 0 of 22 cited, on pages that print
sources.

**Values before -> after:**
- by page kind, relationship pages, supported on the page: 65/102 (first pass) -> **72/102**; version 1 was 90/116
  under the lenient rule -> 76/116.
- prefix-build comparison, page-context precision: prefix t=2 34/39 -> **30/39**, t=3 29/39 -> **28/39**; first
  full build t=2 -> 28/39, t=3 -> 27/39; pooled difference +0.013 -> **+0.038** (58 vs 55 of 78).
- M8 main system: 0.457 (91/199) -> **0.512** (102/199); per cutoff 20, 16, 17, 18, 20 -> 23, 19, 20, 20, 20.
  Among cited atoms 91/169 -> 102/191. With three borderline verdicts as not sufficient: 0.497.
- share of assertions with a paragraph citation, main system: 0.840 -> **0.930**.
- unchanged: M1 0.787, M3 0.784, M4 1 of 220, every value of B1, B2, B3, X1, X2 and of the graph baseline.

**Cells invalidated / still owed:** nothing rebuilt. The prefix-build comparison (A1) was not repeated on version
2 (about $29 and one labelling round); the paper says so. The decontaminated build's inventory has the same
timeline gap and needs the same reading when it is labelled (OPEN_GAPS G16). The 22 timeline verdicts and the
relationship-row verdicts come from the reader that helped build the system; the human check samples both.

**Bears on:** Table 1 (M8 of the main system), the citation paragraph of Section 6.2, the by-page-kind table, the
two-builds table, the construction-scope table, Appendix A (the pair rule; the timeline note).

## C41 — 2026-10-06 — LABELS + HARNESS — decontaminated main system labelled (M1, M4); gold file for renamed variants

**What:** (1) `anne-decon@v2` (main system, version-2 code, decontaminated text) recall and disclosure labels by
the same reader, rules and per-fact patterns as `anne@v2`: `docs/eval/recall_hand/anne-decon@v2_v<t>.json`,
`docs/eval/leak_audit/anne-decon@v2_tree.jsonl`. `recall_patterns.py` reads a renamed wiki through the inverse
name map (`unmap`), so patterns, own-page rule and labels are in original names. (2) `docs/eval/parametric/anne-decon.yaml`,
an identical copy of `anne.yaml`: every tool that resolves gold for a variant of a renamed series looked for
that file (the loader's `gold_from` wins over `decontaminate.from_series`), and the first comparison cell of the
matrix failed on it.

**Why:** maintainer, 2026-10-05: "complete 2 first (i want to add it in the paper)".

**Values (new cells, no earlier value):** M1 0.870 · 0.826 · 0.833 · 0.831 · 0.829, mean 0.838; M4 4 stated of
220 (the leads printed as spouses at t=2 and t=3, OPEN_GAPS G18), 4 uncertain. Precision and citation labels wait
for the runner's inventories; the six comparison systems are still being generated.

**Cells invalidated:** none. **Not done on purpose:** G18 is not fixed in `src/` while the matrix runs on the
frozen code; the decontaminated build is reported as built.

## C42 — 2026-10-07 — HARNESS — human-check package: every cited paragraph shown; sentences not cut at "Mr."

**What:** `docs/eval/human_check/make_check.py`. (1) `cited_of` returned the first six cited paragraphs; parts C
and P now show every one. (2) The sentence splitter of part R broke after Mr. / Mrs. / Dr. / St. / Rev.; it no
longer does. Package regenerated, reader zip rebuilt.

**Why:** found while checking the Codex reader draft (`docs/eval/human_check/CLAUDE_CHECK.md`). Part C asks
whether the cited paragraphs alone are enough and showed six of up to 22; the draft answered `no` on 14 of the
24 cut items, 10 of them wrongly for the full list. A human would have answered the same.

**Values before -> after:** items whose text changed: C 24 of 60, P 33 of 148, R 132 of 235, G 0 of 111. Item
numbers, sampled rows and `key.json` labels unchanged (two `witness_shown` counts differ). The unpatched
generator reproduced the old package byte for byte first.

**Cells invalidated:** no paper figure (no human label exists yet). The Codex draft's answers on the changed
items were made on the old text: 19 are re-judged in `ai_draft_checked.csv`; `human_check_review.py verify`
stops at the page hashes by design. **Ablation/appendix:** the human-check appendix must describe the package
as regenerated on 2026-10-07 and say that part R shows a word-overlap selection of sentences.

## C43 — 2026-10-07 — LABELS — human review accepted; recall re-read under the strict reading for six facts; three row labels

**What:** (1) A human reviewed the checked AI draft of the 554-item check and accepted it
(`docs/eval/human_check/HUMAN_REVIEW.md`, `final_labels.csv`); the open questions were delegated. (2) Recall:
six gold facts re-read in every labelled cell of every system, original and decontaminated, with one test per
fact (`docs/eval/labelling_tools/strict_reread.py`; every change with its sentence in
`docs/eval/recall_hand/anne.strict_20261007.log`). 64 labels changed in 41 label files: 48 to `no`, 16 to `yes`. (3) One precision row
(`anne@v2` t=4, "alive (assumed)": uncertain -> supported) and two citation rows (`anne@b1` t=2 row 30,
`anne@b2` t=4 row 37: insufficient -> sufficient), each with the old label kept in its note.

**Why:** the review accepted `no` where a wiki states something weaker than the fact. The standing rule asks
for the same event, relation or attribute; five facts had been credited on a weaker statement in some cells
(*Marilla urges Anne to go on to Redmond* on "her arrangement allowed Anne to go"; *Rachel Lynde says Anne's hair
is as red as carrots* on "criticised her looks"; *Captain Jim tells Anne about lost Margaret* on "lost his
sweetheart Margaret"; *Carl collects toads, bugs and frogs* on "fascinated by insects"; *Walter hates Dan Reese*
on the fight). The same re-read found cells under-credited: Rachel's remark stated as "criticised Anne's red
hair" and labelled `no` (main system t=2..5, three decontaminated systems), and the braids-and-green-eyes
conjunction stated on Anne's page and labelled `no` (main system t=4, 5; prefix retrieval t=5; full text t=4).
One plain error: full text t=2 states the cow sale and was labelled `no`. Strict support only is the primary
score of the TREC 2024 RAG track (support 1, partial support 0).

**Values (recall, t=1..5 and mean; before -> after):**

| System | Before | After | Mean |
|---|---|---|---|
| Main (`anne@v2`) | 19 34 52 71 87 | 19 35 53 73 89 | 0.787 -> 0.802 |
| Long context (`@b1`) | 20 41 56 77 96 | 20 40 54 75 93 | 0.868 -> 0.848 |
| Prefix retrieval (`@b2`) | 21 36 50 76 96 | 21 36 50 76 96 | 0.834 -> 0.834 |
| LightRAG (`@b3`) | 7 30 36 56 70 | 7 29 36 54 66 | 0.552 -> 0.536 |
| Graph baseline (`@b4-v2`) | 19 32 50 71 84 | 19 32 50 70 82 | 0.767 -> 0.761 |
| Closed book (`@x1`) | 19 31 44 60 71 | 19 30 43 59 69 | 0.696 -> 0.683 |
| Full text (`@x2`) | 20 36 55 76 93 | 20 35 53 75 88 | 0.835 -> 0.814 |
| Decon main (`anne-decon@v2`) | 20 38 55 74 92 | 20 38 56 75 92 | 0.838 -> 0.843 |
| Decon closed book | 0 0 1 0 0 | unchanged | 0.003 |
| Decon prefix retrieval | 20 35 47 69 86 | 20 36 48 69 85 | 0.779 -> 0.784 |
| Decon graph baseline | 14 31 49 60 87 | 15 31 50 59 86 | 0.697 -> 0.704 |

Denominators 23, 46, 66, 89, 111. Version-1 files (`anne`, `anne@b4`), the prefix builds (`@p2`, `@p3`) and
`anne-m36` were re-read by the same tests.

**Cells invalidated:** every M1 figure in the paper and its recall tables (to refill in the paper phase:
`fill_results.py`, then the hand-typed keys); the order of systems on the original text is unchanged except
that full text and prefix retrieval swap places (0.814, 0.834). M3 of the main system at t=4 (+1 supported row);
citation sufficiency of long context t=2 and prefix retrieval t=4 (+1 sufficient each).

**Limits:** the re-read covers the six facts the check surfaced, not all 111. A fact read leniently in every
system and never sampled would still be credited. The tests are regexes over single sentences; each changed row
and each kept `yes` was read once. **Ablation/appendix:** the labelling appendix reports agreement on the labels
as they stood before this entry (R 204/235) and after it (215/235), and states the strict rule with these
examples.

## C44 — 2026-10-07 — HARNESS — a baseline page lost to a mismatched bracket is written from its recorded response

**What:** `docs/eval/runners/recover_page.py <series> <B1|X2> <t> "<Character>"` ($0, no model call). It takes
the first recorded response for the character from the newest run of the cell, replaces each closing bracket
that does not match the bracket it closes (outside strings; nothing else), and passes the result through the
same `common.Page` validation, `common.enforce` and `common.render` as `scripts/baselines/run_baseline.py`.
`_baseline.json` moves the character from `failures` to `written` and gains a `recovered` entry. Applied once:
`anne-decon` X2 t=1, "Nell Harcourt" (4 brackets; 2 citations dropped, 123 kept, 4 sentences cut by `enforce`).

**Why:** the page of the main character came back three times (the call and both repair retries) with lists
closed by `}` where `]` belongs. `llm/client.py` rejects that, the three responses are cached, and every rerun
replays the failure for $0: six attempts of the first-pass runner between 08:48 and 10:40 UTC wrote 13 of 14
pages each time. The alternatives were a full-text cell without the character under whom 9 of the 23 gold facts
at t=1 are filed, or a call that goes round the cache; the standing rules exclude the second ("every API output is
written down and re-read, never re-asked").

**Values:** before, `dist/anne-decon@x2/wiki/v01` had 13 pages and no recall, disclosure or precision figure
(the cell was logged FAILED and would not have been scored); after, 14 pages. No published figure moved.

**Cells invalidated:** none. **Limits:** the recovered page is the model's first answer with four characters
changed; a different fault (cut-off output, a wrong field type) is refused by the tool. The frozen runner is
unchanged, so a later rerun of the cell deletes the page again: the cell is logged `X2 t=1: ok` to prevent
that. If the same fault appears at a later cutoff the tool is applied the same way and listed in the ledger.
**Ablation/appendix:** to state in the reproducibility appendix with the count of recovered pages.

## C45 — 2026-10-08 — REPORTING + PAPER — derived values recomputed after C43; statistics from counts; differences before rounding

**What:** (1) `scripts/papers/summary_stats.py` takes a cell's counts ("0.870 (20/23)" is 20/23), not its
three-decimal rate, asserts that each mean prints the digits of the mean key, and writes `results.tex` with LF
line ends. (2) `docs/paper/_build/fill_decon.py` subtracts the original mean from the treated one before
rounding, writes `M1-annedecon-maxloss`, and no longer deletes the line after its block when
`summary_stats.py` ran last. (3) Eleven hand-typed keys of `results.tex` (and their copies in `fill_v2.py`) and
seven typed cells and counts of appendix G were set to what the label files give. (4) New:
`docs/eval/labelling_tools/derived_values.py`, which recomputes that hand-typed layer and exits 1 on a
difference. No label was changed.

**Why:** an audit of the paper against the label files. C43 changed recall labels in 41 files and three
sampled rows; `fill_results.py` refilled the cells and their means, and the values typed from ledger rows kept
their old state. Separately, the statistics table averaged rounded rates, so it printed 0.835, 0.119 and
-0.012 where Table 1 and the text print 0.834, 0.120 and -0.011.

**Values (before -> after):**

| Value | Before | After |
|---|---|---|
| Citation sufficiency among cited atoms, long context | 0.663 (130/196) | 0.668 (131/196) |
| The same, prefix retrieval | 0.783 (119/152) | 0.802 (154/192) |
| Precision of the reported build, line rule | 0.623 | 0.628 |
| The same, statements alone | 0.929 | 0.934 |
| The same, second annotator's pair verdicts | 0.748 (its first pass) | 0.753 (other labels as reported) |
| Recall of the first build | 0.866 | 0.855 |
| Prefix builds, recall at t=2, t=3 | 0.826, 0.864 | 0.804, 0.848 |
| First full build, recall at t=2, t=3 | 0.870, 0.879 | 0.848, 0.864 |
| Negatives on the page, main system | 43 | 42 |
| Gold facts only the main system conveys at t=5 | 3 | 4 |
| Recall mean of prefix retrieval in the statistics table | 0.835 | 0.834 |
| Paired recall difference, main minus closed book (table) | 0.119 | 0.120 |
| The same, main minus full text (table) | -0.012 | -0.011 |
| Treated minus original recall, LightRAG | -0.066 | -0.067 |

The other seven statistics that moved (two standard deviations of Table 1, a median, two extremes) are in the
ledger rows of this date.

**Cells invalidated:** none; no cell and no headline mean moved. **Limits:** the checker covers the values it
names; counts typed in running text (the examples of missed facts, "seven discordant facts") are printed by it
for comparison and still have to be read. **Ablation/appendix:** the two-builds table and the
construction-scope table of appendix G now compare recall under one reading on both sides.

**Freeze:** `summary_stats.py` is under `scripts/`, so freeze `v2j` no longer matches; `v2k` records the tree
after this change. Nothing built is invalidated: the script makes no model call and only formats numbers for
the paper. The finished runners still name `v2j` and would stop on a restart, which is the intended behaviour.

## C46 — 2026-10-08 — NEW METRIC + PAPER — identity dependence; entity discovery run per prefix

**What:** (1) The v2 entity index was rebuilt from volumes 1..t alone for t=1..4 on `anne@v2`
(`docs/eval/runners/prefix_gazetteer.sh`, `wiki gazetteer --gate build`), written under `data/anne@v2/@t<NN>/`.
(2) New: `scripts/eval/identity_dependence.py`, which compares the full build's index, filtered to t, with the
prefix index: same, alias, regrouped, retyped, absent; over all characters and over those with a page.
(3) Paper: Section 6.3 gains a paragraph with the measure and loses the sentence "Avoiding the dependence costs
a build per cutoff (11.58 and 17.23 dollars)"; Appendix G gains Table `tab:identity` and a paragraph;
Limitations says what was and was not rebuilt; 16 keys `ID-anne-*` in `fill_v2.py` and `results.tex`.

**Why:** review 6.3 and DA4 (`docs/paper/REVIEW_2026-10-08.md`): the design-specific threat had no metric, and
the LightRAG baseline builds its index per prefix while the paper did not say why the system does not.

**Values (before -> after):** none existed. New: identity dependence over character pages 0.390 (23/59),
0.256 (30/117), 0.133 (21/158), 0.089 (17/191) at t=1..4, mean 0.217; four indexes cost 0.21 USD. The removed
sentence was misleading for this stage: the 11.58 and 17.23 dollars are whole prefix builds of the first
version (they stay in Table `tab:scope`).

**Cells invalidated or redone:** none. No label, page, graph or existing key changed (1,407 keys compared
before and after: 0 differ, 16 added); `derived_values.py` exits 0. The full build under `data/anne@v2/` was
only read.

**Bears on:** the construction-scope ablation (Section 6.3, Appendix G). Not done, and said so in the paper:
extraction, graph and pages on the prefix indexes, so there is no recall, precision or disclosure for a
prefix-discovered build of version 2; the treated text; a repeat run to separate model variance.
Freeze: `v2l` replaces `v2k` (one file added under `scripts/`).

## C47 — 2026-10-08 — REPORTING + PAPER — cost in tokens, not dollars; the cutoff figure as bars; Section 6.3 cut back to the page limit

**What:** (1) Every dollar figure in the paper is replaced by input and output tokens (maintainer 2026-10-08):
the cost column of Table `tab:main`, Table `tab:efficiency` (now calls, input, output), the cost rows of
Tables `tab:scope` and `tab:builds`, the Cost paragraph of Appendix G, Section 6.3, the discussion ("about an
eighth of the cost" is now "about a tenth of the input tokens"), M13 in the metric list. The sentence comparing
the list prices of the two Gemini models (Appendix L) is removed. 32 keys `TOK-*`, `T5-*-in`, `T5-*-out` in
`fill_v2.py`; the dollar keys stay declared in `results.tex` and are no longer printed. (2)
`scripts/papers/fig_cutoff.py` draws grouped bars, one per system and cutoff, in three stacked panels
(maintainer 2026-10-08); disclosure counts are printed over the bars. (3) The identity-dependence paragraph of
Section 6.3 is one sentence; C46 had put the conclusion nine lines over the eight-page limit.

**Why:** the maintainer asked for (1) and (2). (3) corrects an error of C46, reported there as checked.

**Values (before -> after):** no measured value changes. Each token pair reproduces the dollar figure it replaces
at 0.75 / 3.75 per million tokens (ledger row of 2026-10-08). "An eighth of the cost" (3.97 of 31.80) becomes "a
tenth of the input tokens" (3.42M of 33.13M); output is a fifth (0.37M of 1.86M) and is in Table `tab:main`.

**Cells invalidated or redone:** none. `derived_values.py` exits 0.

**Bears on:** the cost comparison (Table `tab:main`, Appendix G). Freeze: `v2m` replaces `v2l` (one script
changed under `scripts/`: `fig_cutoff.py`, drawing only).

## C48 — 2026-10-08 — PAPER — bold on the values that carry each table's finding

**What:** New `docs/paper/_build/bold_tables.py` wraps chosen `\res` cells in `\best{}` (new macro in
`macros.tex`), by rule and from the values in `results.tex`: the best value of a column among the compared
systems B1 to B5 (per cutoff in the per-cutoff tables), every non-zero disclosure count, the largest loss of
recall on the treated text, the largest mean difference in each direction, the largest stage of the build, the
better of the two builds where a direction exists, the cutoff with the largest identity dependence, the
highest and lowest recall of the graph baseline, the corpus totals. Two tables of typed numerals carry the
mark by hand (seeded search: the lowest rate of each kind; page kinds: the relationship row). Each caption
says what bold means. Tables without values are unchanged. `derived_values.py` ignores the mark.

**Why:** maintainer 2026-10-08: "for every single table, bold out the important values (like minimum or
maximum) or whatever depending on the context and importance of those values in gaining insights".

**Values (before -> after):** none. The step is idempotent and follows a value that moves; it must be run
after `scripts/papers/summary_stats.py`, which rewrites `tables/t-stats.tex` without it.

**Cells invalidated or redone:** none. **Bears on:** reading of every results table; no ablation.
One caption of the main table was shortened by two phrases to keep the conclusion on page 8.

## C49 — 2026-10-08 — PAPER — the mining rule behind part of identity dependence is quantified and stated (G19 left open by decision)

**What:** Appendix G, paragraph "Identity dependence", now gives the split of the candidates a prefix index
lacks: 109, 111, 78 and 44 at t=1..4, of which 91, 87, 60 and 32 have fewer than three mentions in the prefix
and 18, 24, 18 and 12 are dropped by the capitalisation-ratio rule; it says the rule was left unchanged and
why (off, it admits 352 more candidates on the five-volume build; a narrower rule would change the reported
build's index). Limitations gains one sentence. Four keys `G19-anne-*`. New
`docs/eval/labelling_tools/g19_miner_split.py` reproduces the counts.

**Why:** the maintainer chose the recommended handling of OPEN_GAPS G19 (2026-10-08): report how much of the
measure is the rule, do not change the frozen miner four days before the deadline.

**Values (before -> after):** none existed in the paper. The earlier text said only "part of the difference is
the candidate miner".

**Cells invalidated or redone:** none; `src/` is untouched. **Bears on:** the reading of identity dependence
(Section 6.3, Table `tab:identity`): most of it is evidence that later volumes supply, about a sixth of the
lost candidates at t=1 is a rule.
