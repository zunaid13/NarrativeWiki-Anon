# Codex first-pass annotations — 2026-10-05

Requested by the maintainer: "you will annotate everything first. a human will verify later".
Reader: **Codex, GPT-6; AI pilot; human verification pending**.
Run: `codex-annotation-20261005`. Completed **955 decisions**: 120 support items, 60 disclosure pairs,
440 precision rows and 335 recall checks. Coverage and observations are recorded in
[`docs/MEASUREMENTS.md`](../../../MEASUREMENTS.md).

## Review files

- [`A_answers_Codex.csv`](A_answers_Codex.csv): every support item, pair-participation check,
  citation-only verdict and reason, in the original answer-template columns.
- [`B_answers_Codex.csv`](B_answers_Codex.csv): every disclosure pair, disclosure strength and reason.
- [`precision_review.csv`](precision_review.csv): every collected precision row with file and
  zero-based row number, page verdict, factual-atom verdict, citation verdict and reason.
  The same AI annotations are written to the corresponding canonical precision JSONL files.
- [`recall_review.csv`](recall_review.csv): every eligible gold fact at each cutoff, original and
  proposed yes/no verdicts, rendered-page witness, reason and difference flag.
  [`recall_audit.jsonl`](recall_audit.jsonl) also records page hashes; [`recall_proposed/`](recall_proposed)
  contains the proposed label dictionaries. **Original recall label files are unchanged.**
- [`validation.json`](validation.json): complete coverage, verdict/cutoff checks and file checks.
- [`manifest.json`](manifest.json): input/output/decision hashes and the previous recall labels.
- [`Codex_first_pass_review.zip`](Codex_first_pass_review.zip): the above exported review files,
  annotated precision samples, manifest, these notes and the A/B packet HTML/instructions.
  It contains no answer key.

The collected precision inputs are the main-system `anne@v2` cited and all-surface samples at
cutoffs 1–5, plus `anne@b4-v2_v1_all.jsonl`. The main-system cutoff-5 all-surface sample and
B4 cutoff-1 sample appeared during this pass and were added before their annotation. The manifest
freezes the input collection; later pipeline outputs and future supplement/decontaminated packages
need a separate pass once ready.

## How judgments were made

All judgments were authored by the agent from local text. `review.py` provides deterministic views,
term-ranked search candidates and decision persistence; it does not classify items. Existing screen
verdicts were preserved as provenance, rather than copied into the decisions. There were no provider
calls, paid pipeline runs, resampling or rebuilds.

Packet A/B follow [`INSTRUCTIONS.md`](../../human/INSTRUCTIONS.md). Only the supplied text for each
blind item was used for that item's verdict. `key.json` was never opened and no keyed scorer was run.
The packet retains its v1 main-system items; blind labels cannot identify and skip those items.
The answer files are outside the human scorer's input folder. These are AI answers and do not satisfy
the study's requirement for independent human readers or permit claims about human agreement.

Precision separates placement/identity from the factual atom. For relationship scene rows, both
members must actively participate in the same episode; presence only in an adjacent scene,
recollection or report is insufficient. Broad multi-phase units and group participation are flagged
in notes for human review. A true atom can therefore have an unsupported page verdict. Citation
sufficiency uses only attached paragraph IDs; eligible uncited paragraphs can establish factual
support but cannot repair a citation verdict. Every attached ID was considered, including B4 IDs
omitted from the sampler's embedded passage subset. `additional_citation_audit.json` records the
check for omitted embedded citations in the main-system samples.

`human` is the page verdict; `human_atom` is factual support. `cite` uses the existing workflow's
`frame_error` value when the page verdict is an atomization/frame error. All precision rows also
have explicit AI `reader`/`cite_reader`, date, pending-human-verification flag, page hash and source
witness IDs. Original atoms, units, evidence, passages and automated screens are preserved.

Recall uses the core-proposition rule in [`anne.provenance.md`](../../recall_hand/anne.provenance.md):
same subject, event/relation/attribute and object; compound propositions require both components,
while incidental qualifiers may be omitted. Every cutoff was read afresh. Quoted source pages do
not establish rendered recall, and facts assigned to a conflated/wrong character do not count.
Engagement/plans do not establish a completed marriage. Notes flag interpretive cases such as
school versus hotel at White Sands, Gilbert's presidency, the omitted Averil surname, Paul's
residence, Charlotta's intended move, Owen's grandparent relation, and implicit acceptance of
Meredith's proposal.

## Verification and reproducibility

Page hashes for the main system were captured **after precision reading and before recall reading**;
B4 cutoff-1 hashes were captured before its precision reading. Validation checks those hashes at
export. Parsed-source hashes were captured at export, not at first reading. Those timings are
explicit in the manifest; they are not a claim that all inputs were snapshotted before all reading.

From the repository root:

```powershell
.venv/Scripts/python.exe docs/eval/agent_annotation/2026-10-05/finalize.py
.venv/Scripts/python.exe docs/eval/agent_annotation/2026-10-05/review.py A unused 0 10 --full
.venv/Scripts/python.exe docs/eval/agent_annotation/2026-10-05/review.py precision anne@v2_v1.jsonl 0 10 --full
.venv/Scripts/python.exe docs/eval/agent_annotation/2026-10-05/review.py source '^v01:c15:p0013' 0 1
```

`finalize.py` validates the frozen inputs and all recorded decisions before writing canonical
precision samples and exports. It does not infer labels or calculate study scores. Decision JSONs
and `revisions.jsonl` retain the authored judgments and provisional changes.

A human can verify the CSV rows against the packet, parsed paragraphs and rendered cutoff pages,
then record their own identity and corrected labels. Keep this pilot bundle separate from any
independent human double-labeling study. Performance estimates remain provisional until that work.
