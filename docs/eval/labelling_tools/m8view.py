"""m8view.py <series> <t> [k=2] [chars=420]: cited sampled atoms with their k best-overlapping cited passages."""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = ""
s, t = sys.argv[1], sys.argv[2]
k = int(sys.argv[3]) if len(sys.argv) > 3 else 2
n = int(sys.argv[4]) if len(sys.argv) > 4 else 420
STOP = set("the a an and of to in on at for with by from is was were are be been his her their its that this as it he she they "
           "who whom which had has have not but or after before into over while when also".split())
W = lambda x: {w for w in re.findall(r"[a-z']+", x.lower()) if w not in STOP and len(w) > 2}
TEXT = {}
for v in range(1, int(t) + 1):  # every paragraph up to the cutoff: the sample stores only the first six cited texts
    for l in open(f"{ROOT}data/anne/01_parsed/v0{v}.jsonl", encoding="utf-8"):
        r = json.loads(l); TEXT[r["para_id"]] = r["text"]
ONLY = set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None
for i, l in enumerate(open(f"{ROOT}docs/eval/precision/{s}_v{t}_all.jsonl", encoding="utf-8")):
    r = json.loads(l)
    if not r.get("evidence"):
        continue
    sc = r.get("screen") or {}
    aw = W(r["value"] + " " + (r.get("label") or ""))
    if ONLY and str(i) not in ONLY: continue
    ps = {pid: TEXT.get(pid, "(not in volumes <= t)") for pid in r["evidence"]}
    ranked = sorted(ps.items(), key=lambda kv: -len(aw & W(kv[1] or "")))
    print(f"[{i}] {r['page'].split('/')[-1]} | {r['surface']} | {r['section']}/{r.get('label','')} | human={r.get('human')} scr={sc.get('supported')} | {len(r['evidence'])} cited, {len(ps)} texts")
    print(f"    ATOM: {r['value'][:300]}")
    for pid, txt in ranked[:k]:
        txt = txt or "(missing)"
        # window around the densest overlap
        best, pos = 0, 0
        for m in re.finditer(r"[A-Za-z']+", txt):
            if m.group(0).lower() in aw:
                seg = txt[max(0, m.start() - n // 2): m.start() + n // 2]
                sc2 = len(aw & W(seg))
                if sc2 > best: best, pos = sc2, max(0, m.start() - n // 2)
        print(f"    {pid} ({len(aw & W(txt))}/{len(aw)}): {txt[pos:pos + n].strip()}")
