"""derived_values.py: recompute the paper's DERIVED values from the label files and compare them with what the
paper prints. $0, reads only. Exit 1 if anything printed differs from what the labels give.

Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/derived_values.py

Why it exists (2026-10-08, CHANGES C45): `fill_results.py` refills the per-cutoff cells and their means, but a
second layer of values is typed by hand from ledger rows (docs/paper/_build/fill_v2.py, results.tex, the page-kind
table of appendix G). CHANGES C43 changed recall labels and three sampled rows; the cells were refilled and
thirteen typed values were not. This script recomputes that second layer:

  cells      every M1/M3/M8/M4 cell and mean of both texts, from docs/eval/observations
  cited      citation sufficiency among sampled atoms that carry a citation (M8C-anne-*)
  pair rule  precision of both builds under the scene rule, the line rule, for statements alone, and with the
             second annotator's own pair verdicts (REV-M3*), from precision/*_all.jsonl and pair_strict_claude.json
  page kinds the table of appendix G (cast = gold characters through the page map, as surface_breakdown.py)
  builds     recall of the first build and of the prefix builds (REV-M1-first, A1-*), from recall_hand
  misses     gold facts the reported build misses at t=5 and who conveys them (M1-anne-miss-t5*)
  inventory  assertions and distinct assertions per cutoff (N-, ND-anne-avg-*)
"""
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import yaml

EV = Path(__file__).resolve().parents[1]
ROOT = EV.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.entities.gazetteer import slugify  # noqa: E402

PAPER = ROOT / "docs" / "paper"
RES = Path(sys.argv[sys.argv.index("--results") + 1]) if "--results" in sys.argv else PAPER / "results.tex"  # another revision
KEYS = {m[1]: m[2] for m in re.finditer(r"\\declareres\{([^}]*)\}\{(?:[^{}]|\{[^{}]*\})*\}\{((?:[^{}]|\{[^{}]*\})*)\}",
                                        RES.read_text(encoding="utf-8"))}
PAIR = json.loads((EV / "precision" / "pair_strict_claude.json").read_text(encoding="utf-8"))
TL = json.loads((EV / "precision" / "timeline_cite_claude.json").read_text(encoding="utf-8"))
SYS = {"B1": "b1", "B2": "b2", "B3": "b3", "B4": "b4-v2", "B5": "v2", "X1": "x1", "X2": "x2"}
bad = []


def check(key, want):
    """`want` is the string the labels give; KEYS[key] is what the paper prints."""
    got = KEYS.get(key)
    flag = "" if got == want else f"   <-- paper prints {got!r}"
    if flag:
        bad.append(key)
    print(f"  {key} = {want}{flag}")


def rate(n, d):
    return f"{n / d:.3f} ({n}/{d})"


def mean(cells):
    return float(sum(Fraction(n, d) for n, d in cells) / len(cells))


def jl(path):
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


# ---- cells and means, both texts -------------------------------------------------------------------------------
print("== cells and means (only differences are listed)")
n_cells = 0
for work, prefix in (("anne", "anne"), ("annedecon", "anne-decon")):
    for s, tag in SYS.items():
        acc = defaultdict(lambda: [0, 0])
        for r in jl(EV / "observations" / f"{prefix}@{tag}_{s}.jsonl"):
            acc[(r["metric"], r["cutoff"])][0] += r["num"]
            acc[(r["metric"], r["cutoff"])][1] += r["den"]
        for metric in ("M1", "M3", "M8", "M4"):
            cells = [tuple(acc[(metric, t)]) for t in range(1, 6) if acc[(metric, t)][1]]
            for t, (n, d) in enumerate(cells, 1):
                printed = KEYS.get(f"{metric}-{work}-t{t}-{s}", "")
                want = f"{n}/{d}" if metric == "M4" else rate(n, d)
                n_cells += bool(printed)
                if printed and printed != want:
                    check(f"{metric}-{work}-t{t}-{s}", want)
            avg = KEYS.get(f"{metric}-{work}-avg-{s}", "")
            if metric != "M4" and len(cells) == 5 and re.match(r"^\d\.\d+$", avg) and avg != f"{mean(cells):.3f}":
                check(f"{metric}-{work}-avg-{s}", f"{mean(cells):.3f}")
print(f"  {n_cells} printed cells compared")


# ---- pair rule, page kinds, citations --------------------------------------------------------------------------
def cast_pages(series):
    gold = yaml.safe_load((EV / "parametric" / f"{series.split('@')[0]}.yaml").read_text(encoding="utf-8"))
    pf = EV / "parametric" / "pages" / f"{series}.yaml"
    pmap = (yaml.safe_load(pf.read_text(encoding="utf-8")) or {}) if pf.exists() else {}
    out = set()
    for name in gold["characters"]:
        slugs = pmap.get(name) or [slugify(name)]
        out |= {f"character/{x}.md" for x in ([slugs] if isinstance(slugs, str) else slugs)}
    return out


def kind(page, cast):
    top = page.split("/")[0]
    if top == "character":
        return "cast" if page in cast else "other"
    return {"relationships": "rel", "codex": "codex", "timeline": "timeline"}.get(top, "index")


def sample(series):
    """Per-kind and per-cutoff counts of the labelled sample, sidecars applied as apply_pair_rule.py applies them."""
    cast, agg, per_t, share = cast_pages(series), defaultdict(Counter), defaultdict(Counter), defaultdict(list)
    for t in range(1, 6):
        inv = Counter(kind(r["page"], cast) for r in jl(EV / "inventory" / f"{series}_v{t}.jsonl"))
        for k in ("cast", "other", "rel", "codex", "timeline", "index"):
            share[k].append(inv[k] / sum(inv.values()))
        for i, r in enumerate(jl(EV / "precision" / f"{series}_v{t}_all.jsonl")):
            if (r.get("human") or "") in ("", "frame_error"):
                continue
            atom = (r.get("human_atom") or r["human"]) == "supported"
            pv = PAIR.get(series, {}).get(str(t), {}).get(str(i))
            cite = (TL.get(series, {}).get(str(t), {}).get(str(i)) or r).get("cite") or ""
            row = {"n": 1, "atom": atom, "own": r["human"] == "supported",
                   "page": (atom and pv["scene"] == "yes") if pv else r["human"] == "supported",
                   "line": (atom and pv["line"] == "yes") if pv else r["human"] == "supported",
                   "cited": cite not in ("", "none"), "suff": cite == "sufficient",
                   "pair": pv is not None, "pair_yes": bool(pv) and pv["scene"] == "yes"}
            for c in (agg[kind(r["page"], cast)], agg["all"], per_t[t]):
                c.update({k: int(v) for k, v in row.items()})
    return agg, per_t, {k: sum(v) / len(v) for k, v in share.items()}


def over_t(per_t, field):
    return f"{mean([(per_t[t][field], per_t[t]['n']) for t in sorted(per_t)]):.3f}"


print("== pair rule and citations")
for series, suffix in (("anne", "-first"), ("anne@v2", "")):
    agg, per_t, share = sample(series)
    a = agg["all"]
    check("REV-M3-first" if suffix else "M3-anne-avg-B5", over_t(per_t, "page"))
    check(f"REV-M3L{suffix}", over_t(per_t, "line"))
    check(f"REV-M3A{suffix}", over_t(per_t, "atom"))
    check("REV-M8-first" if suffix else "M8-anne-avg-B5", over_t(per_t, "suff"))
    check(f"REV-pair{suffix}", f"{a['pair_yes']} of {a['pair']}")
    if not suffix:
        check("REV-M3-pair2", over_t(per_t, "own"))
        check("M8C-anne-B5", rate(a["suff"], a["cited"]))
        check("M3K-anne-B5-cast", rate(agg["cast"]["page"], agg["cast"]["n"]))
        check("M3K-anne-B5-rel", rate(agg["rel"]["page"], agg["rel"]["n"]))
        check("M3K-anne-B5-rel-atom", rate(agg["rel"]["atom"], agg["rel"]["n"]))
        table = (PAPER / "appendix" / "g-additional-results.tex").read_text(encoding="utf-8")
        table = re.sub(r"\\best\{([^{}]*)\}", r"\1", table)  # bold marks of docs/paper/_build/bold_tables.py
        print("== page kinds (appendix G table): share, n, page, atom, sufficient")
        rows = [("Character pages, cast", "cast"), ("Character pages, other", "other"), ("Relationship pages", "rel"),
                ("Codex pages", "codex"), ("Timeline", "timeline"), ("\\sys, all pages", "all")]
        for label, k in rows:
            c = agg[k]
            want = f"{label} & {1.0 if k == 'all' else share[k]:.3f} & {c['n']} & {c['page']} & {c['atom']} & {c['suff']} \\\\"
            ok = want in table
            bad.extend([] if ok else [label])
            print(f"  {want}" + ("" if ok else "   <-- not in the table as printed"))
        print(f"  negatives on the page: {a['n'] - a['page']} (relationship {agg['rel']['n'] - agg['rel']['page']}, "
              f"of which true as statements {agg['rel']['atom'] - agg['rel']['page']})")
        for b in ("B1", "B2"):
            bagg, _, _ = sample(f"anne@{SYS[b]}")
            ball = bagg["all"]
            check(f"M8C-anne-{b}", rate(ball["suff"], ball["cited"]))
            want = f"{b} (cast pages only) & 1.000 & {ball['n']} & {ball['page']} & {ball['atom']} & {ball['suff']} \\\\"
            bad.extend([] if want in table else [b])
            print(f"  {want}" + ("" if want in table else "   <-- not in the table as printed"))

# ---- recall of the first build and the prefix builds; misses at t=5 ---------------------------------------------
print("== builds and misses")
rec = lambda s, t: json.loads((EV / "recall_hand" / f"{s}_v{t}.json").read_text(encoding="utf-8"))
yes = lambda d: sum(v == "yes" for v in d.values())
check("REV-M1-first", f"{mean([(yes(rec('anne', t)), len(rec('anne', t))) for t in range(1, 6)]):.3f}")
tot, disc = [0, 0], 0
for t, pre in ((2, "anne@p2"), (3, "anne@p3")):
    p, f = rec(pre, t), rec("anne", t)
    check(f"A1-t{t}-M1-prefix", rate(yes(p), len(p)))
    check(f"A1-t{t}-M1-full", rate(yes(f), len(f)))
    tot = [tot[0] + yes(p), tot[1] + yes(f)]
    disc += sum((p[k] == "yes") != (f[k] == "yes") for k in p)
check("A1-dM1", f"${(tot[0] - tot[1]) / 112:.3f}$ ({tot[0]} vs {tot[1]} of 112)")
print(f"  discordant fact-cutoff pairs between prefix and full build: {disc} (the text says seven)")
nw, b1, b2 = rec("anne@v2", 5), rec("anne@b1", 5), rec("anne@b2", 5)
miss = [k for k in nw if nw[k] != "yes"]
check("M1-anne-miss-t5", str(len(miss)))
check("M1-anne-miss-t5-prefix", str(sum(b1[k] == "yes" or b2[k] == "yes" for k in miss)))
only = [k.split("|")[1].strip() for k in nw if nw[k] == "yes" and b1[k] != "yes" and b2[k] != "yes"]
print(f"  conveyed by the reported build and by neither B1 nor B2: {len(only)} (the text says four): {only}")

# ---- inventory --------------------------------------------------------------------------------------------------
print("== inventory")
fold = lambda s: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()
for s, tag in SYS.items():
    rows = [jl(EV / "inventory" / f"anne@{tag}_v{t}.jsonl") for t in range(1, 6)]
    check(f"N-anne-avg-{s}", f"{round(sum(map(len, rows)) / 5):,}")
    check(f"ND-anne-avg-{s}", f"{round(sum(len({fold(r.get('value') or '') for r in rs}) for rs in rows) / 5):,}")

print(f"\n{'OK: every derived value printed in the paper matches the label files' if not bad else 'MISMATCH: ' + ', '.join(bad)}")
sys.exit(1 if bad else 0)
