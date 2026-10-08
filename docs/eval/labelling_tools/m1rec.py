"""m1rec.py <series> <t> '<comma list of fact ids that are YES>' : write recall_hand/<series>_v<t>.json."""
import json, sys
from pathlib import Path
import yaml
ROOT = Path("")
s, t = sys.argv[1], int(sys.argv[2]); yes = {int(x) for x in sys.argv[3].split(",") if x.strip()}
gold = yaml.safe_load(open(ROOT / "docs/eval/parametric/anne.yaml", encoding="utf-8"))
facts = [(c, f["claim"]) for c, e in gold["characters"].items() for f in e["facts"] if int(f["evidence"][1:3]) <= t]
assert max(yes, default=0) < len(facts)
out = {f"C {c} | {claim}": ("yes" if i in yes else "no") for i, (c, claim) in enumerate(facts)}
(ROOT / "docs/eval/recall_hand" / f"{s}_v{t}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(s, t, f"{len(yes)}/{len(facts)}")
