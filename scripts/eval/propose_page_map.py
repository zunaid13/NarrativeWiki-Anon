"""[34] Draft `docs/eval/parametric/pages/<series>.yaml` from a built gazetteer. $0, no LLM.

Run:  .venv/Scripts/python.exe scripts/eval/propose_page_map.py <series> [--gold anne]

For every gold character, the CHARACTER entities whose canonical name, alias or surface form is the
gold name (or whose slug is the gold name's slug), with each one's mentions and first volume. It
prints a draft; a reader confirms each line against the built pages before the map is written (the
rule in `docs/eval/parametric/pages/anne.yaml`'s header). Names with no match are listed for a
by-hand search: a page-less gold character stays in every denominator.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.entities.gazetteer import slugify  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--gold", default=None, help="gold series id (default: the series' base)")
    a = ap.parse_args()
    gold_id = a.gold or a.series.split("@", 1)[0]
    gold = yaml.safe_load((ROOT / "docs" / "eval" / "parametric" / f"{gold_id}.yaml").read_text(encoding="utf-8"))
    gaz = json.loads((ROOT / "data" / a.series / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    chars = [e for e in gaz["entities"] if e["type"] == "CHARACTER"]
    unmatched = []
    for name in gold["characters"]:
        hits = [e for e in chars if e["canonical"] == name or e["entity_id"] == slugify(name)
                or any(sf["text"] == name for sf in e["surface_forms"])]
        if not hits:
            unmatched.append(name)
            continue
        print(f"{name}: [{', '.join(e['entity_id'] for e in hits)}]   # "
              + "; ".join(f"{e['entity_id']} {e['mention_count']} from v{e['first_vol']}" for e in hits))
    print("# no exact match:", ", ".join(unmatched) or "none")


if __name__ == "__main__":
    main()
