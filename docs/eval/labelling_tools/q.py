"""q.py <series> <t> <spec.txt>: each spec line 'tag ;; regex ;; page-regex(optional)'; prints matching unique sentences."""
import re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path("")
sys.path.insert(0, str(ROOT / "scripts" / "eval")); sys.path.insert(0, str(ROOT / "src"))
from m4_tree import sentences
s, t = sys.argv[1], int(sys.argv[2])
tree = ROOT / "dist" / s / "wiki" / f"v{t:02d}"
S = [(p.stem, x) for p in sorted(tree.rglob("*.md")) if p.parent.name != "source"
     for x in sentences(re.sub(r"\]\([^)]*\)", "]", p.read_text(encoding="utf-8")))]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 3
for line in open(sys.argv[3], encoding="utf-8"):
    if not line.strip(): continue
    parts = [x.strip() for x in line.split(";;")]
    tag, rx, pg = parts[0], parts[1], (parts[2] if len(parts) > 2 and parts[2] else None)
    hits = list(dict.fromkeys(f"{p[:18]}: {x[:260]}" for p, x in S if re.search(rx, x, re.I) and (pg is None or re.search(pg, p))))
    print(f"## {tag} ({len(hits)})")
    for h in hits[:n]: print("    " + h)
