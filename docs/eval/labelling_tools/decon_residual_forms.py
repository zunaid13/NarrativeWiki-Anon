"""decon_residual_forms.py: source names left in the decontaminated Anne text in an inflected or derived form. $0.

`decon_report.json` counts a paragraph as residual when it holds a mapped name exactly; "the Gardners",
"Redmondese" or "Jamesina" are not exact names and pass. This lists every word of the treated text that begins
with a mapped source name of five letters or more that is not itself a stand-in, minus words that only look
like one (NOT_NAMES, read by hand 2026-10-07).
Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/decon_residual_forms.py
"""
import collections
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
GENERIC = set("Captain Reverend Aunt Little Uncle Point Church Mission House Dreams Winds Green Gables White Sands "
              "Rainbow Valley Village Society School College Academy Harbour Harbor Glen Lake Shining Waters Lover "
              "Lane Echo Lodge Place Island Prince Edward Queen Bright River Violet Haunted Birch Path Grove Stone "
              "Three Four Hills Light Master Doctor Bridge Carmody Street".split())
NOT_NAMES = {"Granted", "Bubble", "Irene", "Muriel", "Patterson"}  # an adverb, a spring, and names that are not mapped ones


def entity_map(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == "entity_map":
                return v
            r = entity_map(v)
            if r is not None:
                return r


m = entity_map(yaml.safe_load((ROOT / "config" / "series.anne-decon.yaml").read_text(encoding="utf-8")))
src = {w.strip(".,") for a in m for w in str(a).split() if len(w) >= 5 and w[0].isupper()}
src -= {w.strip(".,") for b in m.values() for w in str(b).split()} | GENERIC
rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, src), key=len, reverse=True)) + r")\w*")
hits, paras, total = collections.Counter(), set(), 0
for v in range(1, 6):
    for line in (ROOT / "data" / "anne-decon" / "01_parsed" / f"v0{v}.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        total += 1
        for mm in rx.finditer(r["text"]):
            if mm.group(0) not in NOT_NAMES:
                hits[mm.group(0)] += 1
                paras.add(r["para_id"])
print(f"{sum(hits.values())} occurrences, {len(hits)} forms, {len(paras)} of {total} paragraphs")
print(", ".join(f"{w} {n}" for w, n in hits.most_common()))
