"""timeline_sources.py [--view]: the paragraph citations of the sampled timeline lines of anne@v2. $0.

Run, from the repository root:
  .venv/Scripts/python.exe docs/eval/labelling_tools/timeline_sources.py --view   # atoms with their cited paragraphs
  .venv/Scripts/python.exe docs/eval/labelling_tools/timeline_sources.py          # counts only

Why: `scripts/eval/assertion_inventory.py` attaches a "_Sources:" line to the prose or quoted scene above it, but
not to a timeline line ("**A, B** -- summary"), so every timeline row of the version-2 inventory has
`evidence: []` although the page prints one to four paragraphs under it. The sampled timeline atoms were
therefore labelled "no citation" for M8. This tool reads the citations from the delivered page; the verdicts of
the re-reading are in docs/eval/precision/timeline_cite_claude.json and enter the observations through
apply_sidecars (CHANGES C40). It also prints the share of inventory rows that carry a paragraph citation once
timeline lines are counted as the page prints them.
"""
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERIES = "anne@v2"
ANCHOR = re.compile(r"#nw-v(\d+)-c(\d+)-p(\d+)")


def plain(s):
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"\s+", " ", s.replace("*", "")).strip()


def page_sources(t, page):
    """[(plain summary, [para ids])] for every timeline line of one delivered page."""
    out, last = [], None
    for raw in (ROOT / "dist" / SERIES / "wiki" / f"v{t:02d}" / page).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = re.match(r"\*\*(.+?)\*\*\s+—\s+(.*)", line)
        if m:
            last = [plain(m.group(2)), []]
            out.append(last)
        elif line.startswith("_Sources") and last is not None:
            last[1] = [f"v{int(v):02d}:c{int(c):02d}:p{int(p):04d}" for v, c, p in ANCHOR.findall(line)]
            last = None
    return out


def main():
    view = "--view" in sys.argv
    text = {}
    for p in glob.glob(str(ROOT / "data" / "anne" / "01_parsed" / "v0*.jsonl")):
        for l in open(p, encoding="utf-8"):
            r = json.loads(l)
            text[r["para_id"]] = r["text"]
    n = found = 0
    for t in range(1, 6):
        rows = [json.loads(l) for l in (ROOT / "docs" / "eval" / "precision" / f"{SERIES}_v{t}_all.jsonl").open(encoding="utf-8")]
        for i, r in enumerate(rows):
            if not r["page"].startswith("timeline/"):
                continue
            n += 1
            unit = plain(r.get("unit") or r["value"])
            ids = next((ids for summary, ids in page_sources(t, r["page"]) if summary == unit or unit in summary), None)
            found += bool(ids)
            if view:
                print(f"\n=== t={t} row {i} [{r['page']} / {r.get('chapter')}] atom={r.get('human_atom')} cite={r.get('cite')}")
                print(f"ATOM: {r['value']}")
                for pid in ids or []:
                    print(f"  {pid}: {text.get(pid, '<missing>')[:1200]}")
    print(f"\nsampled timeline rows: {n}; with a printed paragraph citation: {found}")
    # share of inventory rows with a paragraph citation, timeline lines counted as the page prints them
    shares = []
    for t in range(1, 6):
        rows = [json.loads(l) for l in (ROOT / "docs" / "eval" / "inventory" / f"{SERIES}_v{t}.jsonl").open(encoding="utf-8") if l.strip()]
        cited = sum(1 for r in rows if r.get("evidence"))
        srcs = {}
        tl = tl_cited = 0
        for r in rows:
            if r["page"].startswith("timeline/") and not r.get("evidence"):
                tl += 1
                if r["page"] not in srcs:
                    srcs[r["page"]] = page_sources(t, r["page"])
                unit = plain(r.get("unit") or r["value"])
                tl_cited += any(ids and (s == unit or unit in s) for s, ids in srcs[r["page"]])
        shares.append((cited + tl_cited) / len(rows))
        print(f"t={t}: {len(rows)} rows, cited as inventoried {cited} ({cited / len(rows):.3f}), "
              f"timeline rows {tl} of which the page cites {tl_cited} -> {shares[-1]:.3f}")
    print(f"mean share with a paragraph citation, as printed: {sum(shares) / 5:.3f}")


if __name__ == "__main__":
    main()
