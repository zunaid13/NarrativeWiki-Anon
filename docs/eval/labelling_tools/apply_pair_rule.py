"""apply_pair_rule.py <series> <system> [--out NAME]: put the two label sidecars into a system's observations. $0.

Run (after scripts/eval/paper_observations.py has written docs/eval/observations/<series>_<system>.jsonl; the
step is not idempotent, so regenerate the observations first):
  .venv/Scripts/python.exe docs/eval/labelling_tools/apply_pair_rule.py anne@v2 B5
  .venv/Scripts/python.exe docs/eval/labelling_tools/apply_pair_rule.py anne B5 --out anne_B5_scene

M3. `paper_observations.py` takes the page verdict of a sampled row from its `human` field. For the main system's
relationship pages that field was written by two different passes under two different rules (CHANGES C38, C39).
This step replaces it, for relationship rows only, by: supported iff the factual verdict (`human_atom`) is
supported AND the pair shares an episode of the scene (`scene` verdict in
docs/eval/precision/pair_strict_claude.json, one reader, one written rule, every version).

M8. The inventory tool did not attach the "_Sources:" line a version-2 timeline page prints to the line above it,
so sampled timeline rows carry `cite: none` (CHANGES C40). Their verdict is replaced by the reading of the
paragraphs the delivered page cites (docs/eval/precision/timeline_cite_claude.json).

Everything else in the observations file is left as it is. Observations are matched to sample rows by order
within a cutoff (one M3 observation per row that is not a frame error, one M8 observation per such row with a
`cite` verdict); a label mismatch stops the run.
"""
import json
import sys
from pathlib import Path

EV = Path(__file__).resolve().parents[1]
series, system = sys.argv[1], sys.argv[2]
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else f"{series}_{system}"
obs = [json.loads(l) for l in (EV / "observations" / f"{series}_{system}.jsonl").open(encoding="utf-8")]


def side(name):
    return json.loads((EV / "precision" / name).read_text(encoding="utf-8")).get(series, {})


def kept_rows(t):
    rows = [json.loads(l) for l in (EV / "precision" / f"{series}_v{t}_all.jsonl").open(encoding="utf-8")]
    return [(i, r) for i, r in enumerate(rows) if r.get("human") and r.get("human") != "frame_error"]


changed = 0
for t, verdicts in side("pair_strict_claude.json").items():
    kept = kept_rows(t)
    m3 = [o for o in obs if o["metric"] == "M3" and o["cutoff"] == int(t)]
    assert len(m3) == len(kept), (t, len(m3), len(kept))
    for o, (i, r) in zip(m3, kept):
        assert o["label"] == r["human"], (t, i, o["label"], r["human"])
        if str(i) not in verdicts:
            continue
        ok = r.get("human_atom") == "supported" and verdicts[str(i)]["scene"] == "yes"
        new = "supported" if ok else ("partial" if r.get("human_atom") == "supported" else r["human"])
        changed += (o["num"] == 1) != ok
        o["label_first_pass"], o["label"], o["num"] = o["label"], new, int(ok)
        o["pair_rule"] = "scene (pair_strict_claude.json; agent:claude-opus-5-5; not human)"

cite_changed = 0
for t, verdicts in side("timeline_cite_claude.json").items():
    kept = [(i, r) for i, r in kept_rows(t) if (r.get("cite") or "").strip().lower() not in ("", "frame_error")]
    m8 = [o for o in obs if o["metric"] == "M8" and o["cutoff"] == int(t)]
    assert len(m8) == len(kept), (t, len(m8), len(kept))
    for o, (i, r) in zip(m8, kept):
        assert o["label"] == r["cite"].strip().lower(), (t, i, o["label"], r["cite"])
        if str(i) not in verdicts:
            continue
        assert o["label"] == "none" and r["page"].startswith("timeline/"), (t, i, o["label"], r["page"])
        new = verdicts[str(i)]["cite"]
        cite_changed += new == "sufficient"
        o["label_first_pass"], o["label"], o["num"] = o["label"], new, int(new == "sufficient")
        o["reader"] = "agent:claude-opus-5-5 (not human; citations read from the delivered timeline page)"

(EV / "observations" / f"{out}.jsonl").write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in obs),
                                                  encoding="utf-8")
m3 = [o for o in obs if o["metric"] == "M3"]
m8 = [o for o in obs if o["metric"] == "M8"]
print(f"{series} {system}: {len(m3)} M3 observations, {changed} changed -> observations/{out}.jsonl; "
      f"supported {sum(o['num'] for o in m3)}/{len(m3)}; M8 {cite_changed} timeline rows to sufficient, "
      f"sufficient {sum(o['num'] for o in m8)}/{len(m8)}")
