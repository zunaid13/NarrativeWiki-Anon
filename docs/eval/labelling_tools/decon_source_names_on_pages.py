"""decon_source_names_on_pages.py [--sentences]: source names that reach the pages of the treated cells. $0, no model.

Two questions about `dist/anne-decon@*/wiki/v0*/character/*.md` (citations stripped):
  1. which MAPPED source names (keys of the series' entity map, five letters or more, that are not themselves
     stand-ins) are printed on a page, and whether the treated text holds that name at all
     (`data/anne-decon/01_parsed`): a name on a page that the text does not hold was supplied by the generator;
  2. the same for a short list of source names the map replaces inside longer forms (EXTRA).
Complements `decon_residual_forms.py`, which counts source names left in the treated TEXT.
Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/decon_source_names_on_pages.py
"""
import collections
import glob
import json
import re
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
EXTRA = ["Prince Edward Island", "Prince Edward", "Gardner", "Jamesina", "Redmond", "Avonlea", "Green Gables"]
cfg = yaml.safe_load((ROOT / "config" / "series.anne-decon.yaml").read_text(encoding="utf-8"))
emap = (cfg.get("decontaminate") or {}).get("entity_map") or {}
stand_ins = set(emap.values())
names = sorted({k for k in emap if len(k) >= 5 and k not in stand_ins} | set(EXTRA), key=len, reverse=True)
rx = re.compile(r"\b(" + "|".join(re.escape(k) for k in names) + r")\b")
text = " ".join(json.loads(l)["text"] + " " + (json.loads(l).get("chapter_title") or "")
                for v in range(1, 6) for l in (ROOT / "data" / "anne-decon" / "01_parsed" / f"v0{v}.jsonl").open(encoding="utf-8"))
in_text = collections.Counter(m.group(1) for m in rx.finditer(text))
pages = collections.defaultdict(lambda: collections.defaultdict(set))  # name -> cell -> pages
sents = collections.defaultdict(list)
for f in sorted(glob.glob(str(ROOT / "dist" / "anne-decon@*" / "wiki" / "v0*" / "character" / "*.md"))):
    p = Path(f)
    cell = f"{p.parts[-5].split('@')[1]} t={int(p.parts[-3][1:])}"
    body = re.sub(r"<sub>.*?</sub>", "", p.read_text(encoding="utf-8"))
    body = "\n".join(l for l in body.split("\n") if not l.startswith("_Sources"))
    for m in rx.finditer(body):
        pages[m.group(1)][cell].add(p.stem)
        s = body[max(0, body.rfind("\n", 0, m.start()), body.rfind(". ", 0, m.start())):m.end() + 120].strip()
        sents[m.group(1)].append(f"{cell} {p.stem}: {s[:260]}")
print(f"{len(names)} source names tested on {len(glob.glob(str(ROOT / 'dist' / 'anne-decon@*' / 'wiki' / 'v0*' / 'character' / '*.md')))} pages")
for name in sorted(pages, key=lambda n: (in_text[n] > 0, -sum(len(v) for v in pages[n].values()))):
    cells = pages[name]
    print(f"{name!r}: in the treated text {in_text[name]} times; on {sum(len(v) for v in cells.values())} pages: "
          + "; ".join(f"{c} ({len(v)})" for c, v in sorted(cells.items())))
    if "--sentences" in sys.argv:
        for s in sents[name][:8]:
            print("     " + s.replace("\n", " "))
