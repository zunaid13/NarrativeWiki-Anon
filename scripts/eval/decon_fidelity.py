"""E0b (plan 0009): does a decontaminated paraphrase keep the paragraph's facts? Local MiniCheck, $0.

Run: .venv/Scripts/python.exe scripts/eval/decon_fidelity.py leagues-decon [--n 200]
For a seeded sample of paraphrased paragraphs, score both directions sentence by sentence:
  forward  = every paraphrase sentence supported by the remapped original (nothing invented);
  backward = every original sentence supported by the paraphrase (nothing dropped).
A paragraph passes a direction when its weakest sentence scores >= 0.5 (MiniCheck's own
threshold). Writes docs/eval/decon_fidelity_<series>.json (ids, scores and the 10 weakest pairs
for hand reading). Every score is cached through llm/client.py, so a re-run is free.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from narrativewiki import paths, provenance
from narrativewiki.config import load_settings
from narrativewiki.ingest.decontaminate import remap
from narrativewiki.llm.client import LLMClient


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])[”’\"']?\s+(?=[“\"‘A-Z])", text.strip())
    return [p for p in parts if len(p.split()) >= 3] or [text.strip()]


def weakest(client, document: str, claim_text: str) -> float:
    return min(client.score_support([document], s)["score"] for s in sentences(claim_text))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args()

    settings = load_settings(args.series)
    paths.set_active_series(args.series)
    cfg = settings.series["decontaminate"]
    load = lambda p: [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    vols = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    source = {r["para_id"]: r for v in vols for r in load(paths.parsed_volume_of(cfg["from_series"], v))}
    derived = [r for v in vols for r in load(paths.parsed_volume(v))
               if r.get("decon") == "paraphrased" and r["n_words"] >= 8]
    sample = sorted(random.Random(0).sample(derived, min(args.n, len(derived))), key=lambda r: r["para_id"])

    run = provenance.start_run(args.series, "decon_fidelity", scope=f"n{len(sample)}", volumes=vols,
                               volume_scope=max(vols), stage_keys=[], settings=settings)
    client = LLMClient(settings, run=run, volume_scope=max(vols))
    rows = []
    for i, rec in enumerate(sample, 1):
        original = remap(source[rec["para_id"]]["text"], cfg["entity_map"])
        rows.append({"para_id": rec["para_id"],
                     "forward": round(weakest(client, original, rec["text"]), 4),
                     "backward": round(weakest(client, rec["text"], original), 4)})
        if i % 25 == 0:
            print(f"{i}/{len(sample)}")
    run.finish("ok")

    def rate(key):
        return round(sum(r[key] >= 0.5 for r in rows) / len(rows), 4)

    worst = sorted(rows, key=lambda r: min(r["forward"], r["backward"]))[:10]
    report = {
        "series": args.series, "run_id": run.run_id, "n": len(rows), "threshold": 0.5,
        "forward_pass_rate": rate("forward"), "backward_pass_rate": rate("backward"),
        "both_pass_rate": round(sum(min(r["forward"], r["backward"]) >= 0.5 for r in rows) / len(rows), 4),
        "weakest": [{**w, "original": remap(source[w["para_id"]]["text"], cfg["entity_map"]),
                     "paraphrase": next(r["text"] for r in derived if r["para_id"] == w["para_id"])}
                    for w in worst],
        "rows": rows,
    }
    out = paths.DOCS_DIR / "eval" / f"decon_fidelity_{args.series}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("n", "forward_pass_rate", "backward_pass_rate", "both_pass_rate")}, "->", out)


if __name__ == "__main__":
    main()
