"""Hand audit of the E2 leak screen's negatives (plan 0013 §3.3). $0, no model calls.

Run: .venv/Scripts/python.exe scripts/eval/leak_screen_audit.py sheet anne --upto 1-5
     .venv/Scripts/python.exe scripts/eval/leak_screen_audit.py summarize anne --upto 1-5
`sheet` prints, per cutoff and character, the page with citation markup stripped and every
gold row pipeline_page_leak.py scored on it (docs/eval/pipeline_leak_<series>_v<t>.json).
A reader writes one verdict per row to docs/eval/leak_audit/<series>.jsonl:
  {"t", "character", "claim", "page_sha", "verdict": "stated"|"implied"|"absent", "witness", "reader"}
`summarize` joins verdicts to rows and reports, per t, the screen's sensitivity (rows a reader
found on the page that the screen also flagged) and every future fact a reader found (a leak).
A verdict whose page_sha no longer matches the page on disk is stale and is not counted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from narrativewiki import paths
from narrativewiki.probe.parametric import gold_pages


def strip(md: str) -> str:
    md = re.sub(r"<sub>.*?</sub>", "", md)
    md = re.sub(r"^_Sources?:.*$", "", md, flags=re.M)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    return re.sub(r"\n{3,}", "\n\n", md)


def page_of(series: str, character: str, t: int) -> list[Path]:
    """[33] The system's page(s) for a gold character (probe/parametric.py::gold_pages)."""
    return gold_pages(series, character, paths.wiki_cutoff_dir(t))


def sha(pages: list[Path]) -> str:
    # One page hashes exactly as before, so verdicts recorded against a single page stay valid.
    return hashlib.sha256(b"".join(p.read_bytes() for p in pages)).hexdigest()[:16]


def rows_at(series: str, t: int) -> list[dict]:
    return json.loads((paths.DOCS_DIR / "eval" / f"pipeline_leak_{series}_v{t}.json").read_text(encoding="utf-8"))["rows"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sheet", "summarize"])
    ap.add_argument("series")
    ap.add_argument("--upto", required=True, help="cutoff range, e.g. 1-5")
    args = ap.parse_args()
    lo, _, hi = args.upto.partition("-")
    cutoffs = range(int(lo), int(hi or lo) + 1)
    paths.set_active_series(args.series)
    verdict_file = paths.DOCS_DIR / "eval" / "leak_audit" / f"{args.series}.jsonl"

    if args.mode == "sheet":
        for t in cutoffs:
            by_char: dict[str, list[dict]] = {}
            for r in rows_at(args.series, t):
                by_char.setdefault(r["character"], []).append(r)
            for character, rows in by_char.items():
                pages = page_of(args.series, character, t)
                print(f"\n===== t={t} | {character} | page_sha={sha(pages)} =====")
                print(strip("\n\n".join(p.read_text(encoding="utf-8") for p in pages)))
                print("----- rows -----")
                for r in rows:
                    flag = "SCREEN+" if r["supported"] or r["lexical"] else "screen-"
                    print(f"[{r['kind']}/{flag} {r['score']:.2f}] {r['claim']}")
        return

    verdicts = {}
    if verdict_file.is_file():
        for line in verdict_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                v = json.loads(line)
                verdicts[(v["t"], v["character"], v["claim"])] = v
    for t in cutoffs:
        tally = {k: {"read": 0, "found": 0, "found_flagged": 0, "missing": 0, "stale": 0} for k in ("control", "future")}
        leaks = []
        for r in rows_at(args.series, t):
            c = tally[r["kind"]]
            v = verdicts.get((t, r["character"], r["claim"]))
            if v is None:
                c["missing"] += 1
                continue
            if v["page_sha"] != sha(page_of(args.series, r["character"], t)):
                c["stale"] += 1
                continue
            c["read"] += 1
            if v["verdict"] != "absent":
                c["found"] += 1
                c["found_flagged"] += bool(r["supported"] or r["lexical"])
                if r["kind"] == "future":
                    leaks.append(f"{r['character']}: {r['claim']} <- {v['witness']!r}")
        print(f"t={t} " + " | ".join(
            f"{k}: read {c['read']}, on page {c['found']}, screen caught {c['found_flagged']}/{c['found']}"
            + (f", unread {c['missing']}" if c["missing"] else "") + (f", stale {c['stale']}" if c["stale"] else "")
            for k, c in tally.items()))
        for leak in leaks:
            print("  LEAK", leak)


if __name__ == "__main__":
    main()
