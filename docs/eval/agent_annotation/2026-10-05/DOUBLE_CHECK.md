# Double check of the Codex first pass — 2026-10-05

Reader: **Claude (Opus 5.5); AI; not human**. Requested by the maintainer before the human check.
Row-level disagreements: [`double_check_claude.jsonl`](double_check_claude.jsonl) (33 rows). No first-pass
file, canonical precision file or recall file was changed by this check.

**Not blind for packets A and B.** Earlier in the same session, while inspecting the package, this reader
printed the item-to-system column of `docs/eval/human/key.json`. The AI labels in the key were not read
and the key was not opened again.

## What was read

| Set | Read in this check | Not re-read |
|---|---|---|
| Recall (335) | the 4 proposed changes; 13 of the 26 distinct "no" facts, by whole-tree search | the "yes" rows |
| Precision (440) | all 34 relationship rows rejected for placement, against the chapter text; the reasons of all 28 factual negatives; 10 random accepted cited rows; a wording-overlap screen of every citation verdict | about 370 accepted rows |
| Packet A (120) | the reasons of all 41 "no" / "cannot tell" answers; 12 of them against the book text | the 79 "yes" / "partly" answers |
| Packet B (60) | all 60 pairs | — |

## Findings

1. **Precision: the pair rule differs from the one the earlier labels used.** The first pass asks that both
   members of a pair act in the *same episode*. The v1 labels (2026-09-30, OPEN_GAPS G5) used R1, "generous:
   anywhere in the chapter", and wrote `partial` where the first pass writes `unsupported`. Of the 34 rows
   rejected for placement, 19 pass R1, 4 are rejected only because the page is the duplicate `shirley` entity
   (who is Anne), and 11 fail under both rules. All-surface rows called supported, t=1..5:
   first pass 33, 27, 33, 30, 26 of 40 (149/200); under R1 35, 33, 36, 38, 30 (172/200); v1 labels
   37, 30, 32, 34, 29 (162/200). Version 2 is below or above version 1 depending on the rule alone.
   **One rule has to be chosen and applied to both sets before any comparison.** R1 is lenient (two rows count
   Davy eating at supper as sharing a school scene with Paul); the human instructions' wording is closer to
   the first pass.
2. **Precision: two cited rows (t=1) are rejected only for the duplicate `shirley` page** ("Rival of" between
   Gilbert and Anne). Same question as the 4 relationship rows: is a true statement on a duplicate page
   unsupported?
3. **Precision: "alive (assumed)" is labelled three ways** inside the first pass (2 supported, 1 uncertain,
   1 unsupported). The factual negatives otherwise read as correct (wrong speaker, homonym merges, stale
   "present" intervals, "Fought in" for an exhibition); the 10 random accepted rows are supported by their
   cited passage; citation verdicts follow wording overlap (0.63 / 0.45 / 0.35 for sufficient / partial /
   insufficient) with no unexplained outlier.
4. **Recall: 3 of the 4 proposed changes hold; the fourth breaks the one-rule-for-all-systems audit.**
   "Mary Vance eats ravenously at the manse" is "no" for all seven systems, including v1, whose page says
   "who fed her at the manse". Net: t=2 34/46, t=5 87/111, mean over cutoffs 0.787 (original 0.783, first
   pass 0.789). The Meredith/Rosemary "yes" rests on an implication and needs the human reader.
   None of the 13 "no" facts searched is stated in the rendered tree (two are interpretive: Paul Irving's
   move is on the stray `irving` page; Leslie's page omits who coerced the marriage).
5. **Packet A does not show the passage a reader needs.** The first pass answers "no" where nothing shown
   establishes the statement, as instructed. For 7 of 12 such items checked, the reader's volumes do support
   the statement and the item shows none of the supporting paragraphs (A031, A038, A041, A044, A085, A098,
   A106; e.g. A106 "Marilla feels an unaccustomed warmth toward Anne", v01:c10:p0050). 24 of the 41 negative
   answers are on items with no citation at all. A human will answer the same way, so the packet measures
   its own paragraph selection. The instructions also leave "no" and "cannot tell" overlapping when nothing
   shown bears on the statement. **Rebuild the packet before a human starts it** (it also still carries
   version-1 items).
6. **Packet B: agreement on 57 of 60.** B021 "implied" contradicts B012 and B052 (same sentence, "neither");
   B005 and B023 read as "implied" to this reader. B009 and B047 show a volume-5 courtship on a volume-4 page
   under the wrong man: "neither" for the assessed fact, but later material on an early page.

## System finding made on the way (OPEN_GAPS G15)

In `data/anne@v2/02b_scenes`, 21 of 226 scenes list Anne as `shirley` and, in 20 of them, not as
`anne-shirley` (v1: 6 scenes, always beside `anne`). Those scenes are missing from Anne's pages and build the
five `shirley` pair pages.

## Addendum, same day: every relationship row read, both versions (CHANGES C39)

The check above read only the lines the first pass rejected. Asked for a precision figure comparable across
versions, this reader then read **every** relationship row of both versions under one written rule
(`docs/eval/precision/pair_strict_claude.json`, `docs/eval/labelling_tools/pairstrict.py`).

- Under the standing rule (the pair shares an episode of the scene summary): version 1 **0.751**, version 2
  **0.784**. Under the stricter line rule: 0.604 and 0.623. Finding 1's 172/200 was the retired lenient rule.
- Agreement with the first pass on version 2: 90/99 rows, kappa 0.79. Nine rows to settle:

| Row (cutoff.index) | Pair page | Statement | Codex | Claude | Passage that decides |
|---|---|---|---|---|---|
| 2.24 | Diana Barry & Shirley | The sun breaks through the clouds during Miss Lavendar's wedding ceremony | no | yes | v02:c30:p0029 (Anne and Diana by the stone bench); "Shirley" is Anne |
| 3.2 | Paul Irving & Shirley | Miss Lavendar and Paul bond over ... dream-people | no | yes | v02:c27:p0051-p0062 |
| 3.20 | Paul Irving & Shirley | Anne returns home | no | yes | v02:c05:p0020 (Paul meets her on the way home) |
| 4.5 | Paul Irving & Shirley | Anne meets her students at the Avonlea school | no | yes | v02:c05:p0007 |
| 4.27 | Anne Shirley & Marilla Cuthbert | Barry drives Diana back home | no | yes | v01:c29:p0016 (breakfast before the trip) |
| 4.39 | Anne Shirley & Matthew Cuthbert | Aunt Josephine is the source of the slippers | no | yes | v01:c25:p0057 (Matthew at the concert) |
| 5.7 | Alec & Alonzo | Anne stays in Bolingbroke with Phil Gordon | no | yes | v03:c21:p0000 ("constantly on hand") |
| 5.33 | Anne Shirley & Davy Keith | Following the broken platter incident, Diana goes home | no | yes | v02:c17:p0060 (Davy at the crash) |
| 5.19 | Diana Barry & Ruby Gillis | Anne exchanges secret notes with Diana at school | yes | no | v01:c17: no paragraph with both |
