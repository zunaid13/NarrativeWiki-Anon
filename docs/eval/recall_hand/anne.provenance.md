# Provenance of `anne_v<t>.json` (B5, full v1–5 build)

- **t=1** (2026-09-30): agent pilot, not human, not blinded. Character-page verdicts from Codex's
  refreshed leak audit (`leak_audit/anne.jsonl`, 16 stated + 3 implied, the implied ones paraphrases);
  the 4 it marked absent re-searched over every page of `dist/anne/wiki/v01` by Claude (Opus 5.5):
  "Gilbert rows up … flat sinks" found ("saving Anne from a sinking dory", `character/gilbert-blythe.md`
  — Codex disagreement); braids + green-looking eyes only half conveyed (red braids, no eye colour) →
  "no" (conservative); White Sands school and Phillips on spelling absent.
- **t=2..5** (2026-09-30): agent pilot (Claude, Opus 5.5), not human, not blinded. Whole tree of
  `dist/anne/wiki/v0<t>` (every page except `source/` and `index.md`). New facts of volume t read
  from the top-scored sentences per fact, then targeted searches for any detail not shown; facts of
  earlier volumes carried with one verified sentence-level regex per fact (all re-hit at every later
  t, except "Jane Andrews leaves for the West", conveyed at t=3–4 and gone at t=5). Rule: a compound
  fact is "yes" only when every part is conveyed (e.g. "widower" without "four years ago" = no;
  "youngest daughter" ≠ "youngest of the Blythe children"). "Miss Cornelia marries Marshall
  Elliott" is "no" at t=4 (pages stop at the betrothal) and "yes" at t=5.

# Provenance of `anne@<sys>_v<t>.json` (baselines) and the 2026-10-02 fairness audit

- **First pass** (2026-10-01/02): agent pilot (Claude, Opus 5.5), not human, not blinded. Whole tree
  of `dist/anne@<sys>/wiki/v0<t>` except `source/`. At t=1..2 every fact was read against its
  top-scored sentences; from t=3 a label was carried from t−1 when a lexically close sentence
  (content-word overlap ≥ 0.6) still existed, and only new-volume facts and doubtful carries were read.
- **What that got wrong** (found 2026-10-02, reported to the maintainer): (a) a "no" given at an early
  cutoff was never re-read when a later tree paraphrased the fact (e.g. "Marilla initially intended to
  send Anne back" for *Marilla at first says she is not going to keep Anne*; "named James Matthew after
  Captain Jim and Matthew Cuthbert" for *…partly after Matthew*) — this under-credited the baselines;
  (b) a carried "yes" survived a rewrite that dropped the fact (X1 t=4 says Ruby Gillis is alive);
  (c) B5 had been read more strictly than the baselines on three facts and less strictly on one
  (*Marilla urges Anne to go on to Redmond*).
- **The audit:** every gold fact was re-checked in every cell with one regex per fact
  (`scripts/eval/recall_evidence.py --diff`: rows where label and evidence disagree), and each such
  row was read. 108 labels changed (list: `anne.audit_20261002.log`), 96 no→yes and 12 yes→no;
  B5 changed on 4 (t=4 +1, t=5 +3).
- **One rule for all seven systems** (supersedes the "all-parts" wording above where they differ): a
  fact is conveyed when the tree states its core proposition — same subject, same event / relation /
  attribute, same object — in any wording. A conjunction of two propositions needs both (*newspaper
  man **and** grandson of the schoolmaster*; *high-souled **and** iron-gray hair*; *absent-minded
  **but** a wonderful preacher*). An incidental qualifier may be missing (*four years ago*, *at
  Christmas*, *of yellow fever*, *white*, *stone*); a wrong or missing core element may not (who did
  it, to whom, the number when the number is the fact — *Una is ten*). A statement on the wrong
  character's page does not count. Engagement does not convey a marriage.
- **Limits:** still one agent, unblinded, and the regexes bound what was re-read: a paraphrase that
  shares no keyword with the fact and was labelled "no" in every system would be missed (it would
  be missed equally for all systems). R4 human double-labelling replaces these labels.

# 2026-10-02 (afternoon): the last four cells — `anne@b2_v1.json`, `anne@b4_v3.json`, `anne@b4_v4.json`, `anne@b4_v5.json`

- **Who:** agent pilot (Claude, Opus 5.5), not human, not blinded. Whole tree of
  `dist/anne@<sys>/wiki/v0<t>` except `source/`.
- **How:** every eligible fact was listed with its top-scored sentences
  (`docs/eval/labelling_tools/m1view.py`); every fact whose top sentences did not settle it was searched
  with one regex over the whole tree (`q.py`), and the page was read where the regex left a doubt. No
  label was carried from another cutoff or another system: B4 writes each cutoff's pages afresh and they
  differ (the Avery scholarship is on Anne's page at t=4 and on no page at t=3 or t=5).
- **Rule:** the one rule of the fairness audit above. Borderline readings were decided by how the
  same wording had been labelled in the other cells (`scripts/eval/recall_evidence.py`): "extremely shy"
  conveys *the shyest man alive*; a dare to walk the ridgepole conveys Josie's taunt; "Diana married
  Fred Wright" conveys the engagement; "Thomas fell ill" does not convey *lies on the lounge more than he
  used to*; "sent her flowers" does not convey the orchids; a betrothal does not convey the marriage;
  fear of dying in a final illness conveys *believes she will soon be dead*.
- **Decisions worth a second reader:** B4 t=3 "despite initial hesitation, Marilla chose to keep" was
  read as conveying *Marilla at first says she is not going to keep Anne* (lenient to B4; "agreed to keep
  her" at t=4 and t=5 was not). B4 t=3 and t=5 Paul's quoted "pick out just as good a one the second
  time" with his father's marriage to Miss Lavendar was read as conveying the second-mother fact. B4 t=5
  says Captain Jim died "shortly before the publication of his book": the gold fact (he dies soon after
  it is published) is "no", and the page is wrong against the text.
- **Corrected before recording:** B4 t=3 *Anne helps raise the twins* was first "no"; Davy's page says he
  "reformed under Anne and Marilla's guidance", the wording accepted at t=4 and t=5, so it is "yes".
- **One page is not B4's method:** Carl Meredith's page at t=5 was written with no retrieved context
  (OPEN_GAPS G10). Its facts are labelled as delivered: *collects toads, bugs and frogs* yes, *sore throat
  after the marsh* no.

# Provenance of `anne@v2_v<t>.json` after 2026-10-05 (CHANGES C37, C38)

- First labels: agent pilot (Claude, Opus 5.5), MEASUREMENTS 2026-10-05 "M1 recall, anne@v2". Re-read in full
  at every cutoff by a second AI reader (Codex, GPT-6; `docs/eval/agent_annotation/2026-10-05/recall_review.csv`),
  which proposed four changes; a double check (Claude) accepted three and they were applied: t=2 Gilbert/Redmond
  no -> yes (Affiliations line on his page), t=5 Leslie/Owen marriage yes -> no (the engagement sentence of the
  t=4 page is gone at t=5), t=5 Meredith/Rosemary no -> yes ("she will be a friend rather than a cruel
  stepmother", "the upcoming double wedding": implied acceptance, for the human reader to confirm).
- Not applied: *Mary Vance eats ravenously at the manse*. Every system's label is "no", including version 1,
  whose page says "who fed her at the manse"; the label moves in all cells or in none.

# Provenance of `anne@b4-v2_v<t>.json` (graph baseline rebuilt on the version-2 graph), 2026-10-05

- Agent pilot (Claude, Opus 5.5), not human, not blinded; no second reader yet. Every cell read at its own
  cutoff, nothing carried from another cutoff or from the version-1 baseline: the candidate sentences of
  `recall_patterns.py --view anne@b4-v2` (one stored pattern per fact, `anne.fact_patterns.json`), then a
  targeted whole-tree search for every fact whose candidates did not settle it and for every cell with none.
  The patterns miss 1.8% of the cells already labelled "yes" in the other systems (39/2,186, `--validate`).
- **Result:** 19/23, 32/46, 50/66, 71/89, 84/111.
- **Rules applied as for the other systems:** a missing agent is core (*Anne* gets Diana drunk: "Diana
  accidentally drinking currant wine" is "no", "accidentally intoxicating Diana" and the quoted "I did not
  mean to intoxicate Diana" are "yes"); giving is not longing (Matthew buys the puffed-sleeve dress: "no";
  Anne's goal "to possess ... puffed sleeves" at t=1: "yes"); an engagement, a goal or an accepted proposal
  is not a marriage (Cornelia at t=4, Leslie and Owen at t=4: "no"); a conjunction needs both parts
  (Meredith absent-minded with no word on his preaching: "no"; Captain Jim high-souled and iron-gray: "yes").
- **Lenient calls a second reader should see** (all in the baseline's favour, following the 2026-10-02 audit's
  precedents): t=1 *eyesight is failing* from "severe eye pain ... requiring an oculist"; t=1 *makes eyes at
  Prissy* from favouritism plus "gave all the Mayflowers he found to her" (t=5 "special attention" alone:
  "no"); *spelling is disgraceful* from "displaying her marked-up spelling slate" (t=1) and "holding up her
  slate due to poor spelling" (t=4), but "no" where the slate is held up with no word on spelling (t=2, t=5);
  t=2 *Miss Lavendar marries Stephen Irving* from "Miss Lavendar's wedding" plus Stephen returning to court
  her; t=3 *saves Minnie May* from "Diana sought Anne's assistance during ... croup"; t=3 *going about with
  Christine Stuart* from "accompanied her in social settings"; t=4 *realises she loves Gilbert* from "upon
  learning that Anne loves him, he recovers" (t=5, where the sentence gives only that she was not engaged
  to Roy: "no"); t=4-5 *red hair and green-looking eyes* from "red hair" and "gray-green eyes"; t=4 *dies
  soon after his life-book is published* from "after seeing the book accepted ... passed away" (t=5, no link
  between death and publication: "no"); t=4 *newspaper man* from "author", as accepted for the main system;
  t=5 *pressured by her mother* from "coerced her into marriage with her mother's aid"; t=5 *hates Dan
  Reese* from the fight; t=5 *sullen pupil ... hostility* from "sullen ... impertinent".
