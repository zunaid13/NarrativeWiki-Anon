"""Candidate sentences for every gold fact in a wiki tree, from one stored pattern per fact. $0, no model.

Run:  .venv/Scripts/python.exe docs/eval/recall_hand/recall_patterns.py --validate
      ... --view <series> [--upto 5] [--only-miss]     the candidates a reader then judges
      ... --apply <series> <decisions.json>           write docs/eval/recall_hand/<series>_v<t>.json

Patterns: `anne.fact_patterns.json` (all regexes of a fact must match inside one sentence; an `S:` regex names
the subject and is waived on the fact's own character page). A match is a candidate, never a verdict.
`--validate` runs the patterns over every cell that already has a label and prints how often a fact labelled
"yes" has no candidate: that share is the risk of calling "no" from an empty candidate list.
`--view` prints, per cutoff and fact, the best candidates (own page first), or the nearest sentences by word
overlap when there is none (`--only-miss` prints just those cells).
Lives in docs/ so that it does not touch the frozen scripts/ tree. A decontaminated series is read through
the inverse of its name map (`unmap`): every stand-in in the wiki text and in page addresses is put back to
the original name, so the same patterns and own-page rule apply.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAT = {k: v for k, v in json.loads((HERE / "anne.fact_patterns.json").read_text(encoding="utf-8")).items() if k != "_about"}
STOP = set("the a an and of to in on at for with by from is was were are be been his her their its that this as it he "
           "she they who whom which had has have not but or after before into over while when also very more most".split())


def toks(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in STOP}


def unmap(series: str):
    """text -> text with a renamed series' stand-ins put back (identity for an original series)."""
    cfg = ROOT / "config" / f"series.{series.split('@')[0]}.yaml"
    m = (yaml.safe_load(cfg.read_text(encoding="utf-8")).get("decontaminate") or {}).get("entity_map") if cfg.exists() else None
    if not m:
        return lambda x: x
    inv = {v: k for k, v in m.items()}
    inv.update({slug(v): slug(k) for k, v in m.items()})                 # page addresses: "nell-harcourt"
    rx = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in sorted(inv, key=len, reverse=True)) + r")(?![A-Za-z])")
    return lambda x: rx.sub(lambda g: inv[g.group(1)], x)


def tree(series: str, t: int) -> list[tuple[str, str]]:
    root = ROOT / "dist" / series / "wiki" / f"v{t:02d}"
    out = []
    back = unmap(series)
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("source/") or rel == "index.md":
            continue
        text = re.sub(r"<sub>.*?</sub>", " ", p.read_text(encoding="utf-8"))
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[*_>#`]", " ", text)
        out += [(back(rel[:-3]), s.strip()) for s in re.split(r"(?<=[.!?])\s+|\n+", back(text)) if len(s.strip()) > 12]
    return out


def slug(name: str) -> str:
    return re.sub(r"[^a-z]+", "-", name.lower()).strip("-")


def candidates(fact: str, sents: list[tuple[str, str]]) -> list[tuple[bool, str, str]]:
    ch = fact.split(" | ", 1)[0]
    own = {f"character/{slug(ch)}"} | {f"character/{slug(w)}" for w in ch.split() if len(w) > 3}
    out = []
    for page, s in sents:
        mine = page in own
        if all(re.search(p[2:] if p.startswith("S:") else p, s, re.I) for p in PAT[fact] if not (mine and p.startswith("S:"))):
            out.append((mine, page, s))
    q = toks(fact)
    return sorted(out, key=lambda x: (not x[0], -len(q & toks(x[2]))))


def cutoffs_of() -> dict[int, list[str]]:
    return {t: [k[2:] for k in json.loads((HERE / f"anne@v2_v{t}.json").read_text(encoding="utf-8"))] for t in range(1, 6)}


def validate() -> None:
    tot = Counter()
    for f in sorted(HERE.glob("anne*_v?.json")):
        series, t = f.stem.rsplit("_v", 1)
        if not (ROOT / "dist" / series / "wiki" / f"v{int(t):02d}").is_dir():
            continue
        sents = tree(series, int(t))
        for k, lab in json.loads(f.read_text(encoding="utf-8")).items():
            if k[2:] in PAT and lab in ("yes", "no"):
                tot[(series, lab, bool(candidates(k[2:], sents)))] += 1
    print("series: yes with candidate / yes without (pattern miss) | no with candidate / no without")
    for s in sorted({k[0] for k in tot}):
        print(f"  {s:10} {tot[(s,'yes',True)]:4} / {tot[(s,'yes',False)]:3} | {tot[(s,'no',True)]:4} / {tot[(s,'no',False)]:3}")
    y, m = sum(v for k, v in tot.items() if k[1] == "yes"), sum(v for k, v in tot.items() if k[1] == "yes" and not k[2])
    n, e = sum(v for k, v in tot.items() if k[1] == "no"), sum(v for k, v in tot.items() if k[1] == "no" and not k[2])
    print(f"all: {m}/{y} labelled-yes cells have no candidate ({m / y:.1%}); {e}/{n} labelled-no cells have none")


def view(series: str, upto: int, only_miss: bool, show: int) -> None:
    for t, facts in cutoffs_of().items():
        if t > upto:
            continue
        sents = tree(series, t)
        sdf = Counter(w for _, s in sents for w in toks(s))
        print(f"\n===== {series} t={t}: {len({p for p, _ in sents})} pages, {len(sents)} sentences")
        for i, fact in enumerate(facts):
            c = candidates(fact, sents)
            if c and only_miss:
                continue
            print(f"[{t}.{i}] {fact}")
            if c:
                for mine, page, s in c[:show]:
                    print(f"     {'*' if mine else ' '}{page.split('/')[-1][:16]}: {s[:230]}")
                if len(c) > show:
                    print(f"      (+{len(c) - show} more)")
            else:
                q = toks(fact)
                near = sorted(((sum(math.log(len(sents) / sdf[w]) for w in q & toks(s)), p, s) for p, s in sents), reverse=True)[:2]
                for sc, page, s in near:
                    if sc > 0:
                        print(f"     ~{page.split('/')[-1][:16]}: {s[:200]}")


def apply(series: str, path: str) -> None:
    dec = json.loads(Path(path).read_text(encoding="utf-8"))          # {"<t>": {"<fact>": "yes"|"no"}}
    for t, facts in cutoffs_of().items():
        d = dec[str(t)]
        assert set(d) == set(facts), (t, set(facts) ^ set(d))
        out = {f"C {f}": d[f] for f in facts}
        (HERE / f"{series}_v{t}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{series} t={t}: {sum(v == 'yes' for v in out.values())}/{len(out)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--view")
    ap.add_argument("--apply", nargs=2)
    ap.add_argument("--upto", type=int, default=5)
    ap.add_argument("--only-miss", action="store_true")
    ap.add_argument("--show", type=int, default=2)
    a = ap.parse_args()
    assert set(PAT) == {f for fs in cutoffs_of().values() for f in fs}, "patterns and gold facts differ"
    if a.validate:
        validate()
    if a.view:
        view(a.view, a.upto, a.only_miss, a.show)
    if a.apply:
        apply(*a.apply)
