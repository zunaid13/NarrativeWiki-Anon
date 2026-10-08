"""[33] S15 — where each gold fact is kept or lost, stage by stage (Figure F3, metric M10). $0.

Run:  .venv/Scripts/python.exe scripts/eval/retention_trace.py <series> --upto <t>
      → docs/eval/retention/<series>_v<t>.json, and a per-stage count table on stdout

For every gold fact visible at cutoff t (`first_vol <= t`, docs/eval/parametric/<gold>.yaml) with
evidence paragraph P about character C, each stage is checked independently — a fact can arrive by
several routes, and it counts at a stage if ANY route reaches it (plan 0014 S15):

  entity    C resolves to a gazetteer entity whose first_vol <= t
  read      P was in a prompt a model read about C: a claim_extract / infobox / backstory prompt
            naming C, or a scene_extract span whose answer names C (every run's calls.jsonl)
  claimed   some claim about C cites P (03_claims/*.jsonl — mention-window, scene and pass files)
  graph     such a claim feeds an interval with vol_start <= t (04_graph/graph.db)
  verified  that fact was not flagged by the verifier (04_graph/verification.json, build cutoff)
  page      C's page at cutoff t cites P (dist/<series>/wiki/vNN/character/<slug>.md anchor)
  hand      a person read the page as carrying the fact (docs/eval/recall_hand/<series>_v<t>.json)

`claimed`, `graph` and `page` match on the cited paragraph, not on meaning: a claim at P may be a
different fact, and the fact may be carried from another paragraph. They are therefore a paragraph
route, not a semantic match; `hand` is the semantic end point. The gap between them is itself
reported (`page` without `hand`, `hand` without `page`).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki import paths  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.probe.parametric import gold_pages  # noqa: E402

STAGES = ("entity", "read", "claimed", "graph", "verified", "page", "hand")
READ_STAGES = {"claim_extract", "infobox_extract", "backstory_extract", "scene_extract"}


def anchor(para_id: str) -> str:
    return "nw-" + para_id.replace(":", "-")


def gold_facts(gold: dict, upto: int) -> list[dict]:
    out = []
    for ch, body in (gold.get("characters") or {}).items():
        for f in body.get("facts", []):
            first = int(f["evidence"].split(":")[0][1:])
            if first <= upto:
                out.append({"character": ch, "claim": f["claim"], "para_id": f["evidence"], "first_vol": first})
    return out


def read_routes(runs_dir: Path, wanted: set[str]) -> dict[str, list[tuple[str, str, str]]]:
    """para_id -> [(stage, prompt head, response)] for every call whose prompt shows that paragraph."""
    hits: dict[str, list[tuple[str, str, str]]] = {p: [] for p in wanted}
    tags = {f"[{p}]": p for p in wanted}
    for calls in sorted(runs_dir.glob("*/calls.jsonl")):
        for line in calls.open(encoding="utf-8"):
            r = json.loads(line)
            if r.get("stage") not in READ_STAGES or r.get("error"):
                continue
            prompt = r.get("prompt") or ""
            for tag, p in tags.items():
                if tag in prompt:
                    hits[p].append((r["stage"], prompt[:400], r.get("response") or ""))
    return hits


def trace(series: str, upto: int) -> dict:
    settings = load_settings(series)
    paths.set_active_series(series)
    gold_name = settings.series.get("gold_from") or (settings.series.get("decontaminate") or {}).get("from_series") or series
    gold = yaml.safe_load((paths.DOCS_DIR / "eval" / "parametric" / f"{gold_name}.yaml").read_text(encoding="utf-8"))
    facts = gold_facts(gold, upto)

    gaz = json.loads(paths.gazetteer().read_text(encoding="utf-8")) if paths.gazetteer().is_file() else {}
    entities = gaz.get("entities", gaz) if isinstance(gaz, dict) else gaz
    by_name = {}
    by_id = {e["entity_id"]: e for e in (entities.values() if isinstance(entities, dict) else entities)}
    for e in (entities.values() if isinstance(entities, dict) else entities):
        for name in [e.get("canonical"), *e.get("aliases", [])]:
            if name:
                by_name.setdefault(name, e)

    claims_by_para: dict[tuple[str, str], set[str]] = {}
    for f in sorted(paths.CLAIMS_DIR.glob("*.jsonl")) if paths.CLAIMS_DIR.is_dir() else []:
        for line in f.open(encoding="utf-8"):
            c = json.loads(line)
            for ev in c.get("evidence") or []:
                for subj in {c.get("subject"), c.get("object") if c.get("kind") == "relation" else None} - {None}:
                    claims_by_para.setdefault((subj, ev.get("para_id")), set()).add(c["claim_id"])

    interval_claims: set[str] = set()
    if paths.graph_db().is_file():
        con = sqlite3.connect(paths.graph_db())
        for (ids,) in con.execute("SELECT claim_ids_json FROM intervals WHERE vol_start <= ?", (upto,)):
            interval_claims.update(json.loads(ids or "[]"))
        con.close()

    flagged: set[tuple[str, str]] = set()
    if paths.verification().is_file():
        ver = json.loads(paths.verification().read_text(encoding="utf-8"))
        for res in ver.get("results", []):
            for fl in res.get("flagged", []):
                for ev in fl.get("evidence") or []:
                    flagged.add((res["entity_id"], ev.get("para_id")))

    hand_path = paths.DOCS_DIR / "eval" / "recall_hand" / f"{series}_v{upto}.json"
    hand = json.loads(hand_path.read_text(encoding="utf-8")) if hand_path.is_file() else None
    reads = read_routes(paths.RUNS_DIR, {f["para_id"] for f in facts})

    rows = []
    for f in facts:
        # [33] The system's page(s) for this gold character (docs/eval/parametric/pages/), and their
        # entities: B5 files Diana Barry under "Diana", Anne over "Anne" + "Anne Shirley".
        pages = gold_pages(series, f["character"], paths.wiki_cutoff_dir(upto))
        ents = [by_id[p.stem] for p in pages if p.stem in by_id] or ([by_name[f["character"]]] if f["character"] in by_name else [])
        e = ents[0] if ents else None
        eid = e.get("entity_id") if e else None
        names = [f["character"]] + [a for x in ents for a in x.get("aliases", [])]
        page_text = "\n\n".join(p.read_text(encoding="utf-8") for p in pages)
        cids = set().union(*(claims_by_para.get((x["entity_id"], f["para_id"]), set()) for x in ents))
        routes = [s for s, head, resp in reads[f["para_id"]]
                  if (s == "scene_extract" and any(n in resp for n in names))
                  or (s != "scene_extract" and any(f'"{n}"' in head for n in names))]
        st = {
            "entity": any(int(x.get("first_vol", 99)) <= upto for x in ents),
            "read": bool(routes),
            "claimed": bool(cids),
            "graph": bool(cids & interval_claims),
            "verified": bool(cids & interval_claims) and not any((x["entity_id"], f["para_id"]) in flagged for x in ents),
            "page": anchor(f["para_id"]) in page_text,
            "hand": None if hand is None else hand.get(f"{f['character']} | {f['claim']}") == "yes",
        }
        rows.append({**f, "entity_id": eid, "read_routes": sorted(set(routes)), "claim_ids": sorted(cids), **st})
    counts = {s: sum(1 for r in rows if r[s]) for s in STAGES}
    gaps = {"page_not_hand": sum(1 for r in rows if r["page"] and r["hand"] is False),
            "hand_not_page": sum(1 for r in rows if r["hand"] and not r["page"])}
    return {"series": series, "upto": upto, "gold": gold_name, "facts": len(rows), "counts": counts,
            "hand_available": hand is not None, "gaps": gaps, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, required=True)
    a = ap.parse_args()
    res = trace(a.series, a.upto)
    out = paths.DOCS_DIR / "eval" / "retention" / f"{a.series}_v{a.upto}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    n = res["facts"]
    print(f"{a.series} t={a.upto}: {n} gold facts visible")
    for s in STAGES:
        c = res["counts"][s]
        print(f"  {s:9} {c:>3}/{n}" + ("  (no hand review yet)" if s == "hand" and not res["hand_available"] else ""))
    lost = Counter(next((s for s in STAGES[:6] if not r[s]), "kept") for r in res["rows"])
    print("  first stage missing: " + ", ".join(f"{k} {v}" for k, v in lost.most_common()))
    print(f"  gaps: {res['gaps']}\n{paths.relative(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
