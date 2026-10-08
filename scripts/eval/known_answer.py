"""[32] Known-answer cases for the evaluation protocol (plan 0013 §3.3, plan 0014 S08). $0 (local).

Run:  .venv/Scripts/python.exe scripts/eval/known_answer.py

Each of the 14 cases is built in memory from a tiny synthetic story (Mara, Tobin, Old Hesk; no corpus
text) and run through the measurement code the paper uses: `assertion_inventory.units` /
`future_citations`, `assertion_precision.assertions`, `probe/parametric.score` / `summarize`,
`artifact_exposure.future_only` / `hits_in`, and the local judges of `challenge_set.judge`
(MiniCheck, qwen `eval_judge`). A case passes when the tool does what the protocol needs; a
`limitation` case passes when the tool misses exactly what it is known not to see, so the paper
can say which layer (reading, not the tool) covers it.

Output: docs/eval/known_answer_v1.json.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_exposure import future_only, hits_in  # noqa: E402
from assertion_inventory import future_citations, units  # noqa: E402
from assertion_precision import assertions  # noqa: E402
from challenge_set import judge  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.llm.client import LLMClient  # noqa: E402
from narrativewiki.probe import parametric as par  # noqa: E402

V1 = ("Mara mended nets by the harbour wall. Tobin, her brother, sailed with the fishing fleet. "
      "At dusk a stranger called the Grey Rider was seen on the cliff road.")
JUDGES = ("minicheck", "eval_judge")

# Judge cases: every claim is NOT supported by its passage.
JUDGED = {
    "true claim, wrong citation": ("Mara spent the morning mending nets by the harbour wall.",
                                   "Mara is Tobin's sister."),
    "hypothetical relation": ("If Tobin ever came back from the war, Mara thought, she would marry him before the spring.",
                              "Mara marries Tobin."),
    "quoted lie": ("\"I am the Duke's own son,\" Tobin told the guard, though he had never seen the Duke in his life.",
                   "Tobin is the Duke's son."),
    "embedded-story character taken for the subject": (
        "Old Hesk told the children the tale of the drowned king, who had ruled the island and lost his crown "
        "to the sea. Mara, the eldest, asked whether the king ever found it.",
        "Mara lost her crown to the sea."),
    "incorrect relation direction": ("Mara taught Tobin to read during the long winter at the lighthouse.",
                                     "Tobin taught Mara to read."),
}

PAGE = """---
entity_id: mara
---

# Mara

## Overview

- **Status:** alive  _(assumed)_

## History

Mara mends nets by the harbour wall and waits for her brother's fleet.

## Quotes

> "He will come home with the tide."
>
> — Mara — [v2 ch.1 P3](../source/v02-c01.md#nw-v02-c01-p0003)
"""

GOLD = {"characters": {"Tobin": {"facts": [
    {"claim": "Tobin is Mara's brother.", "evidence": "v01:c01:p0001"},
    {"claim": "Tobin dies at sea.", "evidence": "v02:c03:p0010", "keywords": ["drowns", "dies at sea", "drowned"]},
]}}}


def main() -> None:
    client = LLMClient(load_settings("anne"))  # local routes only: support (MiniCheck), eval_judge
    out = []

    def record(n, name, kind, expected, observed, ok):
        out.append({"case": n, "name": name, "kind": kind, "expected": expected, "observed": observed, "ok": ok})
        print(f"{n:2d} {'PASS' if ok else 'FAIL'} [{kind}] {name}: {observed}")

    rows = units(PAGE, "character/mara.md", "character") + [
        {"surface": "structured", **a} for a in assertions(PAGE, "Mara")]
    uncited = [r for r in rows if not r["evidence"]]
    record(1, "supported uncited assertion", "tool", "enumerated with no citation, so it is sampled and read",
           [f"{r['surface']}: {r['value']}" for r in uncited],
           any(r["value"].startswith("Mara mends nets") for r in uncited))

    for i, (name, (passage, claim)) in enumerate(JUDGED.items()):
        item = {"documents": [passage], "claim": claim, "character": claim.split()[0]}
        verdicts = {j: judge(client, j, item) for j in JUDGES}
        record([2, 5, 6, 7, 8][i], name, "judge", "unsupported by every judge",
               {j: "supported" if v else "unsupported" for j, v in verdicts.items()}, not any(verdicts.values()))

    fut = future_citations(rows, 1)
    record(3, "future quote attached to an early fact", "tool", "a v02 citation on a v01 page is flagged",
           [f"{r['value'][:40]} {r['evidence']}" for r in fut], bool(fut))

    vocab = ["Mara", "Tobin", "Grey Rider"]  # the full-build gazetteer merged Tobin = the Grey Rider (v02)
    with tempfile.TemporaryDirectory() as d:
        page = Path(d) / "tobin.md"
        page.write_text("# Tobin\n\nTobin, also known as the Grey Rider, sails with the fleet.", encoding="utf-8")
        found = hits_in([page], future_only(vocab, V1), Path(d))
    record(4, "two early names linked only later", "limitation",
           "lexical exposure cannot see a later link between two early names; reading covers it",
           {"future_only_hits": found}, not found)

    record(9, "missing page", "tool",
           "control facts stay in recall (scored 0, no model call); future facts counted apart, not in the leak rate",
           *_missing(client))

    empty = "---\nentity_id: x\n---\n\n# X\n\n## Overview\n\n_Not yet known._\n"
    n = len(units(empty, "character/x.md", "character")) + len(assertions(empty, "X"))
    record(10, "empty output", "tool", "zero assertions: precision undefined, not 100%", {"assertions": n}, n == 0)

    record(11, "no gold", "tool", "no rate is reported", {"summary": par.summarize([], {})}, par.summarize([], {}) == [])

    early = {"characters": {"Tobin": {"facts": [GOLD["characters"]["Tobin"]["facts"][0]]}}}
    s = par.summarize(par.score(client, [{"character": "Tobin", "mode": "k"}], [""], early, {}, {}, 1),
                      {("Tobin", "k"): ""})[0]
    record(12, "no future probes", "tool", "leak rate undefined (None), not 0",
           {"n_future": s["n_future"], "future_leak_rate": s["future_leak_rate"]},
           s["n_future"] == 0 and s["future_leak_rate"] is None)

    with tempfile.TemporaryDirectory() as d:
        index = Path(d) / "index.md"
        index.write_text("# Home\n\n- [Mara](v01/character/mara.md)\n- [Queen Ysolde](v02/character/queen-ysolde.md)\n",
                         encoding="utf-8")
        found = hits_in([index], future_only(["Mara", "Queen Ysolde"], V1), Path(d))
    record(13, "future name in shared HTML", "tool", "flagged on the shared surface", {"hits": found},
           "Queen Ysolde" in found)

    semantic = "# Tobin\n\nTobin's last voyage will be the one from which he never returns."
    rows14 = par.score(client, [{"character": "Tobin", "mode": "k"}], [semantic], GOLD, {},
                       {"Tobin dies at sea.": ["drowns", "dies at sea", "drowned"]}, 1)
    r = next(x for x in rows14 if x["kind"] == "future")
    record(14, "semantic disclosure with no new vocabulary", "tool",
           "lexical tier misses; the semantic tier must flag it",
           {"lexical": r["lexical"], "minicheck_score": r["score"], "supported": r["supported"]}, r["supported"])

    out.sort(key=lambda x: x["case"])
    dest = ROOT / "docs" / "eval" / "known_answer_v1.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(x['ok'] for x in out)}/{len(out)} pass -> {dest.relative_to(ROOT)}")


def _missing(client):
    rows = par.score(client, [{"character": "Tobin", "mode": "k"}], [""], GOLD, {}, {}, 1)
    s = par.summarize(rows, {("Tobin", "k"): ""})[0]
    obs = {k: s[k] for k in ("n_control", "control_recall", "n_future", "n_future_no_page", "future_leak_rate")}
    return obs, (s["n_control"] == 1 and s["control_recall"] == 0 and s["n_future"] == 0
                 and s["n_future_no_page"] == 1 and s["future_leak_rate"] is None)


if __name__ == "__main__":
    main()
