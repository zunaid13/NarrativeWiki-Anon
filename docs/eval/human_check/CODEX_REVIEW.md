# Codex reader draft for human adjudication

All 554 items have provisional AI answers with individual reasons: G 111, C 60,
R 235 and P 148. The reviewer was Codex, an AI assistant working alone in this
session. These answers await human checking; they do not constitute human
verification, a second human reader, or evidence of inter-reader agreement.
Human completion remains 0 of 554. No paper figures or gold facts were changed.

The answer files retain the original CSV headers and item order. Every comment
starts with `AI review (Codex):`:

- [G_answers_codex.csv](G_answers_codex.csv)
- [C_answers_codex.csv](C_answers_codex.csv)
- [R_answers_codex.csv](R_answers_codex.csv)
- [P_answers_codex.csv](P_answers_codex.csv)

[codex_review.json](codex_review.json) records the reasons, per-item recording
times, part start/end times and SHA-256 hashes of the instructions, reader HTML
and blank CSV templates. The original reader materials were unchanged.
The separate [draft zip](../../../dist/narrativewiki-codex-reader-draft.zip)
contains only these four answer CSVs, this note and that provenance JSON.
The original `dist/narrativewiki-human-check.zip` was not opened or modified.

## Required reader disclosures

**Prior reading:** This session read the supplied excerpts, not any of the five
books in full. An AI model's pretraining familiarity cannot be assessed. Project
map/handover documents and aggregate AI measurement results were seen; the
answer key and `docs/eval/agent_annotation/` were never opened. This is not an
independent human annotation.

**Time:** Actual wall-clock intervals, including reading, judgment and recording,
in local time. Initial setup and final packaging are outside these
intervals. These are AI session times, not estimates of human effort.

| Part | Start | End | Elapsed |
|---|---|---|---|
| G | 2026-10-06 23:48:16 | 2026-10-06 23:50:10 | 1 min 54 s |
| C | 2026-10-06 23:50:10 | 2026-10-06 23:51:59 | 1 min 49 s |
| R | 2026-10-06 23:51:59 | 2026-10-06 23:57:37 | 5 min 38 s |
| P | 2026-10-06 23:57:37 | 2026-10-07 00:07:11 | 9 min 34 s |

**Unclear instructions:** Pair pages summarize several beats of a chapter, making
the boundary of "that scene" ambiguous. P106 and P146 received pair `yes` because
both characters act during the described preparation/activity sequence; humans
should adjudicate whether those beats qualify as one shared scene. Other
judgment boundaries include interpreting `since vN` as first evidence rather than
literal onset, and accepting ordinary contextual inference without importing
novel memory. Reasons make the interpretation visible item by item.

## Label inventory and review method

These are counts of this draft's judgments, not measured system accuracy.

| Part | yes | partly | no | not shown | none cited | Total |
|---|---:|---:|---:|---:|---:|---:|
| G | 84 | 11 | 16 | 0 | n-a | 111 |
| C | 25 | 12 | 22 | n-a | 1 | 60 |
| R | 167 | 6 | 62 | n-a | n-a | 235 |
| P | 114 | 22 | 8 | 4 | n-a | 148 |

P also has 14 pair judgments: 6 `yes`, 8 `no`; the remaining 134 are `n-a`.
The elapsed total across parts is 1,135 seconds (18 min 55 s).

Judgments used only text displayed with each item, following G, C, R, P order.
G used its gold paragraph; R used its displayed wiki text; P used cited and
additional displayed paragraphs; C used only its cited paragraphs. Each blue
P statement was judged separately from neighboring claims in the page text.
Prior labels were not compared. No provider API or pipeline run was invoked.

The stdlib helper [human_check_review.py](human_check_review.py)
extracts reader text and records individually supplied decisions; it does not
generate labels. Repeated source blocks within a reading batch were displayed
once with an exact-text reference. Validation checks source hashes, exact CSV
headers/IDs/order, allowed labels, reasons, timezone-aware timestamp ordering,
and pair-column eligibility:

```powershell
python docs/eval/human_check/human_check_review.py verify
python docs/eval/human_check/human_check_review.py summary
```

Counts and times are recorded in `docs/MEASUREMENTS.md` under run id
`codex-reader-draft-20261006`. Human reviewers can inspect and correct these
drafts; readers intended to work independently should receive the original
reader zip without these AI answers.

## Added 2026-10-07 (Claude): checked, and the package changed since

This draft was checked by a second AI reader: [CLAUDE_CHECK.md](CLAUDE_CHECK.md) and
[ai_draft_checked.csv](ai_draft_checked.csv). The reader package was regenerated afterwards (CHANGES C42: every
cited paragraph shown, whole sentences in part R), so the answers above were made on the pages of commit
18c75c7 and `human_check_review.py verify` stops at the hashes of the R, P and C pages. The four
`*_answers_codex.csv` files and `codex_review.json` are as Codex left them.
