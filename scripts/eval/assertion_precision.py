"""[32] Audited assertion precision: sample what a character page asserts, with what it cites. $0.

Run:  .venv/Scripts/python.exe scripts/eval/assertion_precision.py <series> [--upto 5] [--n 40] [--seed 0]
      ... --population all    sample every surface (prose atoms, uncited lines, quotes, scenes) from
                              assertion_inventory.py's output instead of cited lines only; → <s>_v<t>_all.jsonl

Every structured line a reader sees on `dist/<series>/wiki/vNN/character/*.md` that carries a
citation (<sub>[...](...#nw-vNN-cNN-pNNNN)</sub>) is one assertion: an Overview field value
(each ';'-separated value separately), a "previously:" history value, an Abilities/Goals bullet,
an Affiliation, a Relationship bullet. Prose paragraphs are not sampled here (their citations
cover whole sections; `wiki audit eval`'s citation entailment covers them).

A seeded random sample of N is written to docs/eval/precision/<series>_v<t>.jsonl with the cited
paragraphs' full text and a local-model pre-screen (`eval_judge`, routed to local Ollama only).
The pre-screen is NOT the verdict: a person reads every sampled row and fills `human` with
"supported" / "unsupported" / "partial" (and a note). Precision = supported / N, reported with a
Wilson 95% interval by `--summarize`, which reads the human column only.

Why this exists: the project's earlier "precision 100%" was selected-control precision (no
hand-listed negative fact on a page), not precision over what pages assert (MEASUREMENTS §48).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from datetime import date
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel  # noqa: E402

from narrativewiki import paths, provenance  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.llm.client import LLMClient, LLMError  # noqa: E402

_ANCHOR = re.compile(r"#nw-v(\d+)-c(\d+)-p(\d+)")
_SUB = re.compile(r"\s*<sub>(.*?)</sub>")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _plain(md: str) -> str:
    text = _LINK.sub(r"\1", md)
    return re.sub(r"[*_`]+", "", text).strip(" -;")


def assertions(page_md: str, entity: str) -> list[dict]:
    """Every cited structured assertion on one rendered page."""
    body = page_md.split("\n---\n", 1)[-1] if page_md.startswith("---") else page_md
    out, section, field, sub = [], "", "", ""
    for line in body.splitlines():
        if line.startswith("## "):
            section, sub = line[3:].strip(), ""
            continue
        if line.startswith("### "):
            sub = _plain(line[4:]).split(" · ")[0].split(" — ")[0].strip()
            continue
        if "<sub>" not in line or line.lstrip().startswith("_Sources"):
            continue
        m = re.match(r"\s*- \*\*(.+?):\*\*\s*(.*)", line)
        if m:
            field, rest = m.group(1), m.group(2)
        elif line.lstrip().startswith("_previously:"):
            rest = line.strip()
        else:
            rest = line.strip()
        # One assertion per cited segment ("a <sub>..</sub>; b <sub>..</sub>").
        pos = 0
        for s in _SUB.finditer(rest):
            value = _plain(rest[pos:s.start()])
            pos = s.end()
            ids = [f"v{int(v):02d}:c{int(c):02d}:p{int(p):04d}" for v, c, p in _ANCHOR.findall(s.group(1))]
            if value and ids:
                label = field if m or rest.startswith("_previously") else (sub or section)
                out.append({"entity": entity, "section": section, "label": label, "value": value, "evidence": ids})
    return out


class Screen(BaseModel):
    supported: bool
    reason: str


_SYSTEM = ("You check a wiki's claims against the passages it cites. Answer only from the passages. "
           "A claim is supported when the passages state it or directly show it about the named "
           "character; otherwise it is not.")


def screen(client: LLMClient, row: dict, texts: dict[str, str]) -> Screen:
    passages = "\n\n".join(f"[{pid}] {texts.get(pid, '(missing)')}" for pid in row["evidence"][:6])
    prompt = (f"Character: {row['entity']}\nWiki line ({row['section']} / {row['label']}): {row['value']}\n\n"
              f"Cited passages:\n{passages}\n\nDo the passages support this line about {row['entity']}? "
              'Respond with JSON only: {"supported": true/false, "reason": "one sentence"}')
    return client.complete_json("eval_judge", prompt, Screen, system=_SYSTEM)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def summarize(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    judged = [r for r in rows if r.get("human")]
    ok = sum(r["human"] == "supported" for r in judged)
    agree = sum((r["human"] == "supported") == r["screen"]["supported"] for r in judged if r["screen"])
    print(f"{path.name}: {ok}/{len(judged)} supported (hand-read), Wilson 95% {wilson(ok, len(judged))}; "
          f"{len(rows) - len(judged)} not yet read; local screen agreed on {agree}/{len(judged)}")
    for surface in sorted({r["surface"] for r in judged if "surface" in r}):  # --population all rows
        rs = [r for r in judged if r.get("surface") == surface]
        print(f"  {surface}: {sum(r['human'] == 'supported' for r in rs)}/{len(rs)}")
    for r in judged:
        if r["human"] != "supported":
            print(f"  {r['human'].upper()}: {r['entity']} | {r['label']}: {r['value']} | {r.get('note', '')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=5)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--population", choices=["cited", "all"], default="cited")
    ap.add_argument("--resample-from", type=Path, metavar="OLD_SAMPLE",
                    help="C17: keep OLD_SAMPLE's rows (and labels) whose atom is still in the pool; top up "
                         "with seeded draws from the rest. The pool only lost atoms, so this is a uniform sample.")
    args = ap.parse_args()
    suffix = "_all" if args.population == "all" else ""
    out = paths.DOCS_DIR / "eval" / "precision" / f"{args.series}_v{args.upto}{suffix}.jsonl"
    if args.summarize:
        summarize(out)
        return

    settings = load_settings(args.series)
    paths.set_active_series(args.series)
    pool: list[dict] = []
    if args.population == "all":
        inv = paths.DOCS_DIR / "eval" / "inventory" / f"{args.series}_v{args.upto}.jsonl"
        pool = [json.loads(l) for l in inv.read_text(encoding="utf-8").splitlines() if l.strip()]
    for page in (sorted((paths.wiki_cutoff_dir(args.upto) / "character").glob("*.md"))
                 if args.population == "cited" else []):
        md = page.read_text(encoding="utf-8")
        title = re.search(r"^# (.+)$", md, re.M)
        pool += assertions(md, title.group(1).strip() if title else page.stem)
    if args.resample_from:
        key = lambda r: (r["page"], r.get("surface"), r.get("section"), r["value"])
        in_pool = {key(r) for r in pool}
        old = [json.loads(l) for l in args.resample_from.read_text(encoding="utf-8").splitlines() if l.strip()]
        kept = [r for r in old if key(r) in in_pool]
        taken = {key(r) for r in kept}
        rest = [r for r in pool if key(r) not in taken]
        sample = kept + random.Random(args.seed).sample(rest, min(args.n - len(kept), len(rest)))
    else:
        sample = random.Random(args.seed).sample(pool, min(args.n, len(pool)))

    need = {pid for r in sample for pid in r["evidence"][:6]}
    texts: dict[str, str] = {}
    for v in sorted({int(pid[1:3]) for pid in need}):
        for line in paths.parsed_volume(v).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["para_id"] in need:
                texts[rec["para_id"]] = rec["text"]

    # A file that already holds read verdicts belongs to an earlier build; never overwrite it.
    if out.is_file() and any(json.loads(line).get("human") for line in out.read_text(encoding="utf-8").splitlines()):
        out = out.with_name(f"{out.stem}_{date.today():%Y%m%d}.jsonl")
    run = provenance.start_run(args.series, "assertion_precision", scope=f"v{args.upto}", volumes=[args.upto],
                               volume_scope=args.upto, stage_keys=[], settings=settings)
    client = LLMClient(settings, run=run, volume_scope=args.upto)
    out.parent.mkdir(parents=True, exist_ok=True)
    screen_up = True
    with out.open("w", encoding="utf-8") as fh:
        for r in sample:
            if r.get("human"):  # C17: a row carried over by --resample-from keeps its verdict and screen
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue
            sc = None
            if screen_up and r["evidence"]:  # optional pre-screen; uncited rows are read from the source
                try:
                    sc = screen(client, r, texts).model_dump()
                except LLMError as exc:  # Ollama down: stop asking, keep sampling
                    screen_up = False
                    print(f"local screen unavailable, sample written without it: {exc}")
            r |= {"passages": {pid: texts.get(pid) for pid in r["evidence"][:6]},
                  "screen": sc, "human": None, "note": ""}
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    run.finish("ok")
    print(f"pool {len(pool)} assertions; sampled {len(sample)} -> {paths.relative(out)}; "
          f"screen says supported {sum(bool(r['screen'] and r['screen']['supported']) for r in sample)}/{len(sample)}")


if __name__ == "__main__":
    main()
