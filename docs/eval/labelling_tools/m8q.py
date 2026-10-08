"""m8q.py <series> <t> <spec file>: lines 'row ;; regex'; prints every cited paragraph of that row matching the regex."""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = ""
s, t = sys.argv[1], int(sys.argv[2])
T = {}
for v in range(1, t + 1):
    for l in open(f"{ROOT}data/anne/01_parsed/v0{v}.jsonl", encoding="utf-8"):
        r = json.loads(l); T[r["para_id"]] = r["text"]
rows = [json.loads(l) for l in open(f"{ROOT}docs/eval/precision/{s}_v{t}_all.jsonl", encoding="utf-8")]
for line in open(sys.argv[3], encoding="utf-8"):
    if not line.strip(): continue
    i, rx = [x.strip() for x in line.split(";;", 1)]
    r = rows[int(i)]
    hits = []
    for pid in r["evidence"]:
        m = re.search(rx, T.get(pid, ""), re.I)
        if m:
            a = max(0, m.start() - 130); hits.append(f"      {pid}: {T[pid][a:a + 300]}".replace("\n", " "))
    print(f"[{i}] {r['value'][:110]} | {len(r['evidence'])} cited | {len(hits)} match /{rx}/")
    for h in hits[:2]: print(h)
