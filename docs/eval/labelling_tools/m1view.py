"""m1view.py <series> <t> [k]: each control fact (reveal <= t) with its top-k whole-tree sentences."""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path("")
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "eval"))
import yaml
from m4_tree import sentences, words
from narrativewiki.entities.gazetteer import slugify
s, t = sys.argv[1], int(sys.argv[2]); k = int(sys.argv[3]) if len(sys.argv) > 3 else 3
gold = yaml.safe_load(open(ROOT / "docs/eval/parametric/anne.yaml", encoding="utf-8"))
tree = ROOT / "dist" / s / "wiki" / f"v{t:02d}"
sents = [(p.relative_to(tree).as_posix(), x) for p in sorted(tree.rglob("*.md")) if p.parent.name != "source" for x in sentences(p.read_text(encoding="utf-8"))]
facts = [(c, f["claim"]) for c, e in gold["characters"].items() for f in e["facts"] if int(f["evidence"][1:3]) <= t]
for i, (c, claim) in enumerate(facts):
    nw = {w.lower() for w in c.split()}
    cw = words(claim, nw)
    own = f"character/{slugify(c)}.md"
    has_page = (tree / own).exists()
    scored = []
    for page, x in sents:
        sh = cw & words(x, nw)
        if sh: scored.append((len(sh) / max(1, len(cw)) + (0.25 if page == own else 0), page, x))
    scored.sort(key=lambda z: -z[0])
    print(f"[{i}] {c}{'' if has_page else ' (NO PAGE)'}: {claim}")
    for sc, page, x in scored[:k]:
        print(f"     {sc:.2f} {page[10:34]:24s} {x[:200]}")
