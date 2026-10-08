"""[33] S02 — freeze the system before test, and check that it stayed frozen. $0.

Run:  .venv/Scripts/python.exe scripts/papers/freeze.py record <name> [--tag]
      .venv/Scripts/python.exe scripts/papers/freeze.py check <name>

`record` refuses unless `src/`, `config/` and `scripts/` have no uncommitted change, then writes
`docs/eval/freeze/<name>.json`: the commit, the git tree hash of each frozen directory, a sha256
of every config file, and the provider/model/price every routed stage resolves to right now (so a
"same model for every compared number" claim is checkable, not remembered). It prints the ledger
row to append to docs/MEASUREMENTS.md. `--tag` also creates the local git tag `freeze-<name>`.

`check` recomputes the same values and lists every difference; exit 1 if anything changed. Run it
before each test-work build (plan 0014 S03): a change after the freeze needs a new freeze and a
note of which steps it invalidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.config import load_settings  # noqa: E402

FROZEN = ("src", "config", "scripts")
OUT = ROOT / "docs" / "eval" / "freeze"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def snapshot() -> dict:
    settings = load_settings("anne")
    routes = {}
    for stage in sorted(settings.models.get("routing", {})):
        p = settings.resolve_role(stage)
        routes[stage] = {"role": p.name, "provider": p.provider, "model": p.model,
                         "price_in": p.price_in, "price_out": p.price_out,
                         "options": {k: v for k, v in sorted(p.options.items())}}
    return {
        "commit": git("rev-parse", "HEAD"),
        "trees": {d: git("rev-parse", f"HEAD:{d}") for d in FROZEN},
        "config_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((ROOT / "config").glob("*.yaml"))},
        "routes": routes,
    }


def dirty() -> list[str]:
    return [l for l in git("status", "--porcelain", "--", *FROZEN).splitlines() if l.strip()]


def diff(a: dict, b: dict, path: str = "") -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        p = f"{path}.{k}" if path else k
        if isinstance(a.get(k), dict) and isinstance(b.get(k), dict):
            out += diff(a[k], b[k], p)
        elif a.get(k) != b.get(k):
            out.append(f"{p}: {a.get(k)!r} -> {b.get(k)!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("record", "check"))
    ap.add_argument("name")
    ap.add_argument("--tag", action="store_true")
    a = ap.parse_args()
    path = OUT / f"{a.name}.json"
    if a.action == "record":
        if d := dirty():
            print("uncommitted changes under the frozen directories; commit first:\n  " + "\n  ".join(d))
            return 1
        snap = snapshot()
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, indent=1), encoding="utf-8")
        if a.tag:
            git("tag", f"freeze-{a.name}", snap["commit"])
        models = sorted({f"{r['provider']}:{r['model']}" for r in snap["routes"].values()})
        print(f"wrote {path.relative_to(ROOT)}")
        print(f"| <date> | Freeze `{a.name}` | commit {snap['commit'][:10]}; trees "
              + ", ".join(f"{d} {h[:10]}" for d, h in snap["trees"].items())
              + f"; routed models {', '.join(models)} | `python scripts/papers/freeze.py record {a.name}` | |")
        return 0
    if not path.is_file():
        print(f"no freeze record {path.relative_to(ROOT)}")
        return 1
    changes = diff(json.loads(path.read_text(encoding="utf-8")), snapshot())
    changes = [c for c in changes if not c.startswith("commit:")]  # a docs-only commit is fine
    for c in changes + [f"uncommitted: {d}" for d in dirty()]:
        print(c)
    if changes or dirty():
        return 1
    print(f"freeze {a.name}: unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
