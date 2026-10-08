"""label.py <precision file> <labels.json>: write human/human_atom/note/reader into an _all sample."""
import json, sys
F, L = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
R = "agent:claude-opus-5-5 (R1-R3 pilot; source text read; not human; not blinded)"
rows = [json.loads(l) for l in open(F, encoding="utf-8")]
assert len(rows) == len(L) == 40, (len(rows), len(L))
for i, r in enumerate(rows):
    assert r.get("human") in (None, ""), i
    page, atom, note = L[str(i)]
    r["human"], r["human_atom"], r["note"], r["reader"] = page, atom, note, R
open(F, "w", encoding="utf-8", newline="\n").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
from collections import Counter; print(Counter(r["human"] for r in rows), Counter(r["human_atom"] for r in rows))
