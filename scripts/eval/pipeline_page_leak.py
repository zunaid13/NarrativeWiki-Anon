"""E2 (plan 0009): do the gated pipeline's cutoff-t pages state facts from after t? $0, local.

Run: .venv/Scripts/python.exe scripts/eval/pipeline_page_leak.py leagues [--upto 1]
Scores each gold character's rendered page `dist/<series>/wiki/vNN/character/<id>.md` against
docs/eval/parametric/<source>.yaml exactly as `probe run parametric` scores its generated pages
(probe/parametric.py: MiniCheck >= 0.5 and lexical markers absent from the volumes <= t text).
Writes docs/eval/pipeline_leak_<series>_v<t>.json. Every positive must still be read by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import yaml

from narrativewiki import paths, provenance
from narrativewiki.config import load_settings
from narrativewiki.ingest.decontaminate import remap
from narrativewiki.llm.client import LLMClient
from narrativewiki.probe import parametric as par


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=1)
    args = ap.parse_args()

    settings = load_settings(args.series)
    paths.set_active_series(args.series)
    cfg = settings.series.get("decontaminate") or {}
    entity_map = cfg.get("entity_map") or {}
    # `gold_from` (series config, [33]): a series rebuilt from another's corpus scores against its gold.
    source = settings.series.get("gold_from") or cfg.get("from_series", args.series)
    gold = yaml.safe_load((paths.DOCS_DIR / "eval" / "parametric" / f"{source}.yaml")
                          .read_text(encoding="utf-8"))
    records = [json.loads(line) for v in range(1, args.upto + 1)
               for line in paths.parsed_volume(v).read_text(encoding="utf-8").splitlines() if line.strip()]
    keywords = par.valid_keywords(gold, par.volume_text(records), entity_map, args.upto)

    jobs, pages = [], []
    for character in gold["characters"]:
        name = remap(character, entity_map)
        found = par.gold_pages(args.series, name, paths.wiki_cutoff_dir(args.upto))
        jobs.append({"character": name, "mode": "pipeline"})
        pages.append("\n\n".join(page.read_text(encoding="utf-8") for page in found))
        if not found:  # stays in the denominators as an empty page (2026-09-28)
            print(f"no page for {name} at v{args.upto:02d}: scored as empty")

    run = provenance.start_run(args.series, "pipeline_page_leak", scope=f"v{args.upto}", volumes=[args.upto],
                               volume_scope=args.upto, stage_keys=[], settings=settings)
    client = LLMClient(settings, run=run, volume_scope=args.upto)
    rows = par.score(client, jobs, pages, gold, entity_map, keywords, args.upto)
    run.finish("ok")
    summary = par.summarize(rows, {(j["character"], j["mode"]): p for j, p in zip(jobs, pages)})
    out = paths.DOCS_DIR / "eval" / f"pipeline_leak_{args.series}_v{args.upto}.json"
    out.write_text(json.dumps({"series": args.series, "upto": args.upto, "run_id": run.run_id,
                               "summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    for s in summary:
        print({k: s[k] for k in ("mode", "n_future", "future_leak_rate", "future_leak_rate_lexical",
                                 "future_leak_rate_either", "n_control", "control_recall", "page_words")})
    for r in rows:
        if r["kind"] == "future" and (r["supported"] or r["lexical"]):
            print(f"  POSITIVE {r['score']:.2f} {r['markers']} {r['character']}: {r['claim']}")
    print("->", paths.relative(out))


if __name__ == "__main__":
    main()
