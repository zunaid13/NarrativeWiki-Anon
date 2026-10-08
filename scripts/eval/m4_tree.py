"""[33] R6 M4 candidates: where might a cutoff-t wiki disclose a later volume's fact? $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/m4_tree.py <series> --upto <t> [--last-vol 5]
      -> docs/eval/leak_audit/<series>_tree_candidates_v<t>.jsonl

For every gold fact with t < reveal <= last-vol (paper §3 F_t; R6: page or not), search EVERY page of
`dist/<series>/wiki/v<t>/` -- not only the character's -- the way the B5 pilot did (leak_audit/anne_tree.jsonl):
  keyword   a future-only marker (probe/parametric.valid_keywords: absent from the text <= t)
  overlap   a sentence naming the character that shares >= half of the claim's content words
  event     a sentence naming the character with a marriage / death / birth / engagement word
Each candidate is a sentence a reader then judges (stated / implied / absent) into <series>_tree.jsonl.
Names are remapped for a decontaminated series. Candidate recall is not measured: absent means "no
candidate found", which the paper reports with the 3/n upper bound, never as "no leak".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki import paths  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.ingest.decontaminate import remap  # noqa: E402
from narrativewiki.probe import parametric as par  # noqa: E402

# 2026-10-02 (C23): "marri" missed "marry"; courtship and violent-death words added after a book-five
# courtship on a cutoff-4 page ("persuaded her to marry him") was found only by the lexical screen.
_EVENT = re.compile(r"\b(marr[iy]|wed\b|wedding|husband|wife|engag|died|dies|death|dead\b|funeral|"
                    r"baby|born|birth|widow|propos|court(ed|ing|ship)|bride|betroth|passed away|"
                    r"killed|drown)", re.I)
_STOP = set("the and that with from this have into their there they them were what when which while "
            "about after before being other than then over also only more most some such very will would "
            "could should does didn just your said says anne's".split())


def sentences(md: str) -> list[str]:
    text = re.sub(r"<sub>.*?</sub>", " ", md)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_>#`]", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 12]


def words(s: str, drop: set[str]) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in _STOP and w not in drop}


def classify(sentence: str, own_page: bool, names: set[str], name_words: set[str], content: set[str],
             kws: list[str]) -> str | None:
    """How one sentence becomes a candidate for one future fact (None = it does not). The whole search
    is this test over every sentence of the tree; scripts/eval/m4_seed_test.py measures its recall."""
    low = sentence.lower()
    kind = next((f"keyword:{k}" for k in kws if k.lower() in low), None)
    if kind is None and (own_page or any(re.search(rf"\b{re.escape(n)}\b", sentence) for n in names)):
        shared = content & words(sentence, name_words)
        if content and len(shared) * 2 >= len(content):
            kind = f"overlap:{len(shared)}/{len(content)}"
        elif _EVENT.search(sentence):
            kind = "event"
    return kind


def frames(series: str, t: int, last: int):
    """Yield, per future fact at cutoff t, what `classify` needs: (tree, character name, names,
    name_words, own pages, claim, reveal volume, content words, future-only keywords)."""
    settings = load_settings(series)
    dc = settings.series.get("decontaminate") or {}
    emap = dc.get("entity_map") or {}
    source = settings.series.get("gold_from") or dc.get("from_series") or paths.variant_base(series) or series
    gold = yaml.safe_load((paths.DOCS_DIR / "eval" / "parametric" / f"{source}.yaml").read_text(encoding="utf-8"))
    records = []
    for v in range(1, t + 1):
        records += [json.loads(l) for l in paths.parsed_volume(v).read_text(encoding="utf-8").splitlines() if l.strip()]
    keywords = par.valid_keywords(gold, par.volume_text(records), emap, t)
    tree = paths.wiki_cutoff_dir(t)
    for character, entry in gold["characters"].items():
        name = remap(character, emap)
        names = {name, name.split()[0]}
        name_words = {w.lower() for n in names for w in n.split()}
        # On the character's own page(s) a sentence says "he"/"she": it names the character too.
        own = {p.relative_to(tree).as_posix() for p in par.gold_pages(series, name, tree)}
        for fact in par.facts_of(entry):
            if not t < fact["first_vol"] <= last:
                continue
            claim = remap(fact["claim"], emap)
            yield (tree, name, names, name_words, own, claim, fact["first_vol"], words(claim, name_words),
                   keywords.get(fact["claim"], []))


def candidates(series: str, t: int, last: int) -> list[dict]:
    out, sents = [], None
    for tree, name, names, name_words, own, claim, reveal, content, kws in frames(series, t, last):
        if sents is None:
            sents = [(p.relative_to(tree).as_posix(), s) for p in sorted(tree.rglob("*.md"))
                     if p.parent.name != "source" for s in sentences(p.read_text(encoding="utf-8"))]
        hits = []
        for page, s in sents:
            kind = classify(s, page in own, names, name_words, content, kws)
            if kind:
                hits.append({"page": page, "kind": kind, "sentence": s[:400]})
        out.append({"t": t, "character": name, "claim": claim, "reveal": reveal, "hits": hits})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, required=True)
    ap.add_argument("--last-vol", type=int, default=5)
    a = ap.parse_args()
    paths.set_active_series(a.series)
    rows = candidates(a.series, a.upto, a.last_vol)
    out = paths.DOCS_DIR / "eval" / "leak_audit" / f"{a.series}_tree_candidates_v{a.upto}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{a.series} t={a.upto}: {len(rows)} future facts, {sum(bool(r['hits']) for r in rows)} with candidates, "
          f"{sum(len(r['hits']) for r in rows)} candidate sentences -> {paths.relative(out)}")


if __name__ == "__main__":
    main()
