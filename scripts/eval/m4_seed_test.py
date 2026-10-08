"""[33] Plan 0015 step 4 — how many seeded disclosures does the M4 candidate search retrieve? $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/m4_seed_test.py anne [--last-vol 5]
      -> docs/eval/m4_seeded/<series>_sensitivity.json, and a table on stdout

M4 reads only the sentences `m4_tree.classify` retrieves, so a disclosure it does not retrieve is
never read. This measures that retrieval on sentences whose answer is known: for every later gold
fact at every cutoff (the same fact-by-cutoff units as M4) a sentence that discloses the fact is
presented to `classify` exactly as a wiki sentence would be, and we count how often it comes back
as a candidate. Seeds are in docs/eval/m4_seeded/<series>_seeds.yaml (hand-written, one natural
paraphrase `p` and one oblique sentence `o` per fact, `{N}` for the subject).

Variants (what is seeded, how the subject is referred to, where the sentence stands):
  direct      the gold claim verbatim            name      the character's own page
  para        the natural paraphrase             name / pronoun    own page / another page
  oblique     the sentence that only implies it  name / pronoun    own page / another page
A pronoun on the character's own page is the normal form of wiki prose; a pronoun on another page
is a disclosure with no handle on the subject at all, the hardest case for any name-anchored search.

Controls: every *eligible* gold claim (reveal <= t) is passed the same way, on its own page; the
share that comes back as a candidate for some later fact is the reading load the search creates,
not an error (a candidate is read, not counted).

Nothing here touches a wiki tree, a label or a reported number.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))
from narrativewiki import paths  # noqa: E402
from narrativewiki.probe import parametric as par  # noqa: E402
import m4_tree  # noqa: E402

VARIANTS = [  # (label, seed field, subject form, on own page)
    ("direct, name, own page", "claim", "name", True),
    ("paraphrase, name, own page", "p", "name", True),
    ("paraphrase, name, other page", "p", "name", False),
    ("paraphrase, pronoun, own page", "p", "pronoun", True),
    ("paraphrase, pronoun, other page", "p", "pronoun", False),
    ("oblique, name, own page", "o", "name", True),
    ("oblique, name, other page", "o", "name", False),
    ("oblique, pronoun, own page", "o", "pronoun", True),
    ("oblique, pronoun, other page", "o", "pronoun", False),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--last-vol", type=int, default=5)
    a = ap.parse_args()
    paths.set_active_series(a.series)
    seed_file = paths.DOCS_DIR / "eval" / "m4_seeded" / f"{a.series}_seeds.yaml"
    seeds = yaml.safe_load(seed_file.read_text(encoding="utf-8"))["seeds"]
    gold = yaml.safe_load((paths.DOCS_DIR / "eval" / "parametric" / f"{a.series}.yaml").read_text(encoding="utf-8"))
    later = [(ch, f) for ch, e in gold["characters"].items() for f in par.facts_of(e) if 1 < f["first_vol"] <= a.last_vol]
    assert len(later) == len(seeds), (len(later), len(seeds))
    seed_of = {}
    for (ch, f), s in zip(later, seeds):
        assert f["claim"].startswith(s["c"]), (f["claim"], s["c"])  # the fixture is in gold order
        seed_of[f["claim"]] = s
    hit = defaultdict(Counter)       # variant -> Counter(found / total / kind)
    per_t = defaultdict(lambda: defaultdict(Counter))
    control = Counter()
    missed = defaultdict(list)
    for t in range(1, a.last_vol):
        fr = list(m4_tree.frames(a.series, t, a.last_vol))
        for tree, name, names, name_words, own, claim, reveal, content, kws in fr:
            s = seed_of[claim]
            for label, field, subject, own_page in VARIANTS:
                text = claim if field == "claim" else s[field].replace("{N}", name if subject == "name" else "They")
                if field == "claim" and subject == "pronoun":
                    continue
                kind = m4_tree.classify(text, own_page, names, name_words, content, kws)
                for c in (hit[label], per_t[t][label]):
                    c["total"] += 1
                    c["found"] += bool(kind)
                hit[label][(kind or "none").split(":")[0]] += 1
                if not kind and label in ("paraphrase, pronoun, own page", "paraphrase, name, other page"):
                    missed[label].append({"t": t, "claim": claim, "sentence": text})
        # Controls: eligible claims on their own page, tested against every later fact of that character.
        by_char = defaultdict(list)
        for f in fr:
            by_char[f[1]].append(f)
        for ch, e in gold["characters"].items():
            for f in par.facts_of(e):
                if f["first_vol"] > t:
                    continue
                control["total"] += 1
                control["flagged"] += any(m4_tree.classify(f["claim"], True, x[2], x[3], x[7], x[8]) for x in by_char.get(ch, []))
    out = {"series": a.series, "units": "fact-by-cutoff, t=1..%d" % (a.last_vol - 1), "variants": {}, "per_cutoff": {},
           "control": dict(control), "missed_examples": {k: v[:12] for k, v in missed.items()}}
    print(f"{a.series}: candidate retrieval on seeded disclosures ({hit[VARIANTS[0][0]]['total']} fact-by-cutoff units)")
    print(f"{'variant':34} found/total  rate   by rule")
    for label, *_ in VARIANTS:
        c = hit[label]
        rules = {k: v for k, v in c.items() if k not in ("total", "found")}
        out["variants"][label] = {"found": c["found"], "total": c["total"], "rate": c["found"] / c["total"], "rules": rules}
        print(f"{label:34} {c['found']:3}/{c['total']:<3}     {c['found'] / c['total']:.3f}  {rules}")
    for t in sorted(per_t):
        out["per_cutoff"][t] = {k: [v["found"], v["total"]] for k, v in per_t[t].items()}
    print(f"controls (eligible claims flagged as a candidate for a later fact of the same character): "
          f"{control['flagged']}/{control['total']}")
    dest = paths.DOCS_DIR / "eval" / "m4_seeded" / f"{a.series}_sensitivity.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("->", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
