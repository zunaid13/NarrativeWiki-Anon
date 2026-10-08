# Instructions for the readers

You will check statements about L. M. Montgomery's *Anne* books (books 1–5: *Anne of Green Gables*,
*Anne of Avonlea*, *Anne of the Island*, *Anne's House of Dreams*, *Rainbow Valley*) and about
automatically written wiki pages on them. You do not need to have read the books: each item shows
text to judge from. If you have read them, say so at the end; **judge only from the text shown**,
not from memory.

- Work **alone**. Do not discuss items with another reader until both have finished.
- Do not open `key.json`, and do not open anything under `docs/eval/agent_annotation/`. They hold
  which system wrote each item and what an AI answered.
- There are no trick items and no target number of "yes" answers.
- Four parts, 554 items. Rough time: G 2 h, R 6 h, P 5 h, C 1 h. Any order; take breaks; note your
  start and end time for each part.

## Setup

1. Copy each `X_answers.csv` to `X_answers_<yourname>.csv` (X = G, R, P, C).
2. Open the matching HTML file in a browser and your CSV copy in Excel or LibreOffice. Fill one row
   per item. Save as CSV (UTF-8).

## `not shown`

Parts G and P offer the answer `not shown`. Use it when **none of the text displayed bears on the
item at all**, so that you cannot say yes or no. It is not a "no": such items come back to you
later with more text. If the text does bear on the item and contradicts it or fails to establish
it, answer `no`.

## Part G — is the gold fact established by its paragraph? (111 items)

Each item shows a fact, the volume it is dated to, and the paragraph it is dated by with the
paragraph before and after.

| Answer | When |
|---|---|
| `yes` | The paragraphs establish the fact as worded. |
| `partly` | The main point is there but a stated detail is not (a number, a place, "at Christmas"). |
| `no` | The paragraphs say something different, or it is said of another person. |
| `not shown` | The paragraphs are about something else. |

## Part R — does the wiki convey the fact? (235 items)

Each item shows a fact and how far the reader has read, then the sentences of the wiki written for
that reader that are nearest to the fact, each with the page it stands on.

| Answer | When |
|---|---|
| `yes` | The sentences shown state the fact's core: the same person, the same event, relation or attribute, the same object, in any wording. A missing incidental detail (a date, a colour, "four years ago") is still `yes`. |
| `partly` | The fact joins two things (*absent-minded **but** a wonderful preacher*) and only one is stated. |
| `no` | None of the sentences states it. A plan or an engagement does not convey a completed journey or marriage. A sentence on another character's page that is really about someone else does not count. |

## Part P — is the statement supported? (148 items)

Each item shows how far the reader has read; which page the statement stands on; **the statement**
(blue box); the page text it was taken from; the paragraphs the page cites for it; and other
paragraphs from volumes 1..*t* that may bear on it.

**Column 1, supported?** Judge the blue statement against all paragraphs shown.

| Answer | When |
|---|---|
| `yes` | The paragraphs establish every part of the statement, said of the right person, with the right force (a belief stays a belief, a rumour a rumour). A faithful paraphrase is `yes`. |
| `partly` | The main point is there but a stated detail is not, or a passing state is presented as a lasting trait. |
| `no` | The paragraphs say something different, or say it of another person. |
| `not shown` | Nothing displayed bears on the statement. |

**Column 2, pair pages only.** If the page line begins "Relationship (pair) page: A & B", the
statement is listed as a *shared scene of A and B*. Answer `yes` if the paragraphs show **both** A
and B taking part in that scene (present and acting or speaking), `no` if one of them is only
mentioned, reported or absent. Otherwise write `n-a`.

## Part C — are the cited paragraphs alone enough? (60 items)

Each item shows a statement and only the paragraphs its page cites for it.

| Answer | When |
|---|---|
| `yes` | Those paragraphs by themselves establish the whole statement. |
| `partly` | They give the main point but not every stated detail. |
| `no` | They do not establish it. |
| `none cited` | The item shows "none cited". |

## When you finish

Send back your four CSV files and answer in one line each: (1) Had you read any of the five books?
Which? (2) Start and end time for each part. (3) Anything unclear in these instructions.

## For the maintainer (not for the readers)

- Give readers this folder **without** `key.json` and `make_check.py`
  (`dist/narrativewiki-human-check.zip` is that folder).
- The allocation is the one fixed on 2026-10-04: gold facts in full, a uniform 10% of recall (235 of
  2,345), precision (148 of 1,480) and citation sufficiency (60 of 600), drawn per system.
  Disclosure is not human-checked.
- The AI labels the answers will be compared with are AI pilots (Claude Opus 5.5 and Codex GPT-6).
  For the retrieval-baseline built from the version-2 graph they may not exist yet when a reader
  starts; the reader's items do not depend on them.
- `not shown` items and `yes`/`no` disagreements with the AI label go to a second round in which the
  item is shown again with the AI reader's witness text; record both rounds.
- Record for the paper: who the readers were (role, not name), whether paid and how much, time
  taken, whether they knew the books, and that they consented to their labels being released.
