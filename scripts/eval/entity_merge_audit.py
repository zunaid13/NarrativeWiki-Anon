"""[33] Entity-merge audit: characters the gazetteer may have fused, or split, across people. $0.

Run:  .venv/Scripts/python.exe scripts/eval/entity_merge_audit.py <series> [<series> ...]

Reads `data/<series>/02_entities/gazetteer.json` only (deterministic, no model). Flags, per
CHARACTER entity:
  span     surface forms whose first volumes differ by >= 2 and at least one is a full name
           (Anne: `walter` = "Walter" v1 + "Walter Blythe" v5 -- Anne's father and her son)
  surname  multi-word aliases ending in different surnames ("Diana Barry" + "Di Blythe")
  split    a one-word character whose name is the first word of another character's full name
           (`anne` + `anne-shirley`, `matthew` + `matthew-cuthbert`)
A flag is a candidate for a reader to confirm, not a verdict: "Mrs. Rachel" and "Rachel Lynde" are
one person, and a family name shared by relatives is legitimate. Output:
`docs/eval/entity_merge_audit_<series>.json` (one row per flag) plus a printed summary.
Written after the maintainer's 2026-09-30 note that merges "should've been caught" -- the
gazetteer carried every one of these signals from the start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TITLES = {"mr.", "mrs.", "miss", "aunt", "uncle", "captain", "reverend", "rev.", "dr.", "little",
          "old", "young", "cousin", "mistress", "sir", "lady", "the"}


def words(name: str) -> list[str]:
    return [w for w in name.split() if w.lower() not in TITLES]


def audit(series: str) -> list[dict]:
    gaz = json.loads((ROOT / "data" / series / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    chars = [e for e in gaz.get("entities", gaz) if e.get("type") == "CHARACTER"]
    rows = []
    for e in chars:
        forms = e.get("surface_forms") or [{"text": e["canonical"], "first_vol": e.get("first_vol", 1)}]
        full = [f for f in forms if len(words(f["text"])) >= 2]
        vols = [int(f.get("first_vol") or 1) for f in forms]
        if full and max(vols) - min(vols) >= 2:
            rows.append({"entity": e["entity_id"], "flag": "span",
                         "forms": {f["text"]: f.get("first_vol") for f in forms}})
        surnames = {words(f["text"])[-1] for f in full}
        if len(surnames) >= 2:
            rows.append({"entity": e["entity_id"], "flag": "surname", "surnames": sorted(surnames),
                         "forms": {f["text"]: f.get("first_vol") for f in full}})
    by_first = {}
    for e in chars:
        w = words(e["canonical"])
        if len(w) >= 2:
            by_first.setdefault(w[0], []).append(e["entity_id"])
    for e in chars:
        w = words(e["canonical"])
        if len(w) == 1 and w[0] in by_first:
            rows.append({"entity": e["entity_id"], "flag": "split", "with": by_first[w[0]]})
    return rows


def main() -> int:
    for series in sys.argv[1:]:
        rows = audit(series)
        out = ROOT / "docs" / "eval" / f"entity_merge_audit_{series}.json"
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        n = {k: sum(r["flag"] == k for r in rows) for k in ("span", "surname", "split")}
        print(f"{series}: {n} -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
