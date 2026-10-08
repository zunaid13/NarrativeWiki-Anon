"""Read only the blinded reader HTML and record an explicitly AI-authored review.

No provider calls, answer-key reads, system identification, or prior-label reads.
Labels are supplied individually by the reviewer; this script never infers them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # moved from scripts/eval/ 2026-10-07: scripts/ is frozen (v2j)
PACKAGE = ROOT / "docs/eval/human_check"
REVIEW = PACKAGE / "codex_review.json"
FILES = {"G": "G_gold.html", "C": "C_citation.html",
         "R": "R_recall.html", "P": "P_precision.html"}
ALLOWED = {"G": {"yes", "partly", "no", "not shown"},
           "C": {"yes", "partly", "no", "none cited"},
           "R": {"yes", "partly", "no"},
           "P": {"yes", "partly", "no", "not shown"}}


def now():
    return datetime.now(timezone(timedelta(hours=6))).isoformat(timespec="seconds")


class Items(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.item = None
        self.block = None
        self.pieces = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("class") == "item":
            self.item = []
        elif self.item is not None and tag in {"h2", "p"}:
            self.block = (tag, attrs.get("class", ""))
            self.pieces = []
        elif self.block and tag == "br":
            self.pieces.append(" ")

    def handle_data(self, data):
        if self.block:
            self.pieces.append(data)

    def handle_endtag(self, tag):
        if self.block and tag == self.block[0]:
            self.item.append({"kind": self.block[1] or self.block[0],
                              "text": " ".join("".join(self.pieces).split())})
            self.block = None
        if tag == "div" and self.item is not None:
            self.items.append(self.item)
            self.item = None


def items(part):
    parser = Items()
    parser.feed((PACKAGE / FILES[part]).read_text(encoding="utf-8"))
    with (PACKAGE / f"{part}_answers.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))[1:]
    assert [x[0]["text"].split()[0] for x in parser.items] == [r[0] for r in rows]
    assert all(sum(b["kind"] == "atom" for b in x) == 1 for x in parser.items)
    return parser.items


def load():
    return json.loads(REVIEW.read_text(encoding="utf-8"))


def save(review):
    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(part, review):
    with (PACKAGE / f"{part}_answers.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        decision = review["parts"][part]["answers"].get(row[0])
        if decision:
            row[1] = decision["answer"]
            if part == "P":
                row[2] = decision["pair"]
            row[-1] = "AI review (Codex): " + decision["reason"]
    with (PACKAGE / f"{part}_answers_codex.csv").open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)


def main():
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    for command in ("start", "finish", "record"):
        p = sub.add_parser(command)
        p.add_argument("part", choices=FILES)
    p = sub.add_parser("view")
    p.add_argument("part", choices=FILES)
    p.add_argument("first", type=int)
    p.add_argument("last", type=int)
    p.add_argument("--dedupe", action="store_true")
    sub.add_parser("verify")
    sub.add_parser("summary")
    args = parser.parse_args()
    if args.cmd == "init":
        assert not REVIEW.exists(), "Review already exists"
        source_files = ["INSTRUCTIONS.md"] + list(FILES.values()) + [f"{p}_answers.csv" for p in FILES]
        review = {"reviewer": "Codex (AI assistant; not a human reader)",
                  "status": "in progress; draft for subsequent human checking",
                  "timezone": "local time",
                  "basis": "Only the text shown in the reader HTML; no novel-memory evidence.",
                  "exposure": "Project map, handover and aggregate measurement ledger were read; no answer key or prior agent annotations were opened. Not an independent human annotation.",
                  "created": now(),
                  "source_sha256": {n: hashlib.sha256((PACKAGE / n).read_bytes()).hexdigest() for n in source_files},
                  "parts": {p: {"start": None, "end": None, "answers": {}} for p in FILES}}
        save(review)
        for part in FILES:
            write_csv(part, review)
        print("Created separate Codex answer files and provenance.")
    elif args.cmd == "view":
        seen = {}
        for blocks in items(args.part)[args.first - 1:args.last]:
            print("\n" + blocks[0]["text"])
            for b in blocks[1:]:
                txt = b["text"]
                if args.dedupe and b["kind"] in {"src", "p"}:
                    if txt in seen:
                        print(f"[{b['kind']}] SAME TEXT as {seen[txt]}")
                        continue
                    seen[txt] = blocks[0]["text"].split()[0]
                print(f"[{b['kind']}] {txt}")
    elif args.cmd in {"start", "finish"}:
        review = load()
        p = review["parts"][args.part]
        if args.cmd == "start":
            assert p["start"] is None
            p["start"] = now()
        else:
            assert len(p["answers"]) == len(items(args.part)), "Part is incomplete"
            assert p["start"] and not p["end"]
            p["end"] = now()
        save(review)
        print(args.part, args.cmd, p["start"] if args.cmd == "start" else p["end"])
    elif args.cmd == "record":
        import sys
        review = load()
        p = review["parts"][args.part]
        assert p["start"] and not p["end"]
        expected = {b[0]["text"].split()[0]: b for b in items(args.part)}
        for line in sys.stdin.read().splitlines():
            if not line.strip():
                continue
            fields = line.split("|", 3 if args.part == "P" else 2)
            assert len(fields) == (4 if args.part == "P" else 3), line
            ident, answer = fields[:2]
            reason = fields[-1]
            assert ident in expected and ident not in p["answers"], ident
            assert answer in ALLOWED[args.part] and reason.strip(), line
            decision = {"answer": answer, "reason": reason, "recorded": now()}
            if args.part == "P":
                pair = fields[2]
                is_pair = any(b["text"].startswith("Relationship (pair) page:") for b in expected[ident])
                assert pair in ({"yes", "no"} if is_pair else {"n-a"}), line
                decision["pair"] = pair
            p["answers"][ident] = decision
        save(review)
        write_csv(args.part, review)
        print(args.part, len(p["answers"]), "recorded")
    elif args.cmd == "verify":
        review = load()
        for name, digest in review["source_sha256"].items():
            assert hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() == digest, name
        for part in FILES:
            with (PACKAGE / f"{part}_answers_codex.csv").open(encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
            with (PACKAGE / f"{part}_answers.csv").open(encoding="utf-8", newline="") as f:
                template = list(csv.reader(f))
            assert rows[0] == template[0]
            assert [r[0] for r in rows] == [r[0] for r in template]
            p = review["parts"][part]
            assert p["start"] and p["end"]
            start, end = (datetime.fromisoformat(p[k]) for k in ("start", "end"))
            assert start.tzinfo and end.tzinfo and start <= end
            expected = {b[0]["text"].split()[0]: b for b in items(part)}
            assert set(p["answers"]) == set(expected)
            assert len(expected) == len(rows) - 1
            for row in rows[1:]:
                assert len(row) == len(rows[0])
                assert row[1] == p["answers"][row[0]]["answer"] in ALLOWED[part]
                assert row[-1] == "AI review (Codex): " + p["answers"][row[0]]["reason"]
                assert p["answers"][row[0]]["reason"].strip()
                recorded = datetime.fromisoformat(p["answers"][row[0]]["recorded"])
                assert recorded.tzinfo and start <= recorded <= end
                if part == "P":
                    assert row[2] == p["answers"][row[0]]["pair"]
                    is_pair = any(b["text"].startswith("Relationship (pair) page:")
                                  for b in expected[row[0]])
                    assert row[2] in ({"yes", "no"} if is_pair else {"n-a"})
            print(part, len(rows) - 1, "complete; IDs, labels, reasons and timestamps verified")
        review["status"] = "complete AI draft; awaits independent human checking"
        save(review)
        print("Source HTML, instructions and blank templates unchanged.")
    elif args.cmd == "summary":
        review = load()
        for part, p in review["parts"].items():
            duration = None
            if p["start"] and p["end"]:
                duration = (datetime.fromisoformat(p["end"])
                            - datetime.fromisoformat(p["start"])).total_seconds()
            print(json.dumps({"part": part, "count": len(p["answers"]),
                              "labels": dict(Counter(a["answer"] for a in p["answers"].values())),
                              "pairs": dict(Counter(a["pair"] for a in p["answers"].values())) if part == "P" else None,
                              "start": p["start"], "end": p["end"],
                              "elapsed_seconds": duration}))


if __name__ == "__main__":
    main()
