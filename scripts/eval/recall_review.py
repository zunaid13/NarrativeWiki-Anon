"""[32] Hand-read recall (and leak) review sheet for one series at one cutoff. $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/recall_review.py <series> <t>
      .venv/Scripts/python.exe scripts/eval/recall_review.py <series> <t> --score

For every gold fact in docs/eval/parametric/<series>.yaml, prints the character's page lines at
cutoff t that share the most content words with the fact, and the tool reading from
docs/eval/pipeline_leak_<series>_v<t>.json. A person decides, and writes the verdicts to
docs/eval/recall_hand/<series>_v<t>.json as {"C <character> | <claim>": "yes" | "no"} (C = control,
F = future fact at t).
`--score` prints hand recall over control facts (first_vol <= t) and hand leak over future
facts (first_vol > t). MiniCheck read Anne's t=1 recall as 2/8 where a person read 5/8
(MEASUREMENTS §47), so only the hand numbers are headline numbers.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.probe.parametric import gold_pages  # noqa: E402

STOP = set("the a an of to and in on for with his her their is was be by as at from that this".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2 and w not in STOP}


def main() -> None:
    series, t = sys.argv[1], int(sys.argv[2])
    from narrativewiki.config import load_settings  # a trial or baseline series scores against its base's gold
    cfg = load_settings(series).series
    name = cfg.get("gold_from") or (cfg.get("decontaminate") or {}).get("from_series") or series
    gold = yaml.safe_load((ROOT / "docs" / "eval" / "parametric" / f"{name}.yaml").read_text(encoding="utf-8"))
    hand_path = ROOT / "docs" / "eval" / "recall_hand" / f"{series}_v{t}.json"
    if "--score" in sys.argv:
        hand = json.loads(hand_path.read_text(encoding="utf-8"))
        ctrl = [v for k, v in hand.items() if k.startswith("C ")]
        fut = [v for k, v in hand.items() if k.startswith("F ")]
        print(f"{series} t={t}: recall {ctrl.count('yes')}/{len(ctrl)}; leak {fut.count('yes')}/{len(fut)}")
        return
    tool = {}
    lf = ROOT / "docs" / "eval" / f"pipeline_leak_{series}_v{t}.json"
    if lf.is_file():
        for r in json.loads(lf.read_text(encoding="utf-8"))["rows"]:
            tool[(r["character"], r["claim"])] = r["supported"] or r["lexical"]
    for name, entry in gold["characters"].items():
        found = gold_pages(series, name, ROOT / "dist" / series / "wiki" / f"v{t:02d}")
        lines = []
        for page in found:  # [33] union of the system's pages for this gold character
            body = page.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
            lines += [re.sub(r"\s*<sub>.*?</sub>|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", ln).strip()
                      for ln in body.splitlines() if ln.strip()]
        for fact in entry["facts"]:
            fv = int(fact["evidence"][1:3])
            kind = "C" if fv <= t else "F"
            fw = words(fact["claim"])
            best = sorted(lines, key=lambda ln: -len(fw & words(ln)))[:3]
            print(f"\n[{kind}] {name} | {fact['claim']}   (v{fv}; tool={tool.get((name, fact['claim']))})")
            if not found:
                print("    (no page at this cutoff)")
            for ln in best:
                print("    >", ln[:260])


if __name__ == "__main__":
    main()
