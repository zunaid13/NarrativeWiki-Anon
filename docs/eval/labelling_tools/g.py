# usage: g.py VOL CHAPTER_SUBSTR REGEX [maxchars]   |  g.py id PARA_ID...
import json, re, sys
B = "data/anne/01_parsed/"
sys.stdout.reconfigure(encoding="utf-8")
if sys.argv[1] == "id":
    ids = set(sys.argv[2:])
    for v in {i[:3] for i in ids}:
        for l in open(B + v + ".jsonl", encoding="utf-8"):
            r = json.loads(l)
            if r["para_id"] in ids: print(r["para_id"], r["text"], "\n")
    sys.exit()
v, ch, rx = sys.argv[1:4]; n = int(sys.argv[4]) if len(sys.argv) > 4 else 400
for l in open(B + f"v0{v}.jsonl", encoding="utf-8"):
    r = json.loads(l)
    if ch.lower() in r["chapter_title"].lower() and re.search(rx, r["text"], re.I):
        t = r["text"]; m = re.search(rx, t, re.I); s = max(0, m.start() - n // 2)
        print(r["para_id"], "|", t[s:s + n].replace("\n", " "), "\n")
