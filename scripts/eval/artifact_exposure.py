"""[33] Artifact exposure (plan 0012 D7): do the files a cutoff-t reader can open name anything
the text up to t never names?

Run:  .venv/Scripts/python.exe scripts/eval/artifact_exposure.py <series> [--upto N]

Future-only vocabulary at t: every gazetteer canonical name, alias and surface form (epithets
included) whose text does not occur, case-insensitively on word boundaries, anywhere in the
parsed volumes 1..t. Each is searched for in three surfaces:
  cutoff  -- dist/<s>/wiki/v0t/** (the pages, codex, relationships, timeline, source views)
  shared  -- dist/<s>/wiki/index.md and dist/<s>/mkdocs.yml (every cutoff's reader sees them)
  html    -- dist/<s>/wiki-html/v0t/** plus every built file outside the vNN dirs (home, 404,
             assets, a search index if one exists)
  slugs   -- the same strings slugified ("Jerry Meredith" -> jerry-meredith) in the cutoff's file
             paths and in the link targets of its files: a file name or URL can name what the
             prose never does (plan 0014 S13: titles and slugs, links)
A hit is a lead, not a verdict: a future-only string on a page is disclosure only if a reader
could tell what it names. Zero LLM calls. Output: docs/eval/artifact_exposure_<series>.json.
A baseline variant (`anne@b1`) is measured with its base work's gazetteer vocabulary, so every
system is searched for the same strings.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki import paths  # noqa: E402
from narrativewiki.entities.gazetteer import slugify  # noqa: E402

LINK_RE = re.compile(r"\]\(([^)\s]+)\)|href=\"([^\"]+)\"")


def _pattern(s: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(s)}(?!\w)", re.I)


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix in {".md", ".html", ".js", ".json", ".yml", ".xml"}]


def future_only(vocab: list[str], text: str) -> list[str]:
    """Strings a reader of `text` has never seen."""
    return [s for s in vocab if not _pattern(s).search(text)]


def hits_in(files: list[Path], future: list[str], base: Path) -> dict[str, list[str]]:
    """future-only string -> the files (relative to base) that contain it."""
    hits: dict[str, list[str]] = {}
    for f in files:
        body = f.read_text(encoding="utf-8", errors="replace")
        for s in future:
            if _pattern(s).search(body):
                hits.setdefault(s, []).append(str(f.relative_to(base)))
    return hits


def slug_hits(files: list[Path], future: list[str], base: Path) -> dict[str, list[str]]:
    """future-only string -> cutoff files whose path or link targets carry its slug."""
    slugs = {slugify(s): s for s in future if len(slugify(s)) >= 3}
    pat = {sl: re.compile(rf"(?<![a-z0-9]){re.escape(sl)}(?![a-z0-9])") for sl in slugs}
    hits: dict[str, list[str]] = {}
    for f in files:
        rel = str(f.relative_to(base)).replace("\\", "/")
        targets = " ".join(a or b for a, b in LINK_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
        for sl, rx in pat.items():
            if rx.search(rel) or rx.search(targets):
                hits.setdefault(slugs[sl], []).append(rel)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, default=5)
    args = ap.parse_args()
    paths.set_active_series(args.series)
    base = paths.variant_base(args.series) or args.series
    gaz = json.loads((ROOT / "data" / base / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    vocab = sorted({s.strip() for e in gaz["entities"]
                    for s in [e["canonical"], *e.get("aliases", []), *(sf["text"] for sf in e.get("surface_forms", []))]
                    if len(s.strip()) >= 3})
    dist = ROOT / "dist" / args.series
    shared = [p for p in (dist / "wiki" / "index.md", dist / "mkdocs.yml") if p.is_file()]
    html_root = dist / "wiki-html"
    # The theme's own assets are not ours: lunr's Hungarian stopword list contains "Sem".
    html_all = [f for f in _files(html_root) if f.relative_to(html_root).parts[0] != "assets"] \
        if html_root.is_dir() else []
    text = ""
    rows = []
    for t in range(1, args.upto + 1):
        text += "\n" + "\n".join(json.loads(line)["text"] for line in
                          paths.parsed_volume(t).read_text(encoding="utf-8").splitlines() if line.strip())
        future = future_only(vocab, text)
        html = [f for f in html_all if not re.fullmatch(r"v\d\d", f.relative_to(html_root).parts[0])
                or f.relative_to(html_root).parts[0] == f"v{t:02d}"]
        surfaces = {"cutoff": _files(dist / "wiki" / f"v{t:02d}"), "shared": shared, "html": html}
        row = {"t": t, "future_only_vocabulary": len(future)}
        for name, files in [*surfaces.items(), ("slugs", surfaces["cutoff"])]:
            hits = slug_hits(files, future, dist) if name == "slugs" else hits_in(files, future, dist)
            row[name] = {"files": len(files), "strings_hit": len(hits),
                         "hits": {s: sorted(fs)[:5] for s, fs in sorted(hits.items())}}
        rows.append(row)
        print(f"t={t}: {len(future)} future-only strings; hits cutoff {row['cutoff']['strings_hit']} "
              f"({row['cutoff']['files']} files) | shared {row['shared']['strings_hit']} | html {row['html']['strings_hit']} "
              f"| slugs {row['slugs']['strings_hit']}")
    out = ROOT / "docs" / "eval" / f"artifact_exposure_{args.series}.json"
    out.write_text(json.dumps({"series": args.series, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
