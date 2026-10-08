"""m4rec.py <series> <t> '<json {fact_id: [verdict, witness]}>' : write R6 verdicts for one cell."""
import json, sys, hashlib
from pathlib import Path
ROOT = Path("")
s, t, over = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3])
cand = [json.loads(l) for l in open(ROOT / f"docs/eval/leak_audit/{s}_tree_candidates_v{t}.jsonl", encoding="utf-8")]
tree = ROOT / "dist" / s / "wiki" / f"v{t:02d}"
sha = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(tree.rglob("*.md")) if p.parent.name != "source")).hexdigest()[:16]
M = ("R6 whole-tree pilot: scripts/eval/m4_tree.py candidates (future-only keywords, name + half the claim's "
     "content words, name + marriage/death/birth words) over every page of v<t>, each unique sentence read by hand; "
     "candidate recall of the retrieval is not measured")
out = ROOT / f"docs/eval/leak_audit/{s}_tree.jsonl"
rows = [json.loads(l) for l in open(out, encoding="utf-8")] if out.exists() else []
rows = [r for r in rows if r["t"] != t]
for i, c in enumerate(cand):
    v, w = over.get(str(i), ["absent", ""])
    rows.append({"t": t, "character": c["character"], "claim": c["claim"], "reveal": c["reveal"], "tree_sha": sha,
                 "verdict": v, "witness": w, "reader": "agent:claude-opus-5-5 (not human; not blinded)", "method": M})
rows.sort(key=lambda r: r["t"])
out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
from collections import Counter
print(s, t, Counter(r["verdict"] for r in rows if r["t"] == t))
