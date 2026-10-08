"""[33] Plan 0015 step 6 — how do the character pages that BOTH builds have differ at one cutoff? $0.

Run:  .venv/Scripts/python.exe scripts/eval/a1_shared_diff.py anne anne@p2 --upto 2
      (full-built series, prefix-built series, the cutoff both are compared at)
      -> docs/eval/a1/<full>_vs_<prefix>_v<t>_shared.json, and a summary on stdout

`a1_page_diff.py` lists the pages only one build has. This compares the pages with the same address
in both trees, on what is rendered from the graph (no prose):
  title      the page's canonical name
  aliases    the alias list of the front matter; an alias of the full build that never occurs in
             the text up to the cutoff is a *later-only alias* -- the one difference here that only
             a later volume can cause
  relations  (other character's title, predicate) lines of the Relationships section
The two builds are independent runs with different entity lists, so a relation line that only one
build has may be extraction variance; the script counts, it does not attribute. Only a later-only
alias is attributable to the suffix by construction.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HEAD = re.compile(r"^### \[([^\]]+)\]\(\.\./character/")
LINE = re.compile(r"^- ([A-Z][A-Za-z ]+?) — ")


def page(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---")[1]) if text.startswith("---") else {}
    rel, other, inside = set(), None, False
    for ln in text.splitlines():
        if ln.startswith("## "):
            inside = ln.strip() == "## Relationships"
            continue
        if not inside:
            continue
        if m := HEAD.match(ln):
            other = m.group(1)
        elif other and (m := LINE.match(ln)):
            rel.add((other, m.group(1).strip()))
    return {"title": fm.get("canonical"), "aliases": set(fm.get("aliases") or []), "relations": rel}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("full")
    ap.add_argument("prefix")
    ap.add_argument("--upto", type=int, required=True)
    a = ap.parse_args()
    t = a.upto
    trees = [ROOT / "dist" / s / "wiki" / f"v{t:02d}" / "character" for s in (a.full, a.prefix)]
    shared = sorted({p.name for p in trees[0].glob("*.md")} & {p.name for p in trees[1].glob("*.md")})
    text = " ".join(json.loads(l)["text"] for v in range(1, t + 1)
                    for l in (ROOT / "data" / a.full.split("@")[0] / "01_parsed" / f"v{v:02d}.jsonl").open(encoding="utf-8"))
    same_title = same_alias = 0
    later_only, rel_both, rel_full, rel_prefix, rows = [], 0, 0, 0, []
    for name in shared:
        f, p = page(trees[0] / name), page(trees[1] / name)
        same_title += f["title"] == p["title"]
        same_alias += f["aliases"] == p["aliases"]
        late = sorted(x for x in f["aliases"] - p["aliases"] if not re.search(rf"(?<!\w){re.escape(x)}(?!\w)", text))
        later_only += [(name, x) for x in late]
        both = f["relations"] & p["relations"]
        rel_both += len(both)
        rel_full += len(f["relations"] - both)
        rel_prefix += len(p["relations"] - both)
        rows.append({"page": name, "title_full": f["title"], "title_prefix": p["title"],
                     "aliases_full_only": sorted(f["aliases"] - p["aliases"]), "aliases_prefix_only": sorted(p["aliases"] - f["aliases"]),
                     "later_only_aliases": late, "relations_both": len(both),
                     "relations_full_only": len(f["relations"] - both), "relations_prefix_only": len(p["relations"] - both)})
    out = {"full": a.full, "prefix": a.prefix, "t": t, "shared_pages": len(shared), "same_title": same_title,
           "same_alias_list": same_alias, "later_only_aliases": later_only, "relation_lines_both": rel_both,
           "relation_lines_full_only": rel_full, "relation_lines_prefix_only": rel_prefix, "pages": rows}
    dest = ROOT / "docs" / "eval" / "a1" / f"{a.full}_vs_{a.prefix}_v{t}_shared.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{a.full} vs {a.prefix} t={t}: {len(shared)} shared character pages; same title {same_title}; same alias list "
          f"{same_alias}; later-only aliases on the full build {len(later_only)} {later_only[:8]}; relation lines both "
          f"{rel_both}, full only {rel_full}, prefix only {rel_prefix} -> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
