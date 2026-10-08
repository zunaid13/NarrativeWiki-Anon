"""[33] M1 recall audit: one gold fact, every labelled cell, the sentences that bear on it. $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/recall_evidence.py "<claim substring>" "<regex>" [--pages RX] [--diff] [-n 2]
      ... --set yes|no <series>:<t> [<series>:<t> ...]     relabel that fact in those cells

For each `docs/eval/recall_hand/<series>_v<t>.json` holding the fact, prints its label and the
whole-tree sentences (every page of `dist/<series>/wiki/v<t>/` except `source/`) matching the regex.
`--diff` keeps only the rows where label and evidence disagree (yes with no match, no with a match)
-- the rows a reader then judges. This is how the 2026-10-02 fairness audit was run: the first pass
carried a label forward from t-1 when a lexically close sentence still existed, which both kept
unsupported "yes" and never re-read a paraphrased "no" (docs/eval/recall_hand/anne.provenance.md).
A regex match is a candidate, not a verdict.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))
from m4_tree import sentences  # noqa: E402

HAND = ROOT / "docs" / "eval" / "recall_hand"


def cells(sub: str):
    for f in sorted(HAND.glob("*_v*.json")):
        series, t = f.stem.rsplit("_v", 1)
        labels = json.loads(f.read_text(encoding="utf-8"))
        keys = [k for k in labels if sub in k]
        if len(keys) == 1:
            yield f, series, int(t), labels, keys[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("claim")
    ap.add_argument("regex", nargs="?", default="")
    ap.add_argument("--pages", default="")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("-n", type=int, default=2)
    ap.add_argument("--set", nargs="+", metavar=("LABEL", "CELL"))
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.set:
        label, wanted = a.set[0], set(a.set[1:])
        assert label in ("yes", "no"), label
        for f, series, t, labels, key in cells(a.claim):
            if f"{series}:{t}" in wanted:
                old, labels[key] = labels[key], label
                f.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"{series} t={t} {old} -> {label}  {sum(v == 'yes' for v in labels.values())}/{len(labels)}")
        return
    rx, pages = re.compile(a.regex, re.I), re.compile(a.pages, re.I) if a.pages else None
    for _, series, t, labels, key in cells(a.claim):
        hits = []
        for p in sorted((ROOT / "dist" / series / "wiki" / f"v{t:02d}").rglob("*.md")):
            if p.parent.name == "source" or (pages and not pages.search(p.name)):
                continue
            text = re.sub(r"\]\([^)]*\)", "]", p.read_text(encoding="utf-8"))
            hits += [f"{p.stem}: {s[:230]}" for s in sentences(text) if rx.search(s)]
        if a.diff and (labels[key] == "yes") == bool(hits):
            continue
        print(f"{series:8s} t={t} {labels[key]:3s} | " + ("  ##  ".join(list(dict.fromkeys(hits))[:a.n]) or "-"))


if __name__ == "__main__":
    main()
