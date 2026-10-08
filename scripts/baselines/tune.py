"""[33] S10 — choose a baseline's settings on the DEVELOPMENT work, never on a test work.

Run:  .venv/Scripts/python.exe scripts/baselines/tune.py b2 [--work middlemarch] [--upto 2]
      .venv-b3/Scripts/python.exe scripts/baselines/tune.py b3 [--work middlemarch] [--upto 2]

For each setting in the grid: write the baseline's pages (run_baseline.py / b3_lightrag.py), score
them with `pipeline_page_leak.py` (local MiniCheck screen, $0), and keep control recall, the future
screen rate, pages and input tokens. The chosen setting maximises screen recall; ties go to fewer
input tokens. The screen is the tuning signal only — reported numbers are hand-read on the test
work. Output: docs/eval/baseline_tuning/<work>_<system>_v<t>.json (every row, including losers).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PY_B3 = str(ROOT / ".venv-b3" / "Scripts" / "python.exe")
GRIDS = {
    "b2": [{"--chunk-words": c, "--budget-tokens": b} for c in (150, 300) for b in (6000, 12000, 20000)],
    "b3": [{"--mode": m, "--budget-tokens": 20000} for m in ("mix", "hybrid")],  # budget = B2's tuned one
}


def run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd)}\n{out.stdout[-1500:]}\n{out.stderr[-1500:]}")
    return out.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("system", choices=sorted(GRIDS))
    ap.add_argument("--work", default="middlemarch")
    ap.add_argument("--upto", type=int, default=2)
    a = ap.parse_args()
    variant, rows = f"{a.work}@{a.system}", []
    if a.system == "b3":  # the index is built once per cutoff (t is t-1 plus volume t); modes then only query
        for t in range(1, a.upto + 1):
            run([PY_B3, "scripts/baselines/b3_lightrag.py", a.work, "--upto", str(t)])
    for setting in GRIDS[a.system]:
        args = [x for k, v in setting.items() for x in (k, str(v))]
        if a.system == "b2":
            run([PY, "scripts/baselines/run_baseline.py", a.work, "B2", "--upto", str(a.upto), *args])
        else:
            run([PY_B3, "scripts/baselines/b3_lightrag.py", a.work, "--upto", str(a.upto), *args])
        run([PY, "scripts/eval/pipeline_page_leak.py", variant, "--upto", str(a.upto)])
        leak = json.loads((ROOT / "docs" / "eval" / f"pipeline_leak_{variant}_v{a.upto}.json").read_text(encoding="utf-8"))
        s = next(x for x in leak["summary"] if x.get("mode") == "pipeline")
        manifest = json.loads((ROOT / "dist" / variant / "wiki" / f"v{a.upto:02d}" / "_baseline.json").read_text(encoding="utf-8"))
        rows.append({"setting": setting, "control_recall_screen": s["control_recall"], "n_control": s["n_control"],
                     "future_screen_rate": s["future_leak_rate_either"], "pages": len(manifest["written"]),
                     "run_id": manifest["run_id"], "stats": manifest.get("stats", {})})
        print(rows[-1], flush=True)
    best = max(rows, key=lambda r: (r["control_recall_screen"], -r["setting"].get("--budget-tokens", 0)))
    out = ROOT / "docs" / "eval" / "baseline_tuning" / f"{a.work}_{a.system}_v{a.upto}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"work": a.work, "system": a.system, "upto": a.upto, "rows": rows, "chosen": best["setting"]},
                              indent=1), encoding="utf-8")
    print("chosen", best["setting"], "->", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
