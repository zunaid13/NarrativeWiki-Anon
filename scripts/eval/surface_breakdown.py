"""[33] Plan 0015 step 5 — precision and citation sufficiency by page kind, from labels already on disk. $0.

Run:  .venv/Scripts/python.exe scripts/eval/surface_breakdown.py anne [--upto 5]
      -> docs/eval/surface_breakdown_<series>.json, and a table on stdout

The comparison systems write one page per cast character; NarrativeWiki also publishes pages for
characters outside the cast, relationship (pair) pages, codex pages, a timeline and an index. The
headline precision samples every assertion of each system's whole tree, so it mixes page kinds. This
reads the labelled all-population samples (`docs/eval/precision/<series>_v<t>_all.jsonl`) and the
inventories they were drawn from and reports, per page kind:

  share   of the inventory's assertions on that kind of page (mean over cutoffs)
  n       sampled atoms, frame errors excluded
  page    labelled supported on the page that prints them          (M3)
  atom    labelled supported as a statement on its own             (M3, atom level)
  cited   carry a paragraph citation; suff = cited paragraphs alone establish them (M8)

"character (cast)" is the matched population: character pages of the characters the baselines also
write (docs/eval/parametric/<gold>.yaml). Nothing is relabelled; a kind with few sampled atoms is
reported with its count so that it is not over-read.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))
from assertion_precision import wilson  # noqa: E402

KINDS = ["character (cast)", "character (other)", "relationship", "codex", "timeline", "index"]


def kind(page: str, cast_pages: set[str]) -> str:
    top = page.split("/")[0]
    if top == "character":
        return "character (cast)" if page in cast_pages else "character (other)"
    return {"relationships": "relationship", "codex": "codex", "timeline": "timeline"}.get(top, "index")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=5)
    a = ap.parse_args()
    ev = ROOT / "docs" / "eval"
    base = a.series.split("@")[0]
    # Cast pages: the page each gold character is scored on (the map written for CHANGES C6), else its slug.
    import yaml
    sys.path.insert(0, str(ROOT / "src"))
    from narrativewiki.entities.gazetteer import slugify
    gold = yaml.safe_load((ev / "parametric" / f"{base}.yaml").read_text(encoding="utf-8"))
    names = sorted(gold["characters"])
    pmap_file = ev / "parametric" / "pages" / f"{a.series}.yaml"
    pmap = (yaml.safe_load(pmap_file.read_text(encoding="utf-8")) or {}) if pmap_file.exists() else {}
    cast_pages = set()
    for n in names:
        slugs = pmap.get(n) or [slugify(n)]
        for s in ([slugs] if isinstance(slugs, str) else slugs):
            cast_pages.add(f"character/{s}.md")
    share = defaultdict(list)
    rows = []
    for t in range(1, a.upto + 1):
        inv = ev / "inventory" / f"{a.series}_v{t}.jsonl"
        if inv.exists():
            c = Counter(kind(json.loads(l)["page"], cast_pages) for l in inv.open(encoding="utf-8"))
            tot = sum(c.values())
            for k in KINDS:
                share[k].append(c[k] / tot)
        f = ev / "precision" / f"{a.series}_v{t}_all.jsonl"
        if f.exists():
            rows += [dict(json.loads(l), t=t) for l in f.open(encoding="utf-8")]
    rows = [r for r in rows if (r.get("human") or "") not in ("", "frame_error")]
    out = {"series": a.series, "cast_characters": len(names), "cast_pages": len(cast_pages), "kinds": {}}
    print(f"{a.series}: {len(rows)} labelled atoms, {len(names)} cast characters")
    print(f"{'page kind':20} share    n  page-supported        atom-supported   cited  sufficient")
    for k in KINDS + ["all"]:
        rs = rows if k == "all" else [r for r in rows if kind(r["page"], cast_pages) == k]
        n = len(rs)
        if not n:
            continue
        ps = sum(r["human"] == "supported" for r in rs)
        at = sum((r.get("human_atom") or r["human"]) == "supported" for r in rs)
        m8 = [r for r in rs if (r.get("cite") or "") not in ("", "frame_error")]
        cited = sum(r["cite"] != "none" for r in m8)
        suff = sum(r["cite"] == "sufficient" for r in m8)
        lo, hi = wilson(ps, n)
        sh = 1.0 if k == "all" else (sum(share[k]) / len(share[k]) if share[k] else None)
        out["kinds"][k] = {"inventory_share": sh, "n": n, "page_supported": ps, "page_ci95": [lo, hi],
                           "atom_supported": at, "m8_n": len(m8), "cited": cited, "sufficient": suff}
        print(f"{k:20} {sh if sh is None else round(sh, 3)!s:6} {n:4}  {ps:3} {ps / n:.3f} [{lo:.2f},{hi:.2f}]   {at:3} {at / n:.3f}"
              f"    {cited:3}/{len(m8):<3} {suff:3}")
    dest = ev / f"surface_breakdown_{a.series}.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("->", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
