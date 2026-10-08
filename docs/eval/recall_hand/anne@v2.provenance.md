# Anne v2 recall audit provenance

The canonical `anne@v2_v{1..5}.json` files retain the original Claude agent-pilot labels on the
post-C35 rendered pages, as described in `docs/MEASUREMENTS.md`. They have not been replaced by
the Codex pass below and are not human labels.

On 2026-10-05, at the maintainer's request, Codex (GPT-6) independently re-read every eligible fact
at each cutoff using the rendered `dist/anne@v2/wiki/v0<t>` tree. Source/index pages were excluded
as recall evidence; quoted source paragraphs were stripped from the rendered-page searches.
The uniform core-proposition rule is in [`anne.provenance.md`](anne.provenance.md).

The proposed labels, original labels, per-fact reasons, witnesses and page hashes are in
[`agent_annotation/2026-10-05/`](../agent_annotation/2026-10-05/README.md), under run
`codex-annotation-20261005`. Every cutoff was checked afresh rather than carrying earlier yes/no
labels forward. Original file hashes and labels are preserved in that pass's manifest.

**Human verification is pending.** Interpretive agreements and disagreements remain visible in
the audit CSV. The canonical files remain unchanged so later reviewers can compare the original
and proposed decisions before adopting corrections. No new recall score or human agreement
estimate was substituted into the paper.
