"""[33] Audit a decontamination name map before any paraphrase call is paid for. $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/decon_map_audit.py <decon series> [--volumes 1-5] [--min 3]

A map can be wrong in three ways that `decontaminate.residual` (original KEYS left in the text)
does not see, all found on anne-decon 2026-10-02 after volume 1 had been paraphrased:

  collision   a stand-in is itself a name in the original text ("Marilla" -> "Hester" while Hester
              Gray and Hester Reese are characters): two people now share a name.
  leftover    a multi-word key maps a full name ("Ellen West" -> "Harriet Ainslie") while the bare
              first name or surname stays ("Ellen" x148): one person now has two unrelated names.
  duplicate   two different originals map to the same stand-in.

Prints each with counts and the commonest contexts; exit 1 when a collision or a leftover with at
least `--min` occurrences remains, so a runner can gate the paraphrase on it. Words in GENERIC are
titles and common nouns that stand-ins may share with the text.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki import paths  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.ingest.decontaminate import remap  # noqa: E402

GENERIC = set("Aunt Uncle Captain Mrs. Mr. Miss Dr. St. Lady Elder Reverend Mary Glen College Academy Avenue Place "
              "Road Street Path Lane Wood Well Hill Hollow Cottage Lodge Waters Sands River Island Port Way Walk "
              "Dell Vale of the The Delight Maiden Shutters Grey Pale Seven Gulls Fern Pear Whispering Glassy "
              "Echo Linden West East Frank Rose Dan John Judson Jordan Stephen Fourth Fitzgerald Agnes Frost Four "
              "White Haunted Gray May Prince E. Queen Snow Valley Rainbow Orchard Shining Green Wendell Lennox".split())


def audit(series: str, volumes: range, minimum: int) -> int:
    cfg = load_settings(series).series["decontaminate"]
    emap, source = cfg["entity_map"], cfg["from_series"]
    paths.set_active_series(source)
    text = " ".join(json.loads(line)["text"] for v in volumes
                    for line in paths.parsed_volume(v).read_text(encoding="utf-8").splitlines() if line.strip())
    treated = remap(text, emap)
    bad = 0

    def count(word: str, hay: str) -> int:
        return len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", hay))

    def contexts(word: str, hay: str) -> str:
        hits = re.findall(rf"(\w+\.?\s+)?(?<!\w){re.escape(word)}(?!\w)(\s+\w+)?", hay)
        c = collections.Counter(f"{(a or '').strip()} {word} {(b or '').strip()}".strip() for a, b in hits)
        return "; ".join(f"{k} x{n}" for k, n in c.most_common(4))

    print(f"{series}: {len(emap)} map entries over {source} v{volumes[0]}-{volumes[-1]}")
    seen: dict[str, str] = {}
    for original, stand in emap.items():
        if original == stand:  # an identity entry protects a real-world name from a shorter key
            continue
        for word in stand.split():
            if word in GENERIC or len(word) < 3 or word in seen or not word[:1].isupper():
                continue
            seen[word] = original
            n = count(word, text)
            if n:
                bad += 1
                print(f"  COLLISION  {original} -> {stand}: '{word}' occurs x{n} in the original ({contexts(word, text)})")
    owners = collections.defaultdict(set)
    for original, stand in emap.items():
        owners[stand].add(original)
    for stand, originals in owners.items():
        if len({o.split()[-1] for o in originals}) > 1:  # spellings of one name share their last word
            print(f"  duplicate  {sorted(originals)} -> {stand}")
    done = set()
    for original in emap:
        parts = original.split()
        if len(parts) < 2:
            continue
        for word in parts:
            if word in emap or word in GENERIC or word in done or not word[:1].isupper():
                continue
            done.add(word)
            n = count(word, treated)
            if n >= minimum:
                bad += 1
                print(f"  LEFTOVER   '{word}' (from {original} -> {emap[original]}) still x{n}: {contexts(word, treated)}")
    print(f"  {bad} blocking finding(s)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--volumes", default="1-5")
    ap.add_argument("--min", type=int, default=3)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    lo, hi = (int(x) for x in a.volumes.split("-"))
    return audit(a.series, range(lo, hi + 1), a.min)


if __name__ == "__main__":
    raise SystemExit(main())
