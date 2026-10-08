"""[33] Side-by-side of two builds of one work (e.g. the generator trial anne-m31 vs anne-m36). $0.

Run:  .venv/Scripts/python.exe scripts/eval/compare_builds.py <series A> <series B> --upto <t>

Reads only what each build already wrote — nothing is re-scored, no model is called:
  gazetteer   entities / characters                 (data/<s>/02_entities/gazetteer.json)
  claims      claim rows by kind                    (data/<s>/03_claims/*.jsonl)
  graph       intervals visible at t                (data/<s>/04_graph/graph.db)
  verify      facts checked / flagged               (data/<s>/04_graph/verification.json)
  pages       character pages at t, total words     (dist/<s>/wiki/vNN/character/*.md)
  leak/recall control recall and future-leak rates  (docs/eval/pipeline_leak_<s>_v<t>.json, tool screen)
  precision   hand verdicts if filled               (docs/eval/precision/<s>_v<t>.jsonl, `human` column)
  cost        cold $ and calls sent                 (scripts/eval/run_cost.py over the build's runs)
The leak/recall numbers are the tool's screen, not hand-read values (MEASUREMENTS §47): they rank
two builds against the same gold, and every positive is still read by hand before it is reported.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_cost", Path(__file__).parent / "run_cost.py")
run_cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_cost)


def build_stats(series: str, t: int) -> dict:
    d, dist = ROOT / "data" / series, ROOT / "dist" / series / "wiki" / f"v{t:02d}" / "character"
    out: dict = {}
    gaz = d / "02_entities" / "gazetteer.json"
    if gaz.is_file():
        ents = json.loads(gaz.read_text(encoding="utf-8"))["entities"]
        out["entities"] = len(ents)
        out["characters"] = sum(1 for e in ents if e["type"] == "CHARACTER")
    kinds = Counter()
    for f in sorted((d / "03_claims").glob("*.jsonl")) if (d / "03_claims").is_dir() else []:
        for line in f.open(encoding="utf-8"):
            kinds[json.loads(line).get("kind", "?")] += 1
    out["claims"] = sum(kinds.values())
    out["claims_by_kind"] = dict(kinds)
    db = d / "04_graph" / "graph.db"
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        out["intervals_at_t"] = con.execute("SELECT COUNT(*) FROM intervals WHERE vol_start <= ?", (t,)).fetchone()[0]
        con.close()
    ver = d / "04_graph" / "verification.json"
    if ver.is_file():
        s = json.loads(ver.read_text(encoding="utf-8")).get("summary", {})
        out["verify_checked"], out["verify_flagged"] = s.get("facts_checked"), s.get("facts_flagged")
    if dist.is_dir():
        pages = list(dist.glob("*.md"))
        out["pages"] = len(pages)
        out["page_words"] = sum(len(p.read_text(encoding="utf-8").split()) for p in pages)
    leak = ROOT / "docs" / "eval" / f"pipeline_leak_{series}_v{t}.json"
    if leak.is_file():
        s = next((x for x in json.loads(leak.read_text(encoding="utf-8"))["summary"] if x.get("mode") == "pipeline"), {})
        for k in ("n_control", "control_recall", "n_future", "future_leak_rate", "future_leak_rate_either"):
            out[f"screen_{k}"] = s.get(k)
    prec = ROOT / "docs" / "eval" / "precision" / f"{series}_v{t}.jsonl"
    if prec.is_file():
        v = Counter((json.loads(l).get("human") or "unread").strip().lower() for l in prec.open(encoding="utf-8"))
        out["precision_hand"] = dict(v)
    if (d / "_runs").is_dir():
        c = run_cost.summarize(d / "_runs")["total"]
        out["cost_usd"], out["calls_sent"] = round(c["cost_usd"], 3), c["sent"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--upto", type=int, required=True)
    x = ap.parse_args()
    sa, sb = build_stats(x.a, x.upto), build_stats(x.b, x.upto)
    print(f"| measure | {x.a} | {x.b} |\n|---|---|---|")
    for k in list(dict.fromkeys([*sa, *sb])):
        print(f"| {k} | {sa.get(k, '—')} | {sb.get(k, '—')} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
