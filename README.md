# NarrativeWiki

Code, evaluation data and generated wikis for the paper *NarrativeWiki: Evidence-Grounded Character Wikis at
Reader-Selected Narrative Cutoffs* (under anonymous review).

NarrativeWiki reads a novel series (EPUB) and builds a hyperlinked character wiki with one tab per volume:
"I have read up to volume N". A page at cutoff N is generated only from facts whose evidence lies in volumes
1..N, and every line cites the paragraphs it rests on.

## What is here

| Path | Holds |
|---|---|
| `src/narrativewiki/` | the pipeline; `wiki` is its command line |
| `config/` | model routing (`models.yaml`), the extraction taxonomy, one `series.<id>.yaml` per work |
| `scripts/run_series.py` | one work, volumes 1..N, every stage and audit in order |
| `scripts/baselines/` | the comparison systems: `X1` closed book, `B1` long context, `B2` prefix retrieval, `B3` LightRAG, `B4` full graph, `X2` full text |
| `scripts/eval/`, `scripts/papers/` | evaluation tools; table values, statistics and the cutoff figure |
| `USER_GUIDE.md` | every command and the order to run them in |
| `docs/CONTRACTS.md`, `docs/PROMPTS.md` | the format of every file the pipeline writes; the prompts |
| `docs/eval/parametric/`, `docs/eval/roster/` | gold facts with evidence paragraphs and reveal volumes; casts |
| `docs/eval/recall_hand/`, `precision/`, `leak_audit/` | label files: recall, precision samples with notes and citation verdicts, disclosure candidates and verdicts |
| `docs/eval/inventory/` | every assertion each wiki makes, per cutoff |
| `docs/eval/observations/`, `paper_numbers.json` | the labels as one row per observation; the values computed from them |
| `docs/eval/human_check/`, `agent_annotation/` | the human-check package and the second annotator's decisions |
| `docs/eval/labelling_tools/` | the helpers the labels were written with, and `derived_values.py` |
| `docs/eval/cost/`, `freeze/`, `a1/`, `m4_seeded/`, `baseline_tuning/` | per-run cost, freeze records, construction-scope comparison, seeded-disclosure test, baseline settings |
| `docs/MEASUREMENTS.md`, `docs/eval/CHANGES.md` | the ledger (one row per measured value, with its command) and the log of changes that moved a number |
| `docs/paper/results.tex`, `tables/`, `appendix/g-additional-results.tex` | every value the paper prints, each with its ledger row; the result tables |
| `dist/<series>/wiki/` | the generated wiki of every system at every cutoff (Markdown) |
| `data/anne-decon/`, `data/leagues-decon/` | the treated (renamed and paraphrased) text of the decontaminated condition |

Series ids: `anne` is the first build of NarrativeWiki and `anne@v2` the reported one; `anne@x1`, `@b1`, `@b2`,
`@b3`, `@b4` / `@b4-v2`, `@x2` are the comparison systems; `anne-decon@*` are the same systems on the treated text;
`anne@p<t>` / `anne@p<t>-v2` are builds whose entity index saw volumes 1..t only; `anne-m31` / `anne-m36` are the
generator trial; `leagues*` is the first-person sample; Middlemarch is the work the baselines were tuned on.

Not included: the source texts (public domain, fetched below), the model response cache and raw call logs
(2.4 GB; per-run cost summaries are included instead). Labels were produced by LLM annotators and reviewed as
the paper states; the `reader` field of a label file says who wrote each label. Local paths were replaced by
relative ones.

## Setup

Python 3.11 or later.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .[all]   # Linux/macOS: .venv/bin/python
cp .env.example .env                                # then add credentials, all optional
.venv/Scripts/python.exe -m narrativewiki doctor -s anne
```

`wiki doctor` prints what is missing and which model each stage will use. Generative stages follow the
`routing:` block of `config/models.yaml` (Vertex AI first; without credentials they fall back to a free tier
and then to a local Ollama model). The evaluation tools marked `$0` in their docstrings need only the files
here, or a local Ollama model (`ollama pull qwen2.5:14b-instruct-q4_K_M`). `scripts/run_series.py` and
`scripts/baselines/tune.py` start the interpreter at `.venv/Scripts/python.exe` (the `PY` line near the top);
on Linux or macOS change that line to `.venv/bin/python`. `B3` runs in its own environment with
`lightrag-hku==1.5.7`.

## The source texts

```bash
mkdir -p corpus/anne corpus/leagues
n=1; for id in 45 47 51 544 5343 3796; do
  curl -L -o corpus/anne/anne-v0$n.epub https://www.gutenberg.org/ebooks/$id.epub3.images; n=$((n+1)); done
curl -L -o corpus/leagues/twenty-thousand-leagues.epub https://www.gutenberg.org/ebooks/164.epub3.images
```

The paper uses volumes 1-5 of the Anne series. The header of each `config/series.<id>.yaml` names its source.

## Using it

```bash
wiki ingest -s anne --volumes 1-5       # parse the EPUBs ($0)
wiki gazetteer -s anne --volumes 1-5    # discover the cast
python scripts/run_series.py anne --upto 5   # every remaining stage, then the audits
wiki serve -s anne                      # browse the wiki
```

`USER_GUIDE.md` explains each stage, how to check its output, and how to add another series. The wikis the
paper evaluates are already under `dist/`; with the `wiki` extra installed,
`mkdocs serve -f dist/anne@v2/mkdocs.yml` renders one.

## Reproducing the paper's numbers

From the label files, with no model call:

```bash
python docs/eval/labelling_tools/derived_values.py   # recomputes the printed values from the labels; exit 1 on a difference
python scripts/papers/summary_stats.py               # mean, median, SD and range over cutoffs
```

From the source texts (generation is billed by the model provider; responses are not bit-identical to the
ones cached for the paper):

| Step | Command |
|---|---|
| NarrativeWiki, all five cutoffs | `python scripts/run_series.py anne --upto 5` |
| a comparison cell at cutoff t | `python scripts/baselines/run_baseline.py anne {X1,B1,B2,B4,X2} --upto t`, `python scripts/baselines/b3_lightrag.py anne --upto t --mode mix` |
| the treated text | `wiki decontaminate --series anne-decon --volumes 1-5` (or use `data/anne-decon/`) |
| assertion inventory and precision sample of a cell S | `python scripts/eval/assertion_inventory.py S --upto t`, `python scripts/eval/assertion_precision.py S --upto t --n 40 --population all` |
| disclosure candidates | `python scripts/eval/m4_tree.py S --upto t` |
| lexical screen | `python scripts/eval/artifact_exposure.py S` |
| labels to observations | `python scripts/eval/paper_observations.py S --system B5 --work anne`, then `python docs/eval/labelling_tools/apply_pair_rule.py S B5` |
| table values | `python scripts/eval/paper_numbers.py docs/eval/observations/<files>` |
| cost of a cell | `python scripts/eval/run_cost.py S --final` |

Each script's docstring states its inputs, outputs and options.
