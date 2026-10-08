"""[34] Identity dependence: how much of the wiki's cast at cutoff t depends on volumes after t. $0.

Run:  .venv/Scripts/python.exe scripts/eval/identity_dependence.py anne@v2 [--upto 4]

Compares the full build's entity index (data/<series>/02_entities/_premerge/gazetteer.json, built
from every volume and filtered to t) with the index built from volumes 1..t alone
(data/<series>/@t<NN>/02_entities/gazetteer.json, `wiki gazetteer --gate build`,
docs/eval/runners/prefix_gazetteer.sh). An entity's identity at t is its set of surface forms first
seen by t. Each full-build CHARACTER visible at t is one of:
  same       a prefix-built character has exactly that set of forms
  alias      it corresponds one-to-one to a prefix-built character, but a form is attached in one
             build only: the same person, with a name the other build does not link
  regrouped  the correspondence is not one-to-one: forms one build gives one character the other
             gives two (a merge or a split; who is who changes)
  retyped    its forms belong only to prefix-built entities that are not characters
  absent     no prefix-built entity carries any of its forms: it exists because of later text
Identity dependence at t = 1 - same / all; `identity` is the stricter share that are regrouped,
retyped or absent (an alias difference keeps the person). Both are reported over all visible
characters and over those with a published page at t (dist/<series>/wiki/v<NN>/character/<id>.md). `renamed` counts the
`same` entities whose canonical name differs (the page's title and address would change).
`prefix_only` are prefix-built characters none of whose forms a visible full-build entity carries.
`mentions` counts character mentions in volumes 1..t under each index (mentions.jsonl): what a
wiki built on the prefix index would have to work from, against the full build.

Both indexes come from one model; where a prefix changes a prompt, model variance is in the
difference too. The measure bounds the dependence from above, it does not isolate it.
Writes docs/eval/identity_dependence_<series>.json; one row per cutoff, with the entities per class.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ("same", "alias", "regrouped", "retyped", "absent")


def _entities(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["entities"]


def _forms(entity: dict, upto: int) -> frozenset[str]:
    return frozenset(f["text"].lower() for f in entity["surface_forms"] if f["first_vol"] <= upto)


def _mentions(path: Path, entities: list[dict], t: int) -> int:
    chars = {e["entity_id"] for e in entities if e["type"] == "CHARACTER"}
    with path.open(encoding="utf-8") as fh:
        return sum(m["vol"] <= t and m["entity_id"] in chars for m in map(json.loads, fh))


def measure(series: str, t: int) -> dict:
    data = ROOT / "data" / series
    full = [e for e in _entities(data / "02_entities" / "_premerge" / "gazetteer.json") if e["first_vol"] <= t]
    prefix = _entities(data / f"@t{t:02d}" / "02_entities" / "gazetteer.json")
    owner: dict[str, list[dict]] = {}  # form -> prefix-built entities carrying it
    for e in prefix:
        for form in _forms(e, t):
            owner.setdefault(form, []).append(e)
    full_owner: dict[str, set[str]] = {}  # form -> full-build characters carrying it
    for e in full:
        if e["type"] == "CHARACTER":
            for form in _forms(e, t):
                full_owner.setdefault(form, set()).add(e["entity_id"])
    rows = []
    for e in full:
        if e["type"] != "CHARACTER":
            continue
        forms = _forms(e, t)
        hits = {p["entity_id"]: p for form in forms for p in owner.get(form, [])}
        chars = [p for p in hits.values() if p["type"] == "CHARACTER"]
        twin = next((p for p in chars if _forms(p, t) == forms), None)
        one_to_one = len(chars) == 1 and {i for form in _forms(chars[0], t) for i in full_owner.get(form, ())} == {e["entity_id"]}
        cls = "same" if twin else "alias" if one_to_one else "regrouped" if chars else "retyped" if hits else "absent"
        rows.append({
            "entity_id": e["entity_id"], "canonical": e["canonical"], "class": cls,
            "page": (ROOT / "dist" / series / "wiki" / f"v{t:02d}" / "character" / f"{e['entity_id']}.md").is_file(),
            "renamed": bool(twin) and twin["canonical"] != e["canonical"],
            "forms": sorted(forms), "prefix": {p["canonical"]: sorted(_forms(p, t)) for p in hits.values()},
        })
    full_forms = {form for e in full for form in _forms(e, t)}
    prefix_only = sorted(p["canonical"] for p in prefix if p["type"] == "CHARACTER" and not _forms(p, t) & full_forms)

    def summary(sel: list[dict]) -> dict:
        n = {c: sum(r["class"] == c for r in sel) for c in CLASSES}
        return {"n": len(sel), **n, "renamed": sum(r["renamed"] for r in sel),
                "dependence": round(1 - n["same"] / len(sel), 4) if sel else None,
                "identity": round((n["regrouped"] + n["retyped"] + n["absent"]) / len(sel), 4) if sel else None}

    return {"series": series, "t": t, "all": summary(rows), "paged": summary([r for r in rows if r["page"]]),
            "mentions": {"full": _mentions(data / "02_entities" / "_premerge" / "mentions.jsonl", full, t),
                         "prefix": _mentions(data / f"@t{t:02d}" / "02_entities" / "mentions.jsonl", prefix, t)},
            "prefix_characters": sum(p["type"] == "CHARACTER" for p in prefix),
            "prefix_only": prefix_only, "entities": [r for r in rows if r["class"] != "same" or r["renamed"]]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=4)
    args = ap.parse_args()
    out = [measure(args.series, t) for t in range(1, args.upto + 1)]
    dest = ROOT / "docs" / "eval" / f"identity_dependence_{args.series}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("t  scope   n  same alias regrouped retyped absent renamed dependence identity  prefix_only")
    for r in out:
        for scope in ("all", "paged"):
            s = r[scope]
            print(f"{r['t']}  {scope:5} {s['n']:4} {s['same']:5} {s['alias']:5} {s['regrouped']:9} {s['retyped']:7} {s['absent']:6} "
                  f"{s['renamed']:7} {s['dependence']!s:>10} {s['identity']!s:>8}  {len(r['prefix_only']) if scope == 'all' else ''}")
    for r in out:
        m = r["mentions"]
        print(f"t={r['t']} character mentions in volumes 1..t: full {m['full']}, prefix {m['prefix']} ({m['prefix'] / m['full']:.3f})")
    print(f"-> {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
