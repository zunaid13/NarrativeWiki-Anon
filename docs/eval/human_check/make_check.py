"""The human check of the AI labels: 554 items, blind to system and to every AI label. $0, no model.

Run:  .venv/Scripts/python.exe docs/eval/human_check/make_check.py
Supersedes docs/eval/human/ (the 120 + 60 packet of 2026-10-02), which showed version-1 pages, checked
disclosure (dropped by the maintainer, MEASUREMENTS 2026-10-04) and often did not show the passage a reader
needed (docs/eval/agent_annotation/2026-10-05/DOUBLE_CHECK.md, finding 5).

Allocation (maintainer, MEASUREMENTS 2026-10-04 "554 checks"): gold facts in full, a uniform 10% of every
other label set, drawn per system so that a system whose files arrive later keeps the others' items fixed.
  G_gold.html       111  every gold fact with the paragraph it is dated by, and its neighbours
  R_recall.html     235  (system, cutoff, gold fact): the wiki sentences nearest the fact
  P_precision.html  148  a sampled assertion: page text, cited paragraphs, the AI reader's own witness
                         paragraphs where it recorded them, and paragraphs found by word overlap
  C_citation.html    60  an assertion and only the paragraphs its page cites (main system, B1, B2)
  *_answers.csv          one row per item; a reader copies each to *_answers_<name>.csv
  key.json               item -> system, cutoff, source row, AI labels. KEEP CLOSED until readers finish.
Parts G and P have the answer `not shown`: nothing displayed bears on the item. Such items are resupplied with
more text instead of being scored as a "no". A stratum whose files are missing is skipped and reported;
rerun when they exist (per-stratum seeds: nothing already issued moves).
"""
from __future__ import annotations

import csv
import html
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EV = ROOT / "docs" / "eval"
SEED = 554
MAIN = ("B5", "anne@v2")
SYSTEMS = [MAIN, ("B1", "anne@b1"), ("B2", "anne@b2"), ("B3", "anne@b3"), ("B4", "anne@b4-v2"),
           ("X1", "anne@x1"), ("X2", "anne@x2")]
PREFIX = [("P2", "anne@p2", 2), ("P3", "anne@p3", 3)]            # prefix builds: one cutoff each
R_QUOTA = dict(zip([s for s, _ in SYSTEMS], [34, 34, 34, 34, 33, 33, 33]))   # 235 of 2,345
STOP = set("the a an and of to in on at for with by from is was were are be been his her their its that this as it "
           "he she they who whom which had has have not but or after before into over while when also very more "
           "most some such than then there them what would could should about only just said says since".split())

paras: dict[str, str] = {}
for v in range(1, 6):
    for line in (ROOT / "data" / "anne" / "01_parsed" / f"v{v:02d}.jsonl").open(encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            paras[r["para_id"]] = r["text"]
ids = sorted(paras)


def toks(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in STOP}    # 6-letter stems


doc_tok = {i: toks(paras[i]) for i in ids}
df = Counter(w for i in ids for w in doc_tok[i])


def best(query: str, t: int, skip: set[str], k: int) -> list[str]:
    q = toks(query)
    scored = [(sum(math.log(len(ids) / df[w]) for w in q & doc_tok[i]), i)
              for i in ids if int(i[1:3]) <= t and i not in skip]
    return [i for s, i in sorted(scored, reverse=True)[:k] if s > 0]


def para_ids(x) -> list[str]:
    """Paragraph ids named anywhere in a stored field (a list, a dict, or free text with p0000-p0003 ranges)."""
    text = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
    out = []
    for m in re.finditer(r"(v\d\d:c\d\d):p(\d{4})(?:\s*-\s*p?(\d{4}))?", text):
        a, b = int(m.group(2)), int(m.group(3) or m.group(2))
        out += [f"{m.group(1)}:p{n:04d}" for n in range(a, min(b, a + 3) + 1)]
    return [i for i in dict.fromkeys(out) if i in paras]


def para_html(i: str) -> str:
    return f"<p class='src'><b>{i}</b> {html.escape(paras[i])}</p>"


def tree_sentences(series: str, t: int) -> list[tuple[str, str]]:
    root = ROOT / "dist" / series / "wiki" / f"v{t:02d}"
    out = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("source/") or rel == "index.md":
            continue
        text = re.sub(r"<sub>.*?</sub>", " ", p.read_text(encoding="utf-8"))
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[*_>#`]", " ", text)
        where = ("the page about " + p.stem.replace("-", " ").title()) if rel.startswith("character/") else "a shared page"
        # CHANGES C42: no break after Mr. / Mrs. / Dr. / St. / Rev. (132 of 235 items showed a cut sentence)
        out += [(where, s.strip()) for s in
                re.split(r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bRev\.)(?<=[.!?])\s+|\n+", text)
                if len(s.strip()) > 12]
    return out


def where(page: str, r: dict) -> str:
    kind = {"character": "Character page", "relationships": "Relationship (pair) page", "codex": "Shared page",
            "timeline": "Timeline"}.get(page.split("/")[0], "Page")
    name = r.get("entity") or " & ".join(x.replace("-", " ").title() for x in Path(page).stem.split("--"))
    bits = [f"{kind}: {name}"]
    if r.get("section"):
        bits.append(f"section “{r['section']}”")
    if r.get("label") and r["label"] != r.get("section"):
        bits.append(str(r["label"]))
    return " · ".join(bits)


CSS = ("<meta charset='utf-8'><style>body{font:15px/1.45 Georgia,serif;max-width:900px;margin:2em auto;padding:0 1em}"
       ".item{border:1px solid #bbb;border-radius:6px;padding:.8em 1em;margin:1.2em 0}"
       ".atom{font-size:1.15em;background:#eef4fb;padding:.4em .6em;border-left:4px solid #2f6db5}"
       ".src{font-size:.92em;color:#222;margin:.35em 0}.h{color:#555;font:13px sans-serif}"
       "h2{font:bold 17px sans-serif;margin:0 0 .4em}</style>")
key: dict[str, dict] = {}
skipped: list[str] = []


def write(part: str, title: str, header: list[str], items: list[tuple[dict, str]]) -> None:
    """items: (key entry, html body). Shuffled once, numbered, written with an empty answer sheet."""
    random.Random(f"{SEED}-{part}-order").shuffle(items)
    out = [CSS, f"<h1>{title}</h1><p>Read INSTRUCTIONS.md first. Fill one row per item in your copy of "
                f"{part}_answers.csv.</p>"]
    with (HERE / f"{part}_answers.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item"] + header + ["comment"])
        for n, (entry, body) in enumerate(items, 1):
            item = f"{part[0]}{n:03d}"
            key[item] = entry
            out.append(f"<div class='item'><h2>{item}{entry.pop('_head', '')}</h2>{body}</div>")
            w.writerow([item] + [""] * (len(header) + 1))
    name = {"G": "G_gold", "R": "R_recall", "P": "P_precision", "C": "C_citation"}[part[0]]
    (HERE / f"{name}.html").write_text("\n".join(out), encoding="utf-8")


# ------------------------------------------------------------------------------------------- G: gold
gold = yaml.safe_load((EV / "parametric" / "anne.yaml").open(encoding="utf-8"))["characters"]
facts = []                                                      # (character, claim, [evidence ids], volume)
for ch, d in gold.items():
    for f in d.get("facts") or []:
        ev = [f["evidence"]] if isinstance(f["evidence"], str) else list(f["evidence"])
        if int(ev[0][1:3]) <= 5:
            facts.append((ch, f["claim"], ev, int(ev[0][1:3])))
g_items = []
for ch, claim, ev, vol in facts:
    ctx = []
    for e in ev:
        n = ids.index(e)
        ctx += [i for i in ids[max(0, n - 1):n + 2] if i[:8] == e[:8]]
    body = (f"<p class='h'>Character: {html.escape(ch)} · dated to volume {vol}</p><p class='atom'>{html.escape(claim)}</p>"
            f"<p class='h'>The paragraph the fact is dated by ({', '.join(ev)}), with its neighbours:</p>"
            + "".join(para_html(i) for i in dict.fromkeys(ctx)))
    g_items.append(({"character": ch, "claim": claim, "evidence": ev, "volume": vol}, body))
write("G", "Part G — is the gold fact established by its paragraph?",
      ["established (yes / partly / no / not shown)"], g_items)

# ----------------------------------------------------------------------------------------- R: recall
r_items = []
for sysname, series in SYSTEMS:
    pop = []
    for t in range(1, 6):
        if not (ROOT / "dist" / series / "wiki" / f"v{t:02d}").is_dir():
            pop = None
            break
        own = EV / "recall_hand" / f"{series}_v{t}.json"
        lab = json.loads(own.read_text(encoding="utf-8")) if own.exists() else {}
        for k in json.loads((EV / "recall_hand" / f"{MAIN[1]}_v{t}.json").read_text(encoding="utf-8")):
            pop.append((t, k, lab.get(k)))                      # the gold facts of a cutoff are the same for all
    if pop is None:
        skipped.append(f"recall {sysname}: wiki tree missing")
        continue
    for t, k, lab in random.Random(f"{SEED}-R-{sysname}").sample(pop, R_QUOTA[sysname]):
        ch, claim = k[2:].split(" | ", 1)
        q = toks(claim + " " + ch)
        sents = tree_sentences(series, t)
        n_s = len(sents)
        sdf = Counter(w for _, s in sents for w in toks(s))
        scored = sorted(((sum(math.log(n_s / sdf[w]) for w in q & toks(s)), wh, s) for wh, s in sents), reverse=True)
        shown, seen = [], set()
        for sc, wh, s in scored:
            if sc > 0 and s[:90] not in seen:
                seen.add(s[:90])
                shown.append((wh, s))
            if len(shown) == 8:
                break
        own = "the page about " + ch                              # the fact's own page: three more, if any
        shown += [(wh, s) for sc, wh, s in scored if sc > 0 and wh.lower() == own.lower() and s[:90] not in seen][:3]
        body = (f"<p class='h'>Gold fact about {html.escape(ch)}:</p><p class='atom'>{html.escape(claim)}</p>"
                f"<p class='h'>The sentences of the volume-{t} wiki nearest to it (word overlap; all pages searched):</p>"
                + ("".join(f"<p class='src'><i>on {html.escape(wh)}:</i> {html.escape(s)}</p>" for wh, s in shown)
                   or "<p class='src'><i>no sentence shares a content word with the fact</i></p>"))
        r_items.append(({"system": sysname, "series": series, "t": t, "fact": k, "ai": lab,
                         "_head": f" — the reader has finished volume {t}"}, body))
write("R", "Part R — does the wiki convey the fact?", ["conveyed (yes / partly / no)"], r_items)


# ------------------------------------------------------------------------------- P and C: precision rows
# CHANGES C40: the inventory tool did not attach a timeline line's "_Sources:" paragraphs to it, so those sampled
# rows carry `evidence: []`. The reader is shown what the delivered page cites; the AI verdict in the key is the
# re-reading of those paragraphs (docs/eval/precision/timeline_cite_claude.json).
TIMELINE_CITES = json.loads((EV / "precision" / "timeline_cite_claude.json").read_text(encoding="utf-8"))


def rows_of(series: str, cutoffs) -> list[tuple[int, int, dict]] | None:
    out = []
    for t in cutoffs:
        f = EV / "precision" / f"{series}_v{t}_all.jsonl"
        if not f.exists():
            return None
        fix = TIMELINE_CITES.get(series, {}).get(str(t), {})
        for n, l in enumerate(f.open(encoding="utf-8")):
            r = json.loads(l)
            if str(n) in fix:
                r["evidence"], r["cite"] = fix[str(n)]["evidence"], fix[str(n)]["cite"]
            out.append((t, n, r))
    return out


def cited_of(r: dict, t: int) -> list[str]:
    # CHANGES C42: every cited paragraph. A cut at six hid the citation that mattered on 24 of 60 C items.
    return [c for c in para_ids(r.get("evidence") or []) if int(c[1:3]) <= t]


p_items = []
for sysname, series, cutoffs, n in [(s, ser, range(1, 6), 20) for s, ser in SYSTEMS] + [(s, ser, [t], 4) for s, ser, t in PREFIX]:
    rows = rows_of(series, cutoffs)
    if rows is None:
        skipped.append(f"precision {sysname}: a sample file is missing")
        continue
    for t, row, r in random.Random(f"{SEED}-P-{sysname}").sample(rows, n):
        cited = cited_of(r, t)
        wit = [i for i in para_ids([r.get("witness_para_ids"), r.get("note")]) if int(i[1:3]) <= t and i not in cited][:4]
        k = (6, 4) if not (cited or wit) else (5, 2)                       # nothing recorded: show more
        extra = best(f"{r['value']} {r.get('entity') or ''}", t, set(cited + wit), k[0])
        extra += best(str(r.get("unit") or ""), t, set(cited + wit + extra), k[1])
        other = wit + extra
        random.Random(f"{SEED}-P-{sysname}-{t}-{row}").shuffle(other)      # a witness is not marked as one
        body = (f"<p class='h'>{html.escape(where(r['page'], r))}</p><p class='atom'>{html.escape(str(r['value']))}</p>"
                + (f"<p class='h'>The page text it comes from:</p><p>{html.escape(r['unit'])}</p>" if r.get("unit") else "")
                + "<p class='h'>Paragraphs the page cites for it:</p>"
                + ("".join(para_html(c) for c in cited) or "<p class='src'><i>none cited</i></p>")
                + f"<p class='h'>Other paragraphs of volumes 1–{t} that may bear on it:</p>"
                + ("".join(para_html(c) for c in other) or "<p class='src'><i>none found</i></p>"))
        p_items.append(({"system": sysname, "series": series, "t": t, "row": row, "ai_page": r.get("human"),
                         "ai_atom": r.get("human_atom"), "surface": r.get("surface"), "witness_shown": len(wit),
                         "_head": f" — the reader has finished volume {t}"}, body))
write("P", "Part P — is the statement supported?",
      ["supported (yes / partly / no / not shown)", "pair page only: did both take part? (yes / no / n-a)"], p_items)

c_items = []
for sysname, series in [MAIN, ("B1", "anne@b1"), ("B2", "anne@b2")]:
    rows = [x for x in rows_of(series, range(1, 6)) or [] if x[2].get("cite")]
    if len(rows) < 200:
        skipped.append(f"citation {sysname}: {len(rows)} rows with a citation label, 200 expected")
        continue
    for t, row, r in random.Random(f"{SEED}-C-{sysname}").sample(rows, 20):
        cited = cited_of(r, t)
        body = (f"<p class='h'>{html.escape(where(r['page'], r))}</p><p class='atom'>{html.escape(str(r['value']))}</p>"
                + (f"<p class='h'>The page text it comes from:</p><p>{html.escape(r['unit'])}</p>" if r.get("unit") else "")
                + "<p class='h'>The paragraphs the page cites for it, and nothing else:</p>"
                + ("".join(para_html(c) for c in cited) or "<p class='src'><i>none cited</i></p>"))
        c_items.append(({"system": sysname, "series": series, "t": t, "row": row, "ai_cite": r.get("cite"),
                         "_head": f" — the reader has finished volume {t}"}, body))
write("C", "Part C — are the cited paragraphs alone enough?",
      ["cited paragraphs alone enough? (yes / partly / no / none cited)"], c_items)

(HERE / "key.json").write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
n = Counter(k[0] for k in key)
print(f"items: G {n['G']} · R {n['R']} · P {n['P']} · C {n['C']} · total {sum(n.values())} of 554")
print("skipped (rerun when ready):", skipped or "nothing")
# self-check: how often would word overlap alone have shown a paragraph the AI reader used as its witness?
hit = tot = 0
for _, series in [MAIN]:
    for t, row, r in rows_of(series, range(1, 6)) or []:
        wit = [i for i in para_ids(r.get("witness_para_ids") or []) if int(i[1:3]) <= t]
        cited = cited_of(r, t)
        if wit and not set(wit) & set(cited):
            tot += 1
            hit += bool(set(wit) & set(best(f"{r['value']} {r.get('entity') or ''}", t, set(cited), 5)
                                       + best(str(r.get("unit") or ""), t, set(cited), 2)))
print(f"retrieval check (main system rows whose witness is not a cited paragraph): word overlap finds it in {hit}/{tot}")
