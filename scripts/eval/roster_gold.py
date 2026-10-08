"""[32] Score a series' gazetteer against its hand-made roster ground truth.

Usage:  python scripts/eval/roster_gold.py <series> [--json out.json]

Inputs:  `data/<series>/02_entities/gazetteer.json` (the pipeline's output) and
         `docs/eval/roster/<series>.yaml` (hand-made, keyed by surface form; see that file).
Output:  one line per check (PASS/FAIL/ABSENT) and a summary. ABSENT means the surface was not
         mined at all at this build's scope, which is neither a pass nor a failure.

Phase 32 req. 8 moved every hand override out of the series configs. This is what now measures
them: the general pipeline has to get these right on its own, and the score says how often it does.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _owners(entities: list[dict]) -> dict[str, list[dict]]:
    """casefolded surface -> every entity that carries it (canonical, alias or surface form)."""
    out: dict[str, list[dict]] = {}
    for e in entities:
        names = {e["canonical"], *e.get("aliases", []), *(sf["text"] for sf in e.get("surface_forms", []))}
        for n in names:
            out.setdefault(n.casefold(), []).append(e)
    return out


def score(entities: list[dict], gold: dict) -> list[tuple[str, str, str]]:
    owners = _owners(entities)
    rows: list[tuple[str, str, str]] = []

    def find(surface: str) -> list[dict]:
        return owners.get(surface.casefold(), [])

    for group in gold.get("same_entity") or []:
        ids = {tuple(sorted(e["entity_id"] for e in find(s))) for s in group if find(s)}
        found = [s for s in group if find(s)]
        if len(found) < 2:
            rows.append(("ABSENT", "same_entity", f"{group} (found {found})"))
        else:
            one = len({i for t in ids for i in t}) == 1
            rows.append(("PASS" if one else "FAIL", "same_entity",
                         f"{group} -> {sorted({i for t in ids for i in t})}"))
    for a, b in gold.get("different_entities") or []:
        ea, eb = {e["entity_id"] for e in find(a)}, {e["entity_id"] for e in find(b)}
        if not ea or not eb:
            rows.append(("ABSENT", "different_entities", f"{a} / {b}"))
        else:
            rows.append(("FAIL" if ea & eb else "PASS", "different_entities", f"{a} {sorted(ea)} / {b} {sorted(eb)}"))
    for surface, title in (gold.get("title_at_first_vol") or {}).items():
        es = find(surface)
        if not es:
            rows.append(("ABSENT", "title_at_first_vol", surface))
        else:
            got = [e["canonical"] for e in es]
            rows.append(("PASS" if title in got else "FAIL", "title_at_first_vol", f"{surface}: want {title!r}, got {got}"))
    for surface, bad in (gold.get("not_an_alias") or {}).items():
        for e in find(surface):
            names = {n.casefold() for n in (*e.get("aliases", []), *(sf["text"] for sf in e.get("surface_forms", [])))}
            for b in bad:
                rows.append(("FAIL" if b.casefold() in names else "PASS", "not_an_alias", f"{e['canonical']} / {b}"))
    for surface, typ in (gold.get("entity_type") or {}).items():
        es = find(surface)
        if not es:
            rows.append(("ABSENT", "entity_type", surface))
        else:
            got = [e["type"] for e in es]
            rows.append(("PASS" if typ in got else "FAIL", "entity_type", f"{surface}: want {typ}, got {got}"))
    canon = {e["canonical"].casefold(): e for e in entities}
    for surface in gold.get("not_an_entity") or []:
        e = canon.get(surface.casefold())
        rows.append(("FAIL" if e else "PASS", "not_an_entity", surface + (f" ({e['type']}, {e['mention_count']} mentions)" if e else "")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--json")
    args = ap.parse_args()
    gaz = json.loads((ROOT / "data" / args.series / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    gold = yaml.safe_load((ROOT / "docs" / "eval" / "roster" / f"{args.series}.yaml").read_text(encoding="utf-8"))
    rows = score(gaz["entities"], gold)
    for verdict, kind, detail in rows:
        print(f"{verdict:6} {kind:20} {detail}")
    judged = [r for r in rows if r[0] != "ABSENT"]
    passed = sum(r[0] == "PASS" for r in judged)
    print(f"\n{args.series}: {passed}/{len(judged)} checks pass ({len(rows) - len(judged)} absent at this scope); "
          f"{len(gaz['entities'])} entities")
    if args.json:
        Path(args.json).write_text(json.dumps({"series": args.series, "passed": passed, "judged": len(judged),
                                               "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
