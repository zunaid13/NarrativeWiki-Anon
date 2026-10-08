"""pairstrict.py: page-context precision of the main system, versions 1 and 2, under ONE pair rule read by ONE reader.

Run:  .venv/Scripts/python.exe docs/eval/labelling_tools/pairstrict.py
Reads docs/eval/precision/pair_strict_claude.json: two verdicts for every relationship-page row of
docs/eval/precision/{anne,anne@v2}_v<t>_all.jsonl (rules in the file's `_rules`):
  scene  the pair shares at least one episode of the scene summary the line belongs to (the standing rule)
  line   the pair takes part in the episode the sampled statement itself describes (stricter; sensitivity)
A row counts as supported in page context when its factual verdict (`human_atom`) is supported and, on a
relationship page, the pair verdict is "yes"; elsewhere the stored page verdict (`human`) decides. Frame errors
leave the denominator, as in paper_observations.py. Prints per-cutoff counts, the mean over cutoffs, the share of
relationship rows with a "yes", and the agreement of the scene verdict with the stored page verdicts.
"""
import json
from pathlib import Path

EV = Path(__file__).resolve().parents[1]
side = json.loads((EV / "precision" / "pair_strict_claude.json").read_text(encoding="utf-8"))
for series in ("anne", "anne@v2"):
    print(f"== {series}")
    for rule in ("scene", "line"):
        means, cells, agree, n_rel, yes_rel, atom = [], [], [0, 0], 0, 0, []
        for t in range(1, 6):
            rows = [json.loads(l) for l in (EV / "precision" / f"{series}_v{t}_all.jsonl").open(encoding="utf-8")]
            pv = side[series][str(t)]
            ok = den = a = 0
            for i, r in enumerate(rows):
                if r.get("human") == "frame_error":
                    continue
                den += 1
                a += r.get("human_atom") == "supported"
                if str(i) in pv:
                    n_rel += 1
                    yes_rel += pv[str(i)][rule] == "yes"
                    good = r.get("human_atom") == "supported" and pv[str(i)][rule] == "yes"
                    agree[0] += (r.get("human") == "supported") == good
                    agree[1] += 1
                else:
                    good = r.get("human") == "supported"
                ok += good
            means.append(ok / den)
            cells.append(f"{ok}/{den}")
            atom.append(a / den)
        print(f"  {rule:5}: {', '.join(cells)}; mean {sum(means) / 5:.3f}; pair 'yes' on {yes_rel}/{n_rel} relationship rows; "
              f"agrees with the stored page verdict on {agree[0]}/{agree[1]}")
    print(f"  factual verdict alone (human_atom): mean {sum(atom) / 5:.3f}")
