"""[33] S12 — per-cutoff values, means over cutoffs, and character-clustered intervals. $0.

Run:  .venv/Scripts/python.exe scripts/eval/paper_numbers.py <observations.jsonl> [...]
      [--reps 2000] [--seed 0] [--pair B5:B1 ...] [--out docs/eval/paper_numbers.json]

Input: one JSON object per observation (a gold fact, a sampled assertion, an assessed future fact):
  {"work": "anne", "system": "B5", "cutoff": 1, "character": "diana-barry",
   "metric": "M1", "num": 1, "den": 1, "weight": 1.0}
`num`/`den` are counts (a fact found = 1/1, missed = 0/1); `weight` is the design weight of a
sampled item (M3 is design-weighted; default 1). A metric whose rows carry no `den` is a count
(N, assessed atomic assertions): its per-cutoff value is the weighted sum of `num`.

Output keys follow `docs/paper/results.tex`: `<METRIC>-<work>-t<k>-<system>` and
`<METRIC>-<work>-avg-<system>`. Per plan 0013 §7 / plan 0014 S12:
- a proportion at cutoff t is the weighted ratio over all of that cutoff's observations;
- the `avg` value is the unweighted mean over the cutoffs present, **within one work** — never
  pooled across works;
- intervals are percentile bootstraps that resample **characters within a work**, keeping each
  character's whole cutoff trajectory together (the unit is the character);
- `--pair A:B` reports the paired difference A − B on the same resampled characters;
- a proportion with zero events is also reported as `0/n` with an exact one-sided 95% upper bound
  computed over the number of CHARACTERS k, 1 − 0.05^(1/k), not over the n items: items within a
  page are correlated, so n would overstate the evidence (paper §5, setup-stats). A bootstrap of
  zero events is degenerate.

Prints one ledger-ready line per key; it never writes `results.tex` (filling that waits for the
maintainer's review). Intervals reflect annotation sampling over characters, not generation
variation (one generation per system).
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        for line in p.open(encoding="utf-8"):
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _cell_value(obs: list[dict], is_count: bool) -> tuple[float | None, float, float]:
    """(value, weighted num, weighted den) for one (metric, work, system, cutoff) cell."""
    num = sum(o.get("weight", 1.0) * o["num"] for o in obs)
    if is_count:
        return num, num, 0.0
    den = sum(o.get("weight", 1.0) * o["den"] for o in obs)
    return (num / den if den else None), num, den


def _avg(by_char: dict[str, list[dict]], chars: list[str], is_count: bool) -> tuple[dict[int, float], float | None]:
    per_t: dict[int, list[dict]] = defaultdict(list)
    for c in chars:
        for o in by_char[c]:
            per_t[o["cutoff"]].append(o)
    vals = {t: _cell_value(obs, is_count)[0] for t, obs in sorted(per_t.items())}
    present = [v for v in vals.values() if v is not None]
    return vals, (sum(present) / len(present) if present else None)


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


def zero_event_bound(n: float) -> float:
    """Exact one-sided 95% upper bound for a proportion with 0 events in n independent units."""
    return 1 - 0.05 ** (1 / n) if n > 0 else 1.0


def compute(rows: list[dict], reps: int = 2000, seed: int = 0, pairs: list[tuple[str, str]] = ()) -> list[dict]:
    groups: dict[tuple[str, str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for o in rows:
        groups[(o["metric"], o["work"], o["system"])][o["character"]].append(o)
    out = []
    boot: dict[tuple[str, str, str], list[float]] = {}
    for (metric, work, system), by_char in sorted(groups.items()):
        is_count = all("den" not in o for obs in by_char.values() for o in obs)
        chars = sorted(by_char)
        per_t, avg = _avg(by_char, chars, is_count)
        for t, v in per_t.items():
            obs = [o for c in chars for o in by_char[c] if o["cutoff"] == t]
            _, num, den = _cell_value(obs, is_count)
            row = {"key": f"{metric}-{work}-t{t}-{system}", "value": v, "num": num, "den": den,
                   "characters": len({o['character'] for o in obs})}
            if not is_count and num == 0 and den:
                row["zero_event_upper95"] = zero_event_bound(row["characters"])  # clusters, not items
            out.append(row)
        # One resample of characters per work, shared by every system of that work (paired).
        rng = random.Random(f"{seed}-{work}-{metric}")
        draws = []
        for _ in range(reps):
            draw = [chars[rng.randrange(len(chars))] for _ in chars]
            # Map each resampled character to fresh ids so a character drawn twice counts twice.
            dup = {f"{c}#{i}": by_char[c] for i, c in enumerate(draw)}
            draws.append(_avg(dup, list(dup), is_count)[1])
        good = [d for d in draws if d is not None]
        boot[(metric, work, system)] = draws
        out.append({"key": f"{metric}-{work}-avg-{system}", "value": avg, "cutoffs": len(per_t),
                    "characters": len(chars), "ci95": [_pct(good, 0.025), _pct(good, 0.975)] if good else None,
                    "unit": "character (trajectory kept)", "reps": reps})
    for a, b in pairs:
        for (metric, work, system), draws_a in sorted(boot.items()):
            if system != a or (metric, work, b) not in boot:
                continue
            ga, gb = groups[(metric, work, a)], groups[(metric, work, b)]
            # Pair on the (character, cutoff) cells BOTH systems have (plan 0015: a system built at
            # fewer cutoffs, e.g. B2 from t=2, must not be compared with the other's extra cutoffs).
            cells_a = {(c, o["cutoff"]) for c, obs in ga.items() for o in obs}
            cells_b = {(c, o["cutoff"]) for c, obs in gb.items() for o in obs}
            both = cells_a & cells_b
            ga = {c: [o for o in obs if (c, o["cutoff"]) in both] for c, obs in ga.items()}
            gb = {c: [o for o in obs if (c, o["cutoff"]) in both] for c, obs in gb.items()}
            ga, gb = {c: v for c, v in ga.items() if v}, {c: v for c, v in gb.items() if v}
            shared = sorted(set(ga) & set(gb))
            if not shared:
                continue
            is_count = all("den" not in o for obs in ga.values() for o in obs)
            point = _avg(ga, shared, is_count)[1], _avg(gb, shared, is_count)[1]
            rng = random.Random(f"{seed}-{work}-{metric}-pair")
            diffs = []
            for _ in range(reps):
                draw = [shared[rng.randrange(len(shared))] for _ in shared]
                da = {f"{c}#{i}": ga[c] for i, c in enumerate(draw)}
                db = {f"{c}#{i}": gb[c] for i, c in enumerate(draw)}
                va, vb = _avg(da, list(da), is_count)[1], _avg(db, list(db), is_count)[1]
                if va is not None and vb is not None:
                    diffs.append(va - vb)
            out.append({"key": f"{metric}-{work}-avg-{a}-minus-{b}",
                        "value": None if None in point else point[0] - point[1], "characters": len(shared),
                        "ci95": [_pct(diffs, 0.025), _pct(diffs, 0.975)] if diffs else None,
                        "unit": "character (paired, trajectory kept)", "reps": reps})
    return out


def _fmt(r: dict) -> str:
    v = r["value"]
    s = "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
    if "den" in r and r["den"]:
        s += f" ({r['num']:g}/{r['den']:g})"
    if r.get("ci95"):
        s += f", 95% CI [{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}]"
    if "zero_event_upper95" in r:
        s += f", 0 events: upper 95% bound {r['zero_event_upper95']:.3f}"
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pair", action="append", default=[], metavar="A:B")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "eval" / "paper_numbers.json")
    a = ap.parse_args()
    res = compute(load(a.inputs), a.reps, a.seed, [tuple(p.split(":")) for p in a.pair])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"inputs": [str(p) for p in a.inputs], "reps": a.reps, "seed": a.seed,
                                 "results": res}, indent=1), encoding="utf-8")
    cmd = "python scripts/eval/paper_numbers.py " + " ".join(str(p) for p in a.inputs)
    for r in res:
        print(f"| {r['key']} | {_fmt(r)} | `{cmd}` |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
