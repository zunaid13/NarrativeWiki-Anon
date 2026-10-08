"""[33] A1 construction scope: which character pages exist in one build of a work and not the other. $0.

Run:  .venv/Scripts/python.exe scripts/eval/a1_page_diff.py anne anne@p2 --upto 2
      (full-built series, prefix-built series, the cutoff both are compared at)

Writes docs/eval/a1/<full>_vs_<prefix>_v<t>.jsonl, one row per page that only one build has:
  side            "full" | "prefix"
  slug, canonical, aliases, words
  mentions        that build's mentions of the entity per volume (02_entities/mentions.jsonl)
  later_forms     surface forms of the entity first seen AFTER the cutoff (full side: the sign that a
                  later volume anchors the entity the page stands for)
  counterpart     pages of the OTHER build that share a name word with this one
  class, note     filled by a reader, kept across re-runs:
                    rename        same person, one page on each side, different address
                    duplicate     a second page for a person who already has one
                    selection     a minor entity only this build gives a page
                    later_anchor  the entity is a later-volume character; at this cutoff its page shows
                                  an earlier namesake (premature identity link)
                    revelation    the page states something no text up to the cutoff supports
Prints the count per class. The script lists; it does not judge.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "eval" / "a1"
STOP = {"Miss", "Mrs.", "Mr.", "Aunt", "Uncle", "Old", "the", "of"}


def pages(series: str, t: int) -> dict[str, dict]:
    out = {}
    for f in sorted((ROOT / "dist" / series / "wiki" / f"v{t:02d}" / "character").glob("*.md")):
        if f.stem == "index":
            continue
        raw = f.read_text(encoding="utf-8")
        fm = yaml.safe_load(raw.split("---")[1])
        names = [fm["canonical"], *(fm.get("aliases") or [])]
        out[f.stem] = {"canonical": fm["canonical"], "aliases": fm.get("aliases") or [], "words": len(raw.split()),
                       "tokens": {w for n in names for w in re.split(r"\s+", n) if len(w) > 2 and w not in STOP}}
    return out


def entities(series: str, t: int) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    base = ROOT / "data" / series / "02_entities"
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for line in (base / "mentions.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        counts[r["entity_id"]][r["vol"]] += 1
    last = max((v for c in counts.values() for v in c), default=t)
    later = {e["entity_id"]: [f"{s['text']} (v{s['first_vol']})" for s in e["surface_forms"] if s["first_vol"] > t]
             for e in json.loads((base / "gazetteer.json").read_text(encoding="utf-8"))["entities"]}
    return {e: [c.get(v, 0) for v in range(1, last + 1)] for e, c in counts.items()}, later


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("full")
    ap.add_argument("prefix")
    ap.add_argument("--upto", type=int, required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    built = {"full": (a.full, pages(a.full, a.upto)), "prefix": (a.prefix, pages(a.prefix, a.upto))}
    path = OUT / f"{a.full}_vs_{a.prefix}_v{a.upto}.jsonl"
    kept = {}
    if path.is_file():
        kept = {(r["side"], r["slug"]): r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    rows = []
    for side, other in (("full", "prefix"), ("prefix", "full")):
        series, mine = built[side]
        theirs = built[other][1]
        mentions, later = entities(series, a.upto)
        for slug in sorted(set(mine) - set(theirs)):
            p, old = mine[slug], kept.get((side, slug), {})
            rows.append({"side": side, "slug": slug, "canonical": p["canonical"], "aliases": p["aliases"],
                         "words": p["words"], "mentions": mentions.get(slug, []), "later_forms": later.get(slug, []),
                         "counterpart": sorted(q for q, o in theirs.items() if p["tokens"] & o["tokens"]),
                         "class": old.get("class"), "note": old.get("note", ""), "reader": old.get("reader")})
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    both = len(set(built["full"][1]) & set(built["prefix"][1]))
    print(f"t={a.upto}: {a.full} {len(built['full'][1])} character pages, {a.prefix} {len(built['prefix'][1])}, "
          f"{both} in both; one-sided {len(rows)} -> {path.relative_to(ROOT)}")
    for side in ("full", "prefix"):
        c = collections.Counter(r["class"] or "unlabelled" for r in rows if r["side"] == side)
        print(f"  only {side}: {sum(c.values())}  " + ", ".join(f"{k} {n}" for k, n in sorted(c.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
