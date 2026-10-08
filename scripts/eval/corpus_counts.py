"""[32] Corpus counts for paper table T1 (plan 0014 S05): paragraphs, words, chapters. $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/corpus_counts.py anne oz [--upto 5]

Reads only `data/<series>/01_parsed/vNN.jsonl` (the ingested source text, never a system output).
Words are whitespace tokens of each paragraph's text; chapters are distinct `chapter_idx` per
volume; paragraphs are parsed records. Prints per volume and the 1..upto total.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def counts(series: str, vol: int) -> tuple[int, int, int]:
    path = ROOT / "data" / series / "01_parsed" / f"v{vol:02d}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(rows), sum(len(r["text"].split()) for r in rows), len({r["chapter_idx"] for r in rows})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series", nargs="+")
    ap.add_argument("--upto", type=int, default=5)
    args = ap.parse_args()
    for s in args.series:
        per = [counts(s, v) for v in range(1, args.upto + 1)]
        for v, (p, w, c) in enumerate(per, 1):
            print(f"{s} v{v:02d}: {p} paragraphs, {w} words, {c} chapters")
        p, w, c = (sum(x[i] for x in per) for i in range(3))
        print(f"{s} v01-v{args.upto:02d}: {p} paragraphs, {w} words, {c} chapters")


if __name__ == "__main__":
    main()
