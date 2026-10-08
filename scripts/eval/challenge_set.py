"""[32] Verifier / judge challenge set (plan 0014 S08, plan 0013 §3.3). Building and local scoring: $0.

Run:  .venv/Scripts/python.exe scripts/eval/challenge_set.py build [--n 50] [--seed 0]
      .venv/Scripts/python.exe scripts/eval/challenge_set.py score --judge minicheck|eval_judge

Positives are hand-written gold facts (`docs/eval/parametric/<work>.yaml`) with their evidence
paragraph and one neighbour each side in the same chapter. Negatives are minimal-word perturbations
of the same facts (NarrativeFactScore, arXiv 2501.09993 E.6), made on local Ollama (`eval_perturb`),
one type per fact, cycling: entity swap, attribute change, negation, relation reversal.
Only public-domain works go in (the set may be released); Oz is held out and never used.

Nothing is scored until a reader confirms it: `confirmed` true means the positive is supported by
its documents / the negative is false against them; false drops the item. `reader` names who read
it (plan 0014 D1). `score` reports TPR, TNR and balanced accuracy over confirmed items, with TNR
per perturbation type, and writes each verdict into the item under `judges.<name>`.

Hard set (plan 0014 S08): docs/eval/challenge/challenge_hard_v1.jsonl, hand-written from the text —
quoted lie, hypothetical, story within the story — each negative paired with a true twin from the
same passages; score it with `score --judge <j> --file <that path>`.

Output: docs/eval/challenge/challenge_v1.jsonl, one item per line:
  {id, work, character, evidence, first_vol, documents, claim, label: supported|unsupported,
   type: gold|entity|attribute|negation|reversal, source_claim, confirmed, reader, note, judges}
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel  # noqa: E402

from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.llm.client import LLMClient  # noqa: E402
from narrativewiki.probe.parametric import facts_of  # noqa: E402

WORKS = ("anne", "middlemarch", "leagues")
TYPES = {
    "entity": "replace one named person, place or thing with a different one from the same story",
    "attribute": "change one descriptive detail (a number, time, colour, manner, quality or object)",
    "negation": "make the sentence state the opposite (insert a negation, or swap an action for its opposite)",
    "reversal": "swap who does what to whom, so the relation runs the other way",
}
OUT = ROOT / "docs" / "eval" / "challenge" / "challenge_v1.jsonl"


def documents(work: str, pid: str) -> list[str]:
    """The evidence paragraph with one neighbour each side, same chapter, in reading order."""
    vol, chap, para = pid.split(":")
    rows = {}
    for line in (ROOT / "data" / work / "01_parsed" / f"{vol}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["para_id"].startswith(f"{vol}:{chap}:"):
            rows[int(r["para_id"][-4:])] = r["text"]
    p = int(para[1:])
    if p not in rows:
        raise KeyError(f"{work} {pid}: evidence paragraph not in 01_parsed")
    return [rows[i] for i in (p - 1, p, p + 1) if i in rows]


class Perturbed(BaseModel):
    perturbed: str


def perturb(client: LLMClient, claim: str, kind: str) -> str:
    prompt = ("This sentence is a fact about a novel. Rewrite it by minimally replacing a few words in "
              "the original sentence to render it factually inaccurate, while keeping the sentence "
              f"structure and length. Perturbation: {TYPES[kind]}.\n\nSentence: {claim}\n\n"
              'Respond with JSON only: {"perturbed": "..."}')
    return client.complete_json("eval_perturb", prompt, Perturbed).perturbed.strip()


def build(n: int, seed: int) -> None:
    if OUT.is_file() and any(json.loads(l).get("confirmed") is not None for l in OUT.read_text(encoding="utf-8").splitlines()):
        sys.exit(f"{OUT.relative_to(ROOT)} already holds confirmations; not overwriting")
    pool = []
    for work in WORKS:
        gold = yaml.safe_load((ROOT / "docs" / "eval" / "parametric" / f"{work}.yaml").read_text(encoding="utf-8"))
        for character, entry in gold["characters"].items():
            pool += [{"work": work, "character": character, **f} for f in facts_of(entry)
                     if f["first_vol"] <= 5]  # the evaluated scope is volumes 1-5 (Anne has a v06)
    rng = random.Random(seed)
    per = {w: [f for f in pool if f["work"] == w] for w in WORKS}
    picked = [f for i, w in enumerate(WORKS) for f in rng.sample(per[w], min(len(per[w]), n // len(WORKS) + (i < n % len(WORKS))))]
    client = LLMClient(load_settings(WORKS[0]))  # local route only; no run ledger for a $0 build
    items = []
    for i, f in enumerate(picked):
        docs = documents(f["work"], f["evidence"])
        kind = list(TYPES)[i % len(TYPES)]
        base = {"work": f["work"], "character": f["character"], "evidence": f["evidence"],
                "first_vol": f["first_vol"], "documents": docs, "source_claim": f["claim"],
                "confirmed": None, "reader": None, "note": "", "judges": {}}
        items.append({"id": f"g{i:03d}", **base, "claim": f["claim"], "label": "supported", "type": "gold"})
        items.append({"id": f"n{i:03d}", **base, "claim": perturb(client, f["claim"], kind),
                      "label": "unsupported", "type": kind})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
    print(f"{len(items)} items ({len(picked)} gold + {len(picked)} perturbed) -> {OUT.relative_to(ROOT)}")


class Verdict(BaseModel):
    supported: bool
    reason: str


def paragraph(work: str, pid: str) -> str:
    vol = pid.split(":")[0]
    for line in (ROOT / "data" / work / "01_parsed" / f"{vol}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["para_id"] == pid:
            return r["text"]
    raise KeyError(f"{work} {pid}: not in 01_parsed")


def judge(client: LLMClient, name: str, item: dict) -> bool:
    if name == "verifier":
        # [33] M12: the PRODUCTION verifier (graph/verify.py, stage `verify`, the frozen generator):
        # the claim as one fact, its evidence paragraph as the quote, the neighbours as the nearby
        # context the verifier already receives in a build.
        from narrativewiki.graph.verify import verify_fact
        ev = paragraph(item["work"], item["evidence"])
        fact = {"predicate": "STATEMENT", "value": item["claim"], "qualifier": None,
                "evidence": [{"para_id": item["evidence"], "quote": ev}]}
        ctx = {item["evidence"]: [{"para_id": f"{item['evidence']}~{i}", "text": d}
                                  for i, d in enumerate(item["documents"]) if d != ev]}
        return verify_fact(item["character"], fact, client, context=ctx) is None
    if name == "minicheck":
        return client.score_support(item["documents"], item["claim"])["score"] >= 0.5  # extraction.yaml support.threshold
    # eval_judge: the same local pre-screen assertion_precision.py uses
    passages = "\n\n".join(item["documents"])
    prompt = (f"Claim about {item['character']}: {item['claim']}\n\nPassages:\n{passages}\n\n"
              'Do the passages support the claim? Respond with JSON only: {"supported": true/false, "reason": "one sentence"}')
    system = ("You check claims about a novel against passages from it. Answer only from the passages. "
              "A claim is supported when the passages state it or directly show it; otherwise it is not.")
    return client.complete_json("eval_judge", prompt, Verdict, system=system).supported


def score(name: str, path: Path = OUT) -> None:
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    settings, run = load_settings(WORKS[0]), None
    if name == "verifier":  # billed: every call goes into a run ledger so the lifetime cap sees it
        from narrativewiki import provenance
        run = provenance.start_run(WORKS[0], "challenge_verify", scope=path.stem, volumes=[5],
                                   volume_scope=5, stage_keys=[], settings=settings)
    client = LLMClient(settings, run=run, volume_scope=5 if run else None)
    for it in items:
        if it["confirmed"]:
            it["judges"][name] = judge(client, name, it)
    if run:
        run.finish("ok")
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
    ok = [it for it in items if it["confirmed"]]
    pos = [it for it in ok if it["label"] == "supported"]
    neg = [it for it in ok if it["label"] == "unsupported"]
    tpr = sum(it["judges"][name] for it in pos) / len(pos)
    tnr = sum(not it["judges"][name] for it in neg) / len(neg)
    print(f"{name}: {len(ok)} confirmed items ({len(pos)} supported, {len(neg)} unsupported; "
          f"{len(items) - len(ok)} unconfirmed or dropped) | TPR {tpr:.3f} TNR {tnr:.3f} "
          f"balanced accuracy {(tpr + tnr) / 2:.3f}")
    for kind in dict.fromkeys(it["type"] for it in neg):  # perturbation types, or the hard set's types
        ks = [it for it in neg if it["type"] == kind]
        if ks:
            print(f"  {kind}: rejected {sum(not it['judges'][name] for it in ks)}/{len(ks)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "score"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--judge", choices=["minicheck", "eval_judge", "verifier"])
    ap.add_argument("--file", type=Path, default=OUT, help="e.g. docs/eval/challenge/challenge_hard_v1.jsonl")
    args = ap.parse_args()
    build(args.n, args.seed) if args.cmd == "build" else score(args.judge, args.file)


if __name__ == "__main__":
    main()
