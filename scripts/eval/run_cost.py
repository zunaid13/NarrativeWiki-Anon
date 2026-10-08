"""[33] S16 — what a build cost, from its own call ledger. $0 (reads files only).

Run:  .venv/Scripts/python.exe scripts/eval/run_cost.py <series> [--sha <git sha>] [--since <run id>]
      [--until <run id>] [--out docs/eval/cost/<series>.json]

Reads every `data/<series>/_runs/<id>/{manifest.json,calls.jsonl}` in the selected window (run ids
sort by start time; `--sha` keeps only runs made at one commit, e.g. the frozen tag's) and reports:

- **cold cost**: dollars and tokens of calls actually sent (not cache hits, not errors), by stage
  and by model; this is what building from an empty cache costs;
- **warm rebuild**: calls served from the cache — a replay costs $0 and these tokens are what it
  avoids re-sending;
- **per volume**: the cold cost of runs scoped to exactly one volume (scenes-vN, extract-vN), the
  part of the marginal cost of adding volume N that scales per volume. Whole-series stages
  (infobox, verify, synthesis) scale with the cast and are reported by stage, not by volume.

`--final` keeps only the last `ok` run of each (command, scope) -- the runs that produced what is on
disk -- so sent + cache-hit tokens give the size of one clean build, free of retries and re-renders.

Recorded `cost_usd` is the ledger's price at call time (Flex is priced at its real rate since
2026-09-28); it is never recomputed here, so a changed price in config cannot rewrite history.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _blank() -> dict:
    return {"sent": 0, "cache_hits": 0, "errors": 0, "in_tokens": 0, "out_tokens": 0,
            "hit_in_tokens": 0, "hit_out_tokens": 0, "cost_usd": 0.0}


def _add(acc: dict, call: dict) -> None:
    if call.get("error"):
        acc["errors"] += 1
    elif call.get("cache_hit"):
        acc["cache_hits"] += 1
        acc["hit_in_tokens"] += call.get("in_tokens") or 0
        acc["hit_out_tokens"] += call.get("out_tokens") or 0
    else:
        acc["sent"] += 1
        acc["in_tokens"] += call.get("in_tokens") or 0
        acc["out_tokens"] += call.get("out_tokens") or 0
        acc["cost_usd"] += call.get("cost_usd") or 0.0


def _final_runs(runs_dir: Path) -> set[str]:
    """The last `ok` run of each (command, scope) that made calls: the runs whose output is on disk now.
    (A later no-op rerun writes a manifest and no calls.jsonl; it must not hide the run that did the work.)"""
    last = {}
    for run in sorted(p for p in runs_dir.iterdir() if (p / "manifest.json").exists() and (p / "calls.jsonl").exists()):
        m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if m.get("outcome") == "ok":
            last[(m.get("command"), m.get("scope"))] = run.name
    return set(last.values())


def summarize(runs_dir: Path, sha: str | None = None, since: str | None = None,
              until: str | None = None, final: bool = False) -> dict:
    total, by_stage, by_model, by_volume = _blank(), defaultdict(_blank), defaultdict(_blank), defaultdict(_blank)
    runs = []
    keep = _final_runs(runs_dir) if final else None
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if (since and run.name < since) or (until and run.name > until) or (keep is not None and run.name not in keep):
            continue
        manifest_path, calls_path = run / "manifest.json", run / "calls.jsonl"
        if not calls_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if sha and not str(manifest.get("git_sha", "")).startswith(sha):
            continue
        vols = manifest.get("volumes") or []
        run_acc = _blank()
        for line in calls_path.open(encoding="utf-8"):
            call = json.loads(line)
            for acc in (total, run_acc, by_stage[call.get("stage", "?")], by_model[call.get("model", "?")]):
                _add(acc, call)
            if len(vols) == 1:
                _add(by_volume[vols[0]], call)
        runs.append({"run_id": run.name, "command": manifest.get("command"), "scope": manifest.get("scope"),
                     "git_sha": manifest.get("git_sha"), "outcome": manifest.get("outcome"), **run_acc})
    return {"total": total, "by_stage": dict(by_stage), "by_model": dict(by_model),
            "by_volume": {str(k): v for k, v in sorted(by_volume.items())}, "runs": runs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--sha")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--out")
    ap.add_argument("--final", action="store_true",
                    help="only the last ok run of each (command, scope); sent + cache-hit tokens are then "
                         "what one clean build of the artifact on disk needs (retries and superseded runs out)")
    a = ap.parse_args()
    runs_dir = ROOT / "data" / a.series / "_runs"
    if not runs_dir.is_dir():
        print(f"no runs at {runs_dir}", file=sys.stderr)
        return 1
    s = summarize(runs_dir, a.sha, a.since, a.until, a.final)
    out = Path(a.out) if a.out else ROOT / "docs" / "eval" / "cost" / f"{a.series}{'_final' if a.final else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"series": a.series, "sha": a.sha, "since": a.since, "until": a.until, **s},
                              indent=1), encoding="utf-8")
    t = s["total"]
    print(f"{a.series}: {len(s['runs'])} runs | cold ${t['cost_usd']:.2f} ({t['sent']} calls sent, "
          f"{t['in_tokens']:,} in / {t['out_tokens']:,} out) | warm rebuild $0 ({t['cache_hits']} cache hits, "
          f"{t['hit_in_tokens']:,} in / {t['hit_out_tokens']:,} out avoided) | {t['errors']} errors")
    if a.final:
        print(f"one clean build (sent + cache hits): {t['in_tokens'] + t['hit_in_tokens']:,} in / "
              f"{t['out_tokens'] + t['hit_out_tokens']:,} out, all models")
    print("| stage | sent | cost $ | in tok | out tok | cache hits |\n|---|---|---|---|---|---|")
    for stage, v in sorted(s["by_stage"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"| {stage} | {v['sent']} | {v['cost_usd']:.2f} | {v['in_tokens']:,} | {v['out_tokens']:,} | {v['cache_hits']} |")
    if s["by_volume"]:
        print("per-volume stages: " + " | ".join(f"v{k} ${v['cost_usd']:.2f}" for k, v in s["by_volume"].items()))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
