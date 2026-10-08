# Human review of the AI draft — 2026-10-07

**What happened.** Codex drafted all 554 answers ([CODEX_REVIEW.md](CODEX_REVIEW.md)); Claude checked the draft
and corrected the package ([CLAUDE_CHECK.md](CLAUDE_CHECK.md)); a human then reviewed the checked sheet
([ai_draft_checked.csv](ai_draft_checked.csv)). The maintainer reported on 2026-10-07: "human checked your review
and has decided your labels are fine", and delegated the open questions ("pick the ones best suited by your
knowledge and internet search").

**What this is, for the paper.** A human review that accepted AI labels. It is not an independent blind
annotation: the reviewer saw the AI answers and their reasons, so no inter-annotator agreement between a
human and an AI can be computed from it, and the paper must not claim one. The second round the instructions
describe (items answered `not shown`, and yes/no disagreements shown again with a witness) was not run.

**Accepted labels:** [final_labels.csv](final_labels.csv). Claude's label where it differs from Codex's
(39 rows: 13 G, 7 R, 7 P, 12 C), Codex's elsewhere.

| Part | Items | Accepted labels | Same as the earlier AI label |
|---|---:|---|---|
| G | 111 | yes 85 · partly 23 · no 3 | no earlier label |
| R | 235 | yes 172 · partly 6 · no 57 | 204 (0.868); 215 (0.915) after the corrections of CHANGES C43 |
| P | 148 | yes 118 · partly 21 · no 4 · not shown 5 | 120 (0.811) |
| C | 60 | yes 32 · partly 16 · no 11 · none cited 1 | 46 (0.767) |

Direction of the differences: in R and P the accepted labels are stricter. R: 21 `no` against an earlier `yes`,
4 the other way. P: 17 `partly` against an earlier `supported`. C goes both ways: 7 `partly` against an earlier
`sufficient`, 6 more lenient than the earlier label.

## Decisions taken on the open questions (Claude, delegated)

1. **Part G, a paragraph that announces or plans the event: `partly`.** The 12 gold facts keep their place: in
   each, the event is completed in the volume the fact is dated to, so no cutoff moves (from the reader's
   knowledge of the chapter order, not a new search). The paper states that
   12 of 111 facts are dated by an announcing paragraph.
2. **Part R, a weaker statement of the same thing: `no`.** Only full support counts, as in the strict nugget
   score that the TREC 2024 RAG track uses as its primary metric (support = 1, partial support = 0). The standing
   rule of `recall_hand/anne.provenance.md` already asked for the same event, relation or attribute; five facts
   had been read more leniently in some cells. They were re-read in every cell of every system under one test per
   fact, with a sixth fact that had been read less leniently in some cells (CHANGES C43).
3. **"alive (assumed)": supported** when the character acts within the cutoff and no death is told (three rows now
   agree; the phantom page "Carrots" stays unsupported).
4. **The six doubted labels:** R189 and R195 `yes` (the wiki states the fact); R024 and R226 `no` (no sentence
   has Marilla urging); C008 and C012 `sufficient`.

## Still owed for the paper

Who reviewed (role, not name), whether paid, time taken, whether they knew the books, and consent to release
the labels. Only the maintainer can supply these.

Sources for decision 2: [Initial Nugget Evaluation Results for the TREC 2024 RAG Track with the AutoNuggetizer
Framework](https://arxiv.org/abs/2411.09607); [The Great Nugget Recall](https://arxiv.org/abs/2504.15068).
