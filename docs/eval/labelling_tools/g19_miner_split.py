"""g19_miner_split.py: why a prefix-only candidate miner lacks candidates of the five-volume build (OPEN_GAPS G19). $0, writes nothing.

Run from the repository root:  .venv/Scripts/python.exe docs/eval/labelling_tools/g19_miner_split.py
"""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"")
sys.path.insert(0, str(ROOT / "src"))
import yaml
from narrativewiki.entities import candidates as C

cfg = yaml.safe_load((ROOT / "config/series.anne.yaml").read_text(encoding="utf-8"))
vols = {v: [json.loads(l) for l in (ROOT / f"data/anne/01_parsed/v{v:02d}.jsonl").open(encoding="utf-8")] for v in range(1, 6)}
full = [json.loads(l) for l in (ROOT / "data/anne@v2/02_entities/candidates.jsonl").open(encoding="utf-8")]
gaz = json.loads((ROOT / "data/anne@v2/02_entities/_premerge/gazetteer.json").read_text(encoding="utf-8"))
kept = {f["text"] for e in gaz["entities"] for f in e["surface_forms"]}  # surfaces the full build made entities of
ratio = C._ALWAYS_CAPITALISED_RATIO


def mine(upto, r, mm=3):
    C._ALWAYS_CAPITALISED_RATIO = r
    try:
        return {c["surface"]: c for c in C.mine_candidates({v: vols[v] for v in range(1, upto + 1)}, cfg, mm)}
    finally:
        C._ALWAYS_CAPITALISED_RATIO = ratio


base5 = mine(5, ratio)
print("full build reproduced:", len(base5), "candidates;", len(full), "on disk; same set:", set(base5) == {c["surface"] for c in full})
loose5 = mine(5, 0.0)
print("full build with the ratio rule off:", len(loose5), "candidates (+%d)" % (len(loose5) - len(base5)),
      "new:", sorted(set(loose5) - set(base5))[:25])
for t in range(1, 5):
    seen = {c["surface"] for c in full if c["first_vol"] <= t}
    a = mine(t, ratio); b = mine(t, 0.0); c1 = mine(t, 0.0, 1)
    lost = seen - set(a)
    by_ratio = lost & set(b)                 # enough mentions in the prefix; only the ratio rule drops it
    few = (lost - set(b)) & set(c1)          # fewer than three mentions in the prefix
    other = lost - set(b) - set(c1)
    ent = lambda s: sum(x in kept for x in s)
    print(f"t={t}: full-build candidates seen by t {len(seen)}; not mined from the prefix {len(lost)} "
          f"= ratio rule {len(by_ratio)} (entities' forms {ent(by_ratio)}) + under three mentions {len(few)} ({ent(few)}) + other {len(other)} ({ent(other)})")
    print("     ratio rule, kept as entity forms:", sorted(x for x in by_ratio if x in kept)[:30])
    print("     other:", sorted(other)[:12])

print("--- check: every non-ratio loss has under three mentions in the prefix")
big = {**cfg, "entities": {**(cfg.get("entities") or {}), "max_candidates_per_volume": 10**6}}
for t in range(1, 5):
    seen = {c["surface"] for c in full if c["first_vol"] <= t}
    a = mine(t, ratio)
    C._ALWAYS_CAPITALISED_RATIO = 0.0
    allc = {c["surface"]: c["count"] for c in C.mine_candidates({v: vols[v] for v in range(1, t + 1)}, big, 1)}
    b3 = {s for s, n in allc.items() if n >= 3}
    C._ALWAYS_CAPITALISED_RATIO = ratio
    lost = seen - set(a)
    few = [s for s in lost if allc.get(s, 0) < 3]
    rule = [s for s in lost if allc.get(s, 0) >= 3]
    print(t, len(lost), "ratio or other rule with >=3 mentions:", len(rule), "under three:", len(few), "absent entirely:", sum(s not in allc for s in lost))
