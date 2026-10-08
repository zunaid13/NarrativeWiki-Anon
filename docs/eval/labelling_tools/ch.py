"""ch.py v02:c26 REGEX [maxhits] [chars]: paragraphs of one chapter matching REGEX (case-insensitive), with ids.
   ch.py id PARA_ID... : print paragraphs."""
import json, re, sys
B = "data/anne/01_parsed/"
sys.stdout.reconfigure(encoding="utf-8")
if sys.argv[1] == "id":
    for pid in sys.argv[2:]:
        for l in open(B + pid[:3] + ".jsonl", encoding="utf-8"):
            r = json.loads(l)
            if r["para_id"] == pid:
                print(pid, r["text"][:900], "\n")
    sys.exit()
vc, rx = sys.argv[1], re.compile(sys.argv[2], re.I)
n = int(sys.argv[3]) if len(sys.argv) > 3 else 4
w = int(sys.argv[4]) if len(sys.argv) > 4 else 260
hits = [json.loads(l) for l in open(B + vc[:3] + ".jsonl", encoding="utf-8")]
hits = [r for r in hits if r["para_id"].startswith(vc) and rx.search(r["text"])]
print(f"== {vc} /{sys.argv[2]}/ : {len(hits)} paragraphs")
for r in hits[:n]:
    m = rx.search(r["text"]); a = max(0, m.start() - w // 2)
    print("  ", r["para_id"], "|", r["text"][a:a + w].replace("\n", " "))
