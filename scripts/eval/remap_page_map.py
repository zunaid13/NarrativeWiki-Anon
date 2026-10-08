"""[34] Page map for a decontaminated build, derived from its source build's confirmed map. $0, no LLM.

Run:  .venv/Scripts/python.exe scripts/eval/remap_page_map.py anne@v2 anne-decon@v2

Each gold character and each mapped page's canonical name go through the target series' own name table
(`decontaminate.entity_map`, ingest/decontaminate.remap); a page is kept when the target gazetteer has an
entity with that remapped canonical or alias. Writes docs/eval/parametric/pages/<target>.yaml, marked
mechanical: a reader confirms it against the built pages before scoring, as for every page map.
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.ingest.decontaminate import remap  # noqa: E402


def entities(series: str) -> list[dict]:
    g = json.loads((ROOT / "data" / series / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    return g.get("entities", g) if isinstance(g, dict) else g


def main(source: str, target: str) -> None:
    pages = ROOT / "docs" / "eval" / "parametric" / "pages"
    src_map = yaml.safe_load((pages / f"{source}.yaml").read_text(encoding="utf-8"))
    emap = (load_settings(target).series.get("decontaminate") or {}).get("entity_map") or {}
    canon = {e["entity_id"]: e["canonical"] for e in entities(source)}
    by_name = {}
    for e in entities(target):
        for n in (e["canonical"], *e.get("aliases", [])):
            by_name.setdefault(n, e["entity_id"])
    out, lost = {}, []
    for character, slugs in src_map.items():
        hits = [by_name[remap(canon[s], emap)] for s in slugs if s in canon and remap(canon[s], emap) in by_name]
        if hits:
            out[remap(character, emap)] = sorted(set(hits))
        else:
            lost.append(character)
    head = (f"# [34] Mechanical: {source}'s confirmed map through {target}'s name table "
            f"(scripts/eval/remap_page_map.py). To be confirmed against the built pages before scoring.\n"
            f"# Not found ({len(lost)}): {', '.join(lost) or 'none'}\n")
    (pages / f"{target}.yaml").write_text(head + yaml.safe_dump(out, allow_unicode=True, sort_keys=True), encoding="utf-8")
    print(f"{target}: {len(out)} of {len(src_map)} characters mapped; not found: {lost}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
