"""[32] Assertion inventory: every assertion a cutoff's wiki shows a reader, atomized. $0 (local).

Run:  .venv/Scripts/python.exe scripts/eval/assertion_inventory.py <series> [--upto 5]
      ... --recall-sheet 12 [--seed 0]     sample prose units for the atomizer recall check
      ... --recall                         score a filled recall sheet

Plan 0014 S06 / plan 0013 §3.2. `assertion_precision.py` samples only cited structured lines; this
enumerates every surface of `dist/<series>/wiki/vNN/` (character, codex, relationship, timeline and
index pages) so precision (M3) and output amount (N) are over everything a reader sees:

  structured  a cited line (the sampler's own `assertions()`, one per cited segment)
  uncited     a field or list line with no citation ("Status: alive (assumed)", "Members: A, B")
  related     a "Related" line (shared-scene count)
  quote       a quotation and the speaker it is attributed to
  prose       a prose paragraph (background, personality, history, codex summary, relationship blurb)
  scene       a scene summary (timeline entries, "Shared scenes" blockquotes)

prose and scene units are atomized sentence by sentence into name-only, third-person facts
(NarrativeFactScore's decomposition, arXiv 2501.09993 E.3), with the paragraph as context so
pronouns resolve. The atomizer routes to `eval_atomize` (local Ollama only). Its omissions are
measured, not assumed: `--recall-sheet` samples prose units; a reader lists the unit's facts by
hand in `hand` ([{"fact": ..., "covered": true/false}], covered = some atom states it) and
`--recall` reports covered / listed (`F-atomizer-recall`).

Output: docs/eval/inventory/<series>_v<t>.jsonl, one row per atomic assertion:
  {page, surface, entity, section, label, value, evidence: [para_id], chapter, unit, atom_of}
`unit` is the source text of a prose/scene unit (null otherwise); `atom_of` its unit index.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import BaseModel  # noqa: E402

from assertion_precision import _ANCHOR, _plain, assertions  # noqa: E402
from narrativewiki import paths, provenance  # noqa: E402
from narrativewiki.config import apply_set_overrides, load_settings  # noqa: E402
from narrativewiki.llm.client import LLMClient, LLMError  # noqa: E402
from narrativewiki.llm.parallel import map_calls, workers_for  # noqa: E402

_SENT = re.compile(r"(?<=[.!?])[\"”’]?\s+(?=[A-Z\"“])")
_EMPTY = {"_Not yet known._", "_None known yet._", "No notable quotes yet.", "_No notable quotes yet._"}


def _ids(text: str) -> list[str]:
    return [f"v{int(v):02d}:c{int(c):02d}:p{int(p):04d}" for v, c, p in _ANCHOR.findall(text)]


def units(md: str, page: str, kind: str) -> list[dict]:
    """Every uncited assertion unit on one page; cited lines come from `assertions()`."""
    body = md.split("\n---\n", 1)[-1] if md.startswith("---") else md
    m = re.search(r"^# (.+)$", body, re.M)
    title = m.group(1).strip() if m else page
    out: list[dict] = []
    section, sub = "", ""
    pending: list[dict] = []          # prose waiting for its "_Sources:" line
    quote: list[str] = []

    def unit(surface: str, value: str, entity: str = "", label: str = "", evidence=(), is_unit=False,
             where: str = ""):
        value = _plain(value)
        if value:
            out.append({"page": page, "surface": surface,
                        "entity": entity or (section if kind == "codex" else title), "section": section,
                        "label": label or sub or section, "value": value, "evidence": list(evidence),
                        "chapter": where or (section if kind == "timeline" else None),
                        "unit": value if is_unit else None})
            return out[-1]

    def close_quote(attrib: str):
        text = " ".join(q for q in quote).strip()
        quote.clear()
        if not text:
            return
        ev = _ids(attrib)
        if ev:  # a quotation: "— Speaker — [v1 ch.7 P37](...)"
            speaker = _plain(attrib.split(" — ")[0].lstrip("— "))
            unit("quote", f"{speaker} said: {text}", entity=speaker, label="Quote", evidence=ev)
        else:   # a scene summary: "— v1 · Chapter 5"
            where = _plain(attrib).lstrip("— ")
            u = unit("scene", text, label=where, is_unit=True, where=where)
            pending.clear()   # [34] its "_Sources:" paragraph, when the build cites scenes, follows
            if u:
                pending.append(u)

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            if "<sub>" in line:  # C17: a cited quote line ("> text <sub>[cite]</sub>") is assertions()'s;
                continue          # left here, baseline quotes piled into one speakerless "scene" unit
            t = line.lstrip("> ").strip()
            if t.startswith("—"):
                close_quote(t)
            elif t:
                quote.append(t)
            continue
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            section, sub, pending = _plain(re.sub(r"\{#.*?\}", "", line[3:])), "", []
            continue
        if line.startswith("### "):
            sub, pending = _plain(line[4:]).split(" · ")[0].strip(), []
            continue
        if line.startswith("_Sources"):
            for p in pending:
                p["evidence"] = _ids(line)
            pending = []
            continue
        if not line or line in _EMPTY or line.startswith("---"):
            continue
        if "<sub>" in line:           # cited: assertions() owns it
            continue
        if kind == "character" and not section:   # category links under the title
            unit("uncited", line, label="Categories")
            continue
        if kind == "index" or section == "Appearances":
            unit("uncited", line, label=section or "index")
            continue
        if section == "Related":
            unit("related", line)
            continue
        m = re.match(r"-\s*\*\*(.+?):\*\*\s*(.*)", line) or re.match(r"\*\*(.+?):\*\*\s*(.*)", line)
        if m:                         # "- **Status:** alive _(assumed)_", "**Members:** A, B"
            for v in (m.group(2).split(", ") if kind == "codex" else [m.group(2)]):
                unit("uncited", v, label=m.group(1))
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", line):   # "**Volume 1**" sub-header
            continue
        m = re.match(r"\*\*(.+?)\*\*\s+—\s+(.*)", line)
        if m and kind == "timeline":  # "**A, B** — summary"
            unit("scene", m.group(2), entity=_plain(m.group(1)), is_unit=True)
            continue
        if line.startswith("- "):
            unit("uncited", line[2:])
            continue
        u = unit("prose", line, is_unit=True)
        if u:
            pending.append(u)
    close_quote("")
    return out


class Atoms(BaseModel):
    facts: list[str]


_SYSTEM = ("You split text from a fiction wiki into atomic facts. An atomic fact states exactly one "
           "thing. Write each in the third person using names only: replace every pronoun and "
           "description (he, she, the girl, the merchant) with the name it refers to in the context. "
           "Keep the sentence's modality on the fact it qualifies: hedges and attributions "
           "('claims', 'suspects', 'may', 'is said to'), appearances ('appears as', 'looks'), and "
           "purposes ('to increase' is an aim, not a result). Never add a fact the sentence does "
           "not state.")


def _sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENT.split(paragraph) if s.strip()]


def _atomize_sentence(client: LLMClient, subject: str, paragraph: str, sent: str) -> list[str]:
    prompt = (f"Wiki page subject: {subject}\nContext paragraph: {paragraph}\n\n"
              f"Sentence: {sent}\n\nList the atomic facts expressed in this sentence only. "
              'Respond with JSON only: {"facts": ["...", "..."]}')
    try:
        return [f.strip() for f in client.complete_json("eval_atomize", prompt, Atoms, system=_SYSTEM).facts
                if f.strip()]
    except LLMError:  # 2026-10-02: the local model returned unparsable JSON three times for one sentence
        print(f"atomizer gave no valid answer; sentence kept whole: {sent[:80]!r}", file=sys.stderr)
        FALLBACKS.append(sent)  # (anne@p2 t=2). The sentence stays in the inventory as one assertion.
        return [sent]


def atomize(client: LLMClient, subject: str, paragraph: str) -> list[list[str]]:
    """Atomic facts per sentence of one prose/scene paragraph."""
    sentences = _sentences(paragraph)
    SENTENCES[0] += len(sentences)
    return [_atomize_sentence(client, subject, paragraph, s) for s in sentences]


def atomize_all(client: LLMClient, units: list[tuple[str, str]]) -> list[list[list[str]]]:
    """[34] `atomize` over every (subject, paragraph) unit, the sentences `concurrency` at a time
    (`eval_atomize`'s profile; the same prompts, results in the same order as the serial loop)."""
    jobs = [(i, subject, paragraph, s) for i, (subject, paragraph) in enumerate(units) for s in _sentences(paragraph)]
    SENTENCES[0] += len(jobs)
    answers = map_calls(lambda j: _atomize_sentence(client, j[1], j[2], j[3]), jobs,
                        workers_for(client, "eval_atomize"))
    out: list[list[list[str]]] = [[] for _ in units]
    for (i, *_rest), atoms in zip(jobs, answers):
        out[i].append(atoms)
    return out


def wait_for_gpu(settings, need_mib: int = 11000, every_s: int = 300) -> None:
    """[34] The parallel atomizer needs ~13 GB of a 16 GB card. Before loading it, wait until that
    much is free (a game or another model holding the card would push layers onto the CPU and slow
    both), unless the model is already loaded. No NVIDIA tool: no wait."""
    import subprocess
    import time
    import urllib.request

    model = settings.resolve_role("eval_atomize").model
    while True:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5) as r:
                if any(m.get("name", "").startswith(model) for m in json.load(r).get("models", [])):
                    return
            free = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                      capture_output=True, text=True, check=True).stdout.split()[0])
        except (OSError, ValueError, IndexError, subprocess.CalledProcessError):
            return
        if free >= need_mib:
            return
        print(f"waiting for the GPU: {free} MiB free, {need_mib} needed; next check in {every_s // 60} min",
              file=sys.stderr, flush=True)
        time.sleep(every_s)


# [34] OPEN_GAPS G11: with the local server down every sentence fell back whole (113 on Oz t=1) and
# the script exited 0. Fallbacks are counted; more than MAX_FALLBACK_SHARE of the sentences fails
# the run before anything is written, and the eval role is probed once before any work.
SENTENCES, FALLBACKS = [0], []
MAX_FALLBACK_SHARE = 0.01


def future_citations(rows: list[dict], upto: int) -> list[dict]:
    """Assertions on a cutoff-t page that cite a paragraph from after t: a spoiler by construction."""
    return [r for r in rows if any(int(pid[1:3]) > upto for pid in r["evidence"])]


def inventory(series: str, upto: int, client: LLMClient | None) -> list[dict]:
    wiki = paths.wiki_cutoff_dir(upto)
    rows: list[dict] = []
    pages = [p for p in sorted(wiki.rglob("*.md")) if p.parent.name != "source"]
    for p in pages:
        md = p.read_text(encoding="utf-8")
        kind = p.parent.name if p.parent != wiki else "index"
        rel = p.relative_to(wiki).as_posix()
        title = re.search(r"^# (.+)$", md, re.M)
        for a in assertions(md, title.group(1).strip() if title else p.stem):
            rows.append({"page": rel, "surface": "structured", **a, "chapter": None, "unit": None})
        rows += units(md, rel, kind)
    if client is None:
        return rows
    unit_rows = [r for r in rows if r["unit"] is not None]
    atoms_by_unit = iter(atomize_all(client, [(r["entity"], r["unit"]) for r in unit_rows]))
    out, n_units = [], 0
    for r in rows:
        if r["unit"] is None:
            out.append(r | {"atom_of": None})
            continue
        for sent_atoms in next(atoms_by_unit):
            for a in sent_atoms:
                out.append(r | {"value": a, "atom_of": n_units})
        n_units += 1
    return out


def recall_sheet(rows: list[dict], k: int, seed: int, path: Path) -> None:
    by_unit: dict[int, list[dict]] = {}
    for r in rows:
        if r.get("atom_of") is not None:
            by_unit.setdefault(r["atom_of"], []).append(r)
    pick = random.Random(seed).sample(sorted(by_unit), min(k, len(by_unit)))
    with path.open("w", encoding="utf-8") as fh:
        for u in pick:
            r = by_unit[u][0]
            fh.write(json.dumps({"page": r["page"], "surface": r["surface"], "entity": r["entity"],
                                 "unit": r["unit"], "atoms": [a["value"] for a in by_unit[u]],
                                 "hand": [], "reader": None, "note": ""}, ensure_ascii=False) + "\n")
    print(f"recall sheet: {len(pick)} units -> {paths.relative(path)}")


def score_recall(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    read = [r for r in rows if r["hand"]]
    listed = sum(len(r["hand"]) for r in read)
    covered = sum(h["covered"] for r in read for h in r["hand"])
    print(f"{path.name}: atomizer recall {covered}/{listed} hand-listed facts over {len(read)} units "
          f"({len(rows) - len(read)} units unread); readers {sorted({r['reader'] for r in read})}")
    for r in read:
        for h in r["hand"]:
            if not h["covered"]:
                print(f"  MISSED ({r['page']}): {h['fact']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=5)
    ap.add_argument("--recall-sheet", type=int, metavar="K")
    ap.add_argument("--recall", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-atomize", action="store_true", help="count units only, no model calls")
    ap.add_argument("--out", help="[34] write the inventory here instead (an equivalence check must not "
                    "overwrite a scored cell's file)")
    ap.add_argument("--set", action="append", default=[], help="[34] KEY=VALUE settings override, as `wiki`")
    args = ap.parse_args()
    outdir = paths.DOCS_DIR / "eval" / "inventory"
    out = Path(args.out) if args.out else outdir / f"{args.series}_v{args.upto}.jsonl"
    sheet = outdir / f"{args.series}_v{args.upto}_recall.jsonl"
    if args.recall:
        score_recall(sheet)
        return

    settings = load_settings(args.series)
    apply_set_overrides(settings, args.set)
    paths.set_active_series(args.series)
    run = client = None
    if not args.no_atomize:
        run = provenance.start_run(args.series, "assertion_inventory", scope=f"v{args.upto}", volumes=[args.upto],
                                   volume_scope=args.upto, stage_keys=[], settings=settings)
        client = LLMClient(settings, run=run, volume_scope=args.upto)
        wait_for_gpu(settings)
        try:  # [34] G11: the atomizer is local-only; an unreachable server must stop the run, not split nothing
            client.complete_json("eval_atomize", 'Sentence: Anne smiled.\n\nRespond with JSON only: {"facts": ["..."]}',
                                 Atoms, system=_SYSTEM)
        except LLMError as exc:
            run.finish("failed", error=str(exc))
            sys.exit(f"eval_atomize does not answer ({exc}); start the local model and rerun")
    rows = inventory(args.series, args.upto, client)
    if client is not None and len(FALLBACKS) > max(2, MAX_FALLBACK_SHARE * SENTENCES[0]):
        run.finish("failed", error=f"{len(FALLBACKS)} of {SENTENCES[0]} sentences kept whole")
        sys.exit(f"{len(FALLBACKS)} of {SENTENCES[0]} sentences kept whole (atomizer failures); nothing written")
    if run:
        run.finish("ok")
    for r in future_citations(rows, args.upto):
        print(f"FUTURE CITATION on a v{args.upto:02d} page: {r['page']} | {r['value'][:80]} | {r['evidence']}")
    by = Counter(r["surface"] for r in rows)
    print(f"{args.series} t={args.upto}: {len(rows)} {'assertions' if client else 'units'} "
          + " · ".join(f"{k} {v}" for k, v in by.most_common()))
    if client is None:
        return
    outdir.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"-> {paths.relative(out)}")
    if args.recall_sheet:
        if sheet.is_file() and any(json.loads(l)["hand"] for l in sheet.read_text(encoding="utf-8").splitlines()):
            sys.exit(f"{paths.relative(sheet)} already holds hand-listed facts; not overwriting")
        recall_sheet(rows, args.recall_sheet, args.seed, sheet)


if __name__ == "__main__":
    main()
