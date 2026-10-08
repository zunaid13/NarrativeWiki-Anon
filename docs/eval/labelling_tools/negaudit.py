"""negaudit.py [system filter] [start] [end]: every baseline atom not labelled supported, with the best-matching
paragraphs of the WHOLE text up to its cutoff (IDF-weighted content-word overlap), for a fairness re-read."""
import json, math, re, sys, collections
sys.stdout.reconfigure(encoding="utf-8")
ROOT = ""
S = "scratch/"  # a scratch folder of your own, holding m3_negatives.json (see README.md)
items = json.load(open(S + "m3_negatives.json", encoding="utf-8"))
flt = sys.argv[1] if len(sys.argv) > 1 else ""
a = int(sys.argv[2]) if len(sys.argv) > 2 else 0
b = int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 6
STOP = set("the a an and of to in on at for with by from is was were are be been his her their its that this as it he she they "
           "who whom which had has have not but or after before into over while when also very more most some such than then "
           "there what would could should about being other".split())
tok = lambda x: [w for w in re.findall(r"[a-z']+", x.lower()) if w not in STOP and len(w) > 2]
P = {}
for v in range(1, 6):
    P[v] = [json.loads(l) for l in open(f"{ROOT}data/anne/01_parsed/v0{v}.jsonl", encoding="utf-8")]
df = collections.Counter()
allp = [r for v in P for r in P[v]]
for r in allp:
    r["_w"] = set(tok(r["text"]))
    df.update(r["_w"])
N = len(allp)
idf = lambda w: math.log(N / (1 + df.get(w, 0)))
sel = [x for x in items if flt in x[0]][a:b]
for n, (s, t, i, lab, page, atom, note) in enumerate(sel, a):
    aw = set(tok(atom))
    cand = []
    for v in range(1, t + 1):
        for r in P[v]:
            sh = aw & r["_w"]
            if sh:
                cand.append((sum(idf(w) for w in sh), r["para_id"], r["text"], sh))
    cand.sort(key=lambda z: -z[0])
    print(f"#{n} {s[5:]} t={t} row {i} [{lab}] {page}\n   ATOM: {atom[:230]}\n   NOTE: {note[:230]}")
    for sc, pid, txt, sh in cand[:2]:
        best = max(sh, key=idf)
        m = re.search(re.escape(best), txt, re.I)
        st = max(0, (m.start() if m else 0) - 170)
        print(f"   {pid} [{', '.join(sorted(sh, key=idf, reverse=True)[:5])}]: {txt[st:st + 380]}".replace("\n", " "))
