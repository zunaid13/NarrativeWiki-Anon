"""m8view_any.py <series> <t> [k=3] [chars=520] [rows i,j]: cited sampled atoms with their cited paragraphs. $0.

As m8view.py, for any series: the text is read from data/<series before @>/01_parsed, so a decontaminated cell
is shown in its own text. A timeline row carries `evidence: []` in the inventory (OPEN_GAPS G16); its citations
are read here from the delivered page (the "_Sources:" line under the scene) and printed as TIMELINE-CITED, to be
recorded in docs/eval/precision/timeline_cite_claude.json under the series (CHANGES C40).
Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/m8view_any.py anne-decon@v2 1
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
s, t = sys.argv[1], int(sys.argv[2])
k = int(sys.argv[3]) if len(sys.argv) > 3 else 3
n = int(sys.argv[4]) if len(sys.argv) > 4 else 520
only = set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None
STOP = set("the a an and of to in on at for with by from is was were are be been his her their its that this as it he she "
           "they who whom which had has have not but or after before into over while when also".split())
ANCHOR = re.compile(r"#nw-v(\d+)-c(\d+)-p(\d+)")


def words(x):
    return {w for w in re.findall(r"[a-z']+", x.lower()) if w not in STOP and len(w) > 2}


def plain(x):
    return re.sub(r"\s+", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", x).replace("*", "")).strip()


text = {}
for v in range(1, t + 1):
    for line in (ROOT / "data" / s.split("@")[0] / "01_parsed" / f"v{v:02d}.jsonl").open(encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            text[r["para_id"]] = r["text"]


def timeline_cites(r):
    page = ROOT / "dist" / s / "wiki" / f"v{t:02d}" / r["page"]
    lines = page.read_text(encoding="utf-8").splitlines()
    key = plain(r.get("unit") or r["value"])[:60]
    for i, line in enumerate(lines):
        if key and key in plain(line):
            for nxt in lines[i:i + 4]:
                ids = [f"v{int(a):02d}:c{int(b):02d}:p{int(c):04d}" for a, b, c in ANCHOR.findall(nxt)]
                if ids and "ource" in nxt:
                    return ids
    return []


for i, line in enumerate((ROOT / "docs/eval/precision" / f"{s}_v{t}_all.jsonl").open(encoding="utf-8")):
    r = json.loads(line)
    if only and str(i) not in only:
        continue
    ev, tag = list(r.get("evidence") or []), "CITED"
    if not ev and r["page"].startswith("timeline/"):
        ev, tag = timeline_cites(r), "TIMELINE-CITED"
    if not ev:
        continue
    aw = words(r["value"])
    ranked = sorted(((pid, text.get(pid, "(not in volumes <= t)")) for pid in ev), key=lambda kv: -len(aw & words(kv[1])))
    print(f"\n[{i}] {r['page'].split('/')[-1]} | {r['surface']} | human={r.get('human')}/{r.get('human_atom')} | {tag} {len(ev)}")
    print(f"   ATOM: {r['value']}")
    for pid, p in ranked[:k]:
        hits = [m.start() for m in re.finditer(r"[A-Za-z']+", p) if m.group(0).lower() in aw]
        a = max(0, (hits[len(hits) // 2] if hits else 0) - n // 2)
        print(f"   <{pid}> {'...' if a else ''}{p[a:a + n]}{'...' if a + n < len(p) else ''}")
    if len(ranked) > k:
        print("   also cited: " + " ".join(pid for pid, _ in ranked[k:]))
