"""pagekinds.py: precision and citation sufficiency of the main system by page kind, and the prefix builds. $0.

Run:  .venv/Scripts/python.exe docs/eval/labelling_tools/pagekinds.py
For `anne` (version 1) and `anne@v2` (version 2), from docs/eval/precision/<series>_v<t>_all.jsonl, the pair
verdicts of docs/eval/precision/pair_strict_claude.json (scene rule) and docs/eval/inventory/<series>_v<t>.jsonl:
  cast   sampled rows on the character pages of the 43 evaluated characters (the surface every system writes):
         supported in page context, and cited paragraphs sufficient
  rel    sampled rows on relationship pages: supported in page context (statement true AND the pair shares an
         episode of the scene), and statement true
  share  the kind's share of the whole assertion inventory, pooled over the five cutoffs
The cast's page names are those of the graph baseline built on the same graph (it writes exactly the cast).
Then the prefix builds `anne@p2` (t=2) and `anne@p3` (t=3) against the full build of the same code (`anne`).
"""
import json
from pathlib import Path

EV = Path(__file__).resolve().parents[1]
ROOT = EV.parents[1]
side = json.loads((EV / "precision" / "pair_strict_claude.json").read_text(encoding="utf-8"))


def rows(series, t):
    return [json.loads(l) for l in (EV / "precision" / f"{series}_v{t}_all.jsonl").open(encoding="utf-8")]


def page_ok(series, t, i, r):
    v = side[series][str(t)].get(str(i))
    if v is None:
        return r.get("human") == "supported"
    return r.get("human_atom") == "supported" and v["scene"] == "yes"


for series, base in (("anne", "anne@b4"), ("anne@v2", "anne@b4-v2")):
    cast = {p.name for p in (ROOT / "dist" / base / "wiki" / "v05" / "character").glob("*.md")}
    c = {k: 0 for k in ("cast_n", "cast_ok", "cast_cite", "cast_citen", "rel_n", "rel_ok", "rel_atom", "all_n")}
    inv = {"cast": 0, "rel": 0, "all": 0}
    for t in range(1, 6):
        for i, r in enumerate(rows(series, t)):
            if r.get("human") == "frame_error":
                continue
            c["all_n"] += 1
            kind, name = r["page"].split("/")[0], r["page"].split("/")[-1]
            if kind == "character" and name in cast:
                c["cast_n"] += 1
                c["cast_ok"] += page_ok(series, t, i, r)
                if r.get("cite"):
                    c["cast_citen"] += 1
                    c["cast_cite"] += r["cite"] == "sufficient"
            elif kind == "relationships":
                c["rel_n"] += 1
                c["rel_ok"] += page_ok(series, t, i, r)
                c["rel_atom"] += r.get("human_atom") == "supported"
        for line in (EV / "inventory" / f"{series}_v{t}.jsonl").open(encoding="utf-8"):
            p = json.loads(line)["page"]
            inv["all"] += 1
            inv["cast"] += p.startswith("character/") and p.split("/")[-1] in cast
            inv["rel"] += p.startswith("relationships/")
    print(f"== {series} ({len(cast)} cast pages)")
    print(f"  cast pages: supported {c['cast_ok']}/{c['cast_n']} = {c['cast_ok'] / c['cast_n']:.3f}; "
          f"cited paragraphs sufficient {c['cast_cite']}/{c['cast_citen']} = {c['cast_cite'] / max(1, c['cast_citen']):.3f}; "
          f"share of inventory {inv['cast'] / inv['all']:.1%}")
    print(f"  relationship pages: supported on the page {c['rel_ok']}/{c['rel_n']} = {c['rel_ok'] / c['rel_n']:.3f}; "
          f"statement true {c['rel_atom']}/{c['rel_n']} = {c['rel_atom'] / c['rel_n']:.3f}; "
          f"share of inventory {inv['rel'] / inv['all']:.1%}")

print("== prefix builds against the full build of the same code (version 1), scene rule")
tot = {"prefix": [0, 0], "full": [0, 0]}
for series, t in (("anne@p2", 2), ("anne@p3", 3)):
    for name, s in (("prefix", series), ("full", "anne")):
        rs = [(i, r) for i, r in enumerate(rows(s, t)) if r.get("human") != "frame_error"]
        ok = sum(page_ok(s, t, i, r) for i, r in rs)
        tot[name][0] += ok
        tot[name][1] += len(rs)
        print(f"  t={t} {name:6} ({s}): {ok}/{len(rs)} = {ok / len(rs):.3f}")
(p, pn), (f, fn) = tot["prefix"], tot["full"]
print(f"  pooled: prefix {p}/{pn} = {p / pn:.3f}, full {f}/{fn} = {f / fn:.3f}, difference {p / pn - f / fn:+.3f}")
