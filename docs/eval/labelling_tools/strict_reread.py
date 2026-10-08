"""strict_reread.py [--write]: six gold facts re-read in every labelled Anne cell under the strict reading. $0.

CHANGES C43 (2026-10-07). The human check of 2026-10-07 accepted `no` where a wiki states something weaker than
the fact (fights him / hates him; fascinated by insects / collects them). The standing rule already asks for the
same event, relation or attribute; five facts had been read more leniently in some cells, and one conjunction
(braids and green eyes) less leniently in others. This applies one test per fact to every cell that has a label
file and a wiki tree, prints each cell whose label changes with the sentence that decides, and with --write
rewrites the label files. A test is a regex over one sentence; every printed row was read before --write.
Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/strict_reread.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RH = ROOT / "docs" / "eval" / "recall_hand"
sys.path.insert(0, str(RH))
import recall_patterns as rp  # noqa: E402

GREEN_EYES = r"\beyes\b.{0,90}\bgreen|\bgreen\b.{0,30}\beyes\b|gr[ae]y-green|greenish"


def own_quote(sents):
    """Rachel's own words on her own page: the quoted line carries no name"""
    return next((f"rachel-lynde: {s}" for p, s in sents if p.endswith("character/rachel-lynde")
                 and re.search(r"carrot|hair as red|(critici|insult|remark|comment|mock).{0,160}\bhair\b", s, re.I)), None)


def one(rx, page=None):
    """yes when one sentence (optionally on a page whose address matches `page`) matches rx"""
    def test(sents):
        return next((f"{p.split('/')[-1]}: {s}" for p, s in sents if (not page or re.search(page, p))
                     and re.search(rx, s, re.I)), None)
    return test


def braids(sents):
    own = [s.replace("Green Gables", "") for p, s in sents if p.endswith("character/anne-shirley")]
    b = next((s for s in own if re.search(r"\b(braid|plait)", s, re.I)), None)
    e = next((s for s in own if re.search(GREEN_EYES, s, re.I)), None)
    return f"{b} || {e}" if b and e else None


TESTS = {
    "Marilla urges Anne to go on to Redmond": one(
        r"Marilla.{0,160}(urg|encourag|insist|persuad|advis|press(es|ed)|ought)\w*.{0,160}(college|Redmond)"),
    "Rachel Lynde says Anne's hair is as red as carrots": one(
        r"(Rachel|Lynde).{0,200}(critici|insult|remark|comment|mock|twitt).{0,160}\bhair\b"
        r"|(critici|insult|remark|comment|mock).{0,120}\bhair\b.{0,200}(Rachel|Lynde)"),
    "Captain Jim tells Anne about lost Margaret": one(
        r"(tells?|told|shar|confid|reveal|recount|entrust|speak to you|talk).{0,140}Margaret|Margaret.{0,60}(to|with) (Anne|Mistress)"),
    "Carl Meredith collects toads, bugs and frogs": one(
        r"Carl.{0,200}(collect|catch|as pets|pocket|mania|into the house)|insect collection"
        r"|(bugs|frogs|toads|creatures|insects).{0,80}pockets?"
        r"|(collect|catch|pocket|into the house|keeping|kept).{0,120}(bugs|frogs|toads|creatures|insects|animal)",
        page=r"character/(carl|john-meredith|faith|una|jerry|mary-vance)"),
    "Walter hates Dan Reese": one(
        r"(hate|hatred|detest|loath|dislike|enem).{0,90}\bDan\b|\bDan\b.{0,120}(hate|hatred|detest|loath|dislike|enem)"),
    "Anne has two braids of very thick red hair and eyes that look green in some lights": braids,
}
ONLY = {("anne@x2", 2, "Anne sells Mr. Harrison's Jersey cow to Mr. Shearer by mistake"):
        "yes"}  # labelled no beside the sentence "Anne accidentally sold Mr. Harrison's own Jersey cow to a buyer"
write = "--write" in sys.argv
for s in sorted({f.name.rsplit("_v", 1)[0] for f in RH.glob("anne*_v[1-5].json")}):
    for t in range(1, 6):
        f = RH / f"{s}_v{t}.json"
        if not f.exists() or not (ROOT / "dist" / s / "wiki" / f"v{t:02d}").is_dir():
            continue
        raw = f.read_bytes()
        lab = json.loads(raw.decode("utf-8"))
        sents = rp.tree(s, t)
        before, old = sum(v == "yes" for v in lab.values()), dict(lab)
        for k in lab:
            fact = k.split(" | ", 1)[1].rstrip(".")
            new, why = None, None
            if fact in TESTS:
                why = TESTS[fact](sents) or (fact.startswith("Rachel") and own_quote(sents)) or None
                new = "yes" if why else "no"
            if (s, t, fact) in ONLY:
                new, why = ONLY[(s, t, fact)], "listed"
            if new and new != lab[k]:
                print(f"{s} t={t} {lab[k]}->{new} | {fact[:44]} | {(why or '')[:300]}")
                lab[k] = new
            elif new == "yes" and "--keeps" in sys.argv:
                print(f"   keep yes {s} t={t} | {fact[:30]} | {why[:260]}")
        after = sum(v == "yes" for v in lab.values())
        if lab != old:
            print(f"== {s} t={t}: {before}/{len(lab)} -> {after}/{len(lab)}")
            if write:
                f.write_text(json.dumps(lab, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                             newline="\r\n" if b"\r\n" in raw else "\n")
