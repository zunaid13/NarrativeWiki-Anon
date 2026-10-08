"""m8rec.py <series> <t> '<json {row index: "S"|"P"|"I" or [verdict, note]}>': write citation-sufficiency labels.

Every cited, non-frame-error row of the sample must get a verdict; uncited rows get cite = "none".
Fields written: cite (sufficient | partial | insufficient | none), cite_note, cite_reader.
"""
import json, sys
from collections import Counter
ROOT = ""
s, t, lab = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
F = f"{ROOT}docs/eval/precision/{s}_v{t}_all.jsonl"
R = "agent:claude-opus-5-5 (M8 pilot: the cited paragraphs read alone; not human; not blinded)"
M = {"S": "sufficient", "P": "partial", "I": "insufficient"}
rows = [json.loads(l) for l in open(F, encoding="utf-8")]
need = {str(i) for i, r in enumerate(rows) if r.get("evidence") and r.get("human") != "frame_error"}
assert need == set(lab), (sorted(need - set(lab), key=int), sorted(set(lab) - need, key=int))
for i, r in enumerate(rows):
    if str(i) in lab:
        v = lab[str(i)]
        v, note = (v, "") if isinstance(v, str) else v
        r["cite"], r["cite_note"], r["cite_reader"] = M[v], note, R
    elif r.get("human") == "frame_error":
        r["cite"] = "frame_error"
    else:
        r["cite"] = "none"
open(F, "w", encoding="utf-8", newline="\n").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
c = Counter(r["cite"] for r in rows)
print(s, t, dict(c), "end-to-end %d/%d" % (c["sufficient"], len(rows) - c["frame_error"]))
