# Labelling helpers (session tools, kept for the next session)

Small scripts the agent used while hand-labelling the Anne cells on 2026-10-01/02. They were written in a
session scratch directory; several still hold that directory in a constant named `S` (where they read or
write intermediate JSON) -- point it at a scratch folder of your own before use. They read label files and
parsed text only; none calls a model. They live here, not under `scripts/`, so that keeping or editing them
does not touch the frozen tree.

| File | Use |
|---|---|
| `ch.py v02:c26 REGEX` / `ch.py id PARA...` | paragraphs of one chapter matching a pattern; print paragraphs by id |
| `g.py VOL CHAPTER REGEX` | search a volume's text |
| `label.py <sample.jsonl> <labels.json>` | write `human`, `human_atom`, `note`, `reader` into an `_all` precision sample (40 rows; index -> [page label, atom label, note]) |
| `negaudit.py` | every baseline atom not labelled supported, with the best-matching paragraphs of the text up to its cutoff (the C25 audit) |
| `m8view.py`, `m8rec.py`, `m8q.py` | view sampled atoms with their cited paragraphs; record citation-sufficiency verdicts |
| `m4sent.py`, `m4rec.py` | list disclosure candidate sentences per fact; record verdicts into `leak_audit/<series>_tree.jsonl` |
| `m1view.py`, `m1rec.py`, `q.py` | search a wiki tree for a gold fact; record recall verdicts |
| `m3fix.py` | apply corrected precision labels with a note keeping the old label |
| `pview.py <sample.jsonl> [--labels] [--full i,j] [--w N]` | one block per sampled atom: page, value, the screen's verdict and the best-overlapping cited passages; `--labels` lists the labels already written (run from the repository root) |
| `emptyctx.py [--all]` | every baseline run's page calls whose prompt carried no passage (the title-only branch X1 uses by design): the check behind OPEN_GAPS G10, to run on a new matrix before labelling it |
