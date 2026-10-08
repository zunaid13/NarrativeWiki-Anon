# Check of the Codex reader draft — 2026-10-07

Reader: **Claude (Opus 5.5); AI; not human.** Asked by the maintainer to check the Codex draft
([CODEX_REVIEW.md](CODEX_REVIEW.md)) before a human reviews it. Human completion is still 0 of 554.
Nothing in the paper, the gold file or the earlier AI labels was changed. The Codex files are untouched.

**Start here:** [ai_draft_checked.csv](ai_draft_checked.csv), one row per item: Codex's label, mine where it
differs, the label the AI draft now carries, a status and a one-line reason. Read the 60 rows whose status is
`changed`, `rejudged` or `boundary` first.

**Not blind.** I wrote part of the earlier AI labels, and I joined the draft to `key.json` by script to find
disagreements (labels only; the system of an item was never printed). A reader who is meant to work
independently should get `dist/narrativewiki-human-check.zip` and none of the AI answers.

## Two faults in the reader package, fixed (CHANGES C42)

The draft was faithful to what each item showed. Two items did not show what the question needs.

1. **Parts C and P listed only the first six cited paragraphs.** A History sentence cites up to 22. 24 of 60
   C items and 33 of 148 P items were cut. Codex answered `no` on 14 of the 24 C items; on the full list 8 of
   those are `yes` and 2 are `partly`. Now every cited paragraph is shown.
2. **Part R broke sentences after "Mr.", "Mrs.", "Dr.", "St."** 132 of 235 items showed a cut sentence
   ("Jane eventually met and married Mr."). Now whole sentences are shown.

The unpatched generator reproduced the old package byte for byte before the change; item numbers and sampled
items are the same after it; part G is unchanged. `make_check.py` was rerun and the reader zip rebuilt.
`docs/eval/human_check/human_check_review.py verify` (moved from `scripts/eval/` on 2026-10-07) now stops at the hash of the changed pages: Codex's draft was made
on the package of commit 18c75c7.

## What was read

| Part | Items | Read against the text | Not re-read |
|---|---:|---|---|
| G | 111 | all 111 | none |
| R | 235 | 37: every disagreement with the earlier AI label, each traced in its wiki; 30 more screened for a lost sentence | 198 |
| C | 60 | 33: the 24 corrected items on every cited paragraph, 9 other disagreements | 27 |
| P | 148 | 19: the hard disagreements and the corrected items Codex had not accepted; 14 more by Codex's reason only | 115 |

Part P was read on the cited paragraphs and the passages a decision turns on, not on every extra paragraph of
an item. 340 rows carry Codex's label unchecked by me (`not re-read`, or `stands` where the corrected text can only add
support to a `yes`).

## Result

| Status | G | R | P | C | All | Meaning |
|---|---:|---:|---:|---:|---:|---|
| agree | 98 | 12 | 9 | 21 | 140 | read; Codex's label fits the text |
| rejudged | 0 | 4 | 5 | 10 | 19 | the item text was corrected; the label is mine |
| changed | 1 | 3 | 2 | 0 | 6 | read; Codex's label does not fit the text shown |
| boundary | 12 | 18 | 3 | 2 | 35 | both labels defensible; the instructions do not decide. Codex's label kept, mine beside it |

Part G: all 84 `yes` and all 11 `partly` hold. Of the 16 `no`, 3 hold, 1 is `yes` (G093) and 12 are the first
question below. Among the 200 rows read, the 6 `changed` rows are the only ones where Codex's label does not fit the text it
was shown.

Agreement of the draft with the earlier AI labels, exact label: R 199/235, P 115/148, C 39/60; after this check
204, 120 and 44.

## Three questions the instructions leave open

The human reviewer or the maintainer has to settle these; they move 22 rows.

1. **G: a paragraph that announces or plans the event** (12 items: "the morning of her wedding day", "is going
   to Redmond"). Codex: `no`, following part R's rule that a plan is not a completed event. I read `partly`.
   Either way, 12 gold facts are dated by a paragraph that announces the event and does not show it.
2. **R: a weaker statement of the same thing** (9 items: fascinated by insects / collects them; fought him /
   hates him; criticised her looks / said her hair was red as carrots). Codex: `no`. The earlier AI labels: `yes`.
3. **P: "alive (assumed)"** (P005), which the earlier AI labels treated three ways.

## Still true of the package

- **Part R shows 8 to 11 sentences picked by shared words.** For at least 8 items the sentence that conveys
  the fact is on the page and not in the item (R002, R058, R079, R094, R096, R166, R168, R178). Codex's `no`
  is right on the text shown; these need the second round the instructions describe. R096 lost its sentence
  through the sentence fix (the ranking moved).
- **Part P shows dialogue without its speaker** (P011: the cited whipping lines are Jane's in the book; no
  paragraph shown says so).

## Earlier AI labels that look wrong (for their owner; not changed here)

R189 (`no`; the wiki says Anne accidentally sold Mr. Harrison's Jersey cow), R195 (`no`; red braids and
gray-green eyes are stated), R024 and R226 (`yes`; no sentence has Marilla urging), C008 and C012
(`insufficient`; the cited paragraphs give the proposal and marriage, and "Do your duty by God").
