"""[32] Run the whole pipeline for one series, volumes 1..N, then every audit and evaluation.

Run:  .venv/Scripts/python.exe scripts/run_series.py <series> [--upto 5] [--from <step>] [--to <step>]
      [--list] [--set KEY=VALUE ...]

Nothing here is series-specific (req. 8): the same steps, in the same order, for every series.
Each stage runs as its own `wiki` process, so no single run approaches `budget.hard_stop_usd`.
A step that fails on a Vertex 429 RESOURCE_EXHAUSTED is retried after 15 minutes (60 until 2026-10-01, maintainer), at most 3
times (standing rule: stop on repeated API failure); any other failure stops the driver. Audit
FAILs are recorded and the driver continues. Every completed LLM call is cached, so resuming
with --from re-sends only what failed. Per-step logs: data/<series>/_driver/<step>.log.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
CLI = [PY, "-m", "narrativewiki.cli"]
AUDITS = ("ingest", "gazetteer", "claims", "contradictions", "verify", "pages", "links", "events",
          "relationships", "outline", "eval")


def steps(series: str, upto: int, extra: list[str] = ()) -> list[tuple[str, list[str]]]:
    s = ["--series", series, *extra]  # extra: per-run --set overrides (stage commands only)
    vols = f"1-{upto}"
    # --force: scene records carry entity ids, so outputs built on an older gazetteer are stale
    # (Overlord's v1-2 epithets named "ainz-ooal-gown", Ainz's page then, the guild now). Calls
    # whose prompt did not change are cache hits.
    out = [(f"scenes-v{v}", CLI + ["scenes", *s, "--volumes", str(v), "--force"]) for v in range(1, upto + 1)]
    out.append(("merge-epithets", CLI + ["gazetteer", *s, "--volumes", vols, "--merge-epithets"]))
    out += [(f"extract-v{v}", CLI + ["extract", *s, "--volumes", str(v), "--force"]) for v in range(1, upto + 1)]
    out += [
        ("infobox", CLI + ["infobox", *s, "--volumes", vols, "--force"]),
        ("backstory", CLI + ["infobox", *s, "--volumes", vols, "--pass", "backstory", "--force"]),
        ("graph", CLI + ["graph", "build", *s]),
        ("verify", CLI + ["verify", *s, "--upto", str(upto)]),
        ("events", CLI + ["events-build", *s, "--volumes", vols, "--force"]),
        ("synthesize", CLI + ["synthesize", *s, "--upto", str(upto)]),
        ("site", CLI + ["site", "build", *s, "--upto", str(upto), "--html"]),
    ]
    out += [(f"audit-{a}", CLI + ["audit", a, "--series", series]) for a in AUDITS]
    out += [(f"leak-t{t}", [PY, str(ROOT / "scripts" / "eval" / "pipeline_page_leak.py"), series, "--upto", str(t)])
            for t in range(1, upto + 1)]
    out.append(("artifact-exposure", [PY, str(ROOT / "scripts" / "eval" / "artifact_exposure.py"), series,
                                      "--upto", str(upto)]))
    # Every evaluation is per volume cutoff (PHASE_33): one precision sample per t.
    out += [(f"precision-t{t}", [PY, str(ROOT / "scripts" / "eval" / "assertion_precision.py"), series,
                                 "--upto", str(t), "--n", "40"]) for t in range(1, upto + 1)]
    return out


# [33] `merge-epithets` rewrites these in place, and the scene prompts list the gazetteer's aliases,
# so a retry that restarts at scenes ran on an already-merged gazetteer: every call missed the
# cache and the build was not the one-merge protocol (A1 t=2/t=3, 2026-10-01, CHANGES C16).
MERGED = ("gazetteer.json", "surface_forms.jsonl", "mentions.jsonl")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def premerge_guard(entities: Path, step: str) -> str | None:
    """Before `merge-epithets`: snapshot the three files. Before a step that precedes it (scenes):
    restore the snapshot, but only if the gazetteer is still exactly what that merge wrote -- a
    `wiki gazetteer --force` rebuild since then makes the snapshot stale, and it is dropped."""
    snap = entities / "_premerge"
    if not (entities / "gazetteer.json").exists():
        return None
    if step == "merge-epithets":
        if not (snap / "gazetteer.json").exists():  # a failed merge keeps the first, clean copy
            snap.mkdir(exist_ok=True)
            for f in MERGED:
                shutil.copy2(entities / f, snap / f)
        return "snapshot"
    if not step.startswith("scenes-") or not (snap / "merged.sha").exists():
        return None
    if _sha(entities / "gazetteer.json") != (snap / "merged.sha").read_text().strip():
        shutil.rmtree(snap)
        return "stale snapshot dropped"
    for f in MERGED:
        shutil.copy2(snap / f, entities / f)
    shutil.rmtree(snap)
    return "restored pre-merge gazetteer"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=5)
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="stop")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="passed to every stage command, e.g. models.profiles.vertex_flash.max_volumes=6")
    args = ap.parse_args()
    plan = steps(args.series, args.upto, [x for kv in args.set for x in ("--set", kv)])
    names = [n for n, _ in plan]
    if args.list:
        print("\n".join(names))
        return 0
    lo = names.index(args.start) if args.start else 0
    hi = names.index(args.stop) + 1 if args.stop else len(plan)
    logdir = ROOT / "data" / args.series / "_driver"
    logdir.mkdir(parents=True, exist_ok=True)
    if (logdir / "SKIP").exists():  # lets a queued chain of series skip one without stopping
        print(f"{args.series}: skipped ({logdir / 'SKIP'} exists)", flush=True)
        return 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    entities = ROOT / "data" / args.series / "02_entities"
    for name, cmd in plan[lo:hi]:
        if (note := premerge_guard(entities, name)) and note != "snapshot":
            print(f"{name}: {note}", flush=True)
        handed_back = False
        for attempt in range(1, 4):
            proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            out = proc.stdout + proc.stderr
            (logdir / f"{name}.log").write_text(out, encoding="utf-8")
            stamp = datetime.now(UTC).strftime("%H:%M")
            if proc.returncode == 0:
                if name == "merge-epithets":  # what the merge wrote, so a later restore can trust it
                    (entities / "_premerge" / "merged.sha").write_text(_sha(entities / "gazetteer.json"))
                print(f"{stamp} {name}: ok", flush=True)
                break
            if "RESOURCE_EXHAUSTED" in out:
                # [34] RUN_SERIES_429_RETRIES=0: hand the 429 back to the caller at once, so a runner that
                # holds two Vertex projects can switch instead of waiting 45 min on the refusing one.
                if attempt > int(os.environ.get("RUN_SERIES_429_RETRIES", "3")):
                    handed_back = True
                    break
                print(f"{stamp} {name}: 429, retry in 15 min (attempt {attempt})", flush=True)
                time.sleep(900)
                continue
            if name.startswith(("audit-", "leak-", "precision-")):
                print(f"{stamp} {name}: FAIL (exit {proc.returncode}), continuing; see {name}.log", flush=True)
                break
            print(f"{stamp} {name}: FAILED (exit {proc.returncode}), stopping; see data/{args.series}/_driver/{name}.log",
                  flush=True)
            return 1
        else:
            print(f"{name}: still 429 after 3 attempts, stopping", flush=True)
            return 2
        if handed_back:
            print(f"{stamp} {name}: 429 (RESOURCE_EXHAUSTED), returned to the caller", flush=True)
            return 2
    print("all steps done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
