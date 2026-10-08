"""[33] S12 — turn hand-read review files into paper_numbers.py observations. $0, no model.

Run:  .venv/Scripts/python.exe scripts/eval/paper_observations.py <series> [--system B5] [--upto 5]
      [--work <name>] [--partial-supported]  → docs/eval/observations/<series>_<system>.jsonl

Only human (or explicitly reader-labelled) verdicts become observations — a tool's screen score is
never a result (MEASUREMENTS §47: MiniCheck read Anne's t=1 recall as 2/8, a person 5/8):

- **M1 recall**: `docs/eval/recall_hand/<series>_v<t>.json` ({"<character> | <claim>": "yes"|"no"}),
  control facts only (gold `first_vol <= t`, docs/eval/parametric/<gold>.yaml).
- **M4 future-fact exposure**: the same file's future facts (`first_vol > t`), plus
  `docs/eval/leak_audit/<series>.jsonl` rows (verdict stated/implied = disclosed, absent = not);
  a fact read in both places counts once, the leak audit winning (it is bound to a page sha).
  Future = `t < reveal <= --last-vol` (paper §3 F_t; a reveal past the corpus scope is not assessed).
  When `docs/eval/leak_audit/<series>_tree.jsonl` exists (R6, scripts/eval/m4_tree.py: every future
  fact read against every page of the cutoff tree) it is the M4 source instead; a row whose
  `tree_sha` differs from the tree on disk is stale. "uncertain" counts 0 and keeps its label.
- **M3 precision**: `docs/eval/precision/<series>_v<t>_all.jsonl` (the all-assertion sample, uncited
  included — paper §5; the cited-only `<series>_v<t>.jsonl` is not M3) rows with a `human` verdict;
  "supported" = 1, "unsupported"/"uncertain" = 0, "partial" = 0 unless `--partial-supported` (R3);
  "frame_error" rows leave the denominator and are counted (R2). Every M3 observation keeps its
  `label` and `label_atom` for the R3 sensitivity analyses. The same rows give **M8 citation sufficiency** when
  they carry a `cite` verdict (sufficient = 1; partial, insufficient and none = 0). Rows with no human verdict are skipped
  and counted. `weight` carries through when the sampler set one.

A verdict must judge the page on disk now (2026-09-30, CHANGES C12): a leak-audit row whose
`page_sha` differs from the current page, and a recall/precision file older than the newest page at
its cutoff, is skipped and counted as stale. Gold facts with no verdict are counted as unassessed.

Every observation keeps `reader` when the source names one, so a pilot (agent-read) label is never
mistaken for an independent human one (plan 0014 D1).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from narrativewiki.entities.gazetteer import slugify  # noqa: E402
from narrativewiki.probe.parametric import facts_of  # noqa: E402

EVAL = ROOT / "docs" / "eval"


def gold_first_vol(gold: dict) -> dict[tuple[str, str], int]:
    # [34] facts_of reads both gold shapes (`facts:`, and Leagues' older `future:`/`control:` lists)
    return {(ch, f["claim"]): f["first_vol"]
            for ch, body in (gold.get("characters") or {}).items() for f in facts_of(body)}


def observations(series: str, system: str, upto: int, work: str, gold: dict, *, page_sha, pages_mtime,
                 last_vol: int = 5, partial_supported: bool = False, eval_dir: Path = EVAL,
                 tree_sha=None) -> tuple[list[dict], dict]:
    """page_sha(t, character) -> sha of the current page(s) (leak_screen_audit.sha);
    pages_mtime(t) -> newest character page mtime at t. Both judge staleness (C12).
    tree_sha(t) -> the acceptable shas of the whole cutoff tree; with it, `leak_audit/<series>_tree.jsonl` (R6: every
    future fact against every page) is the M4 source and the per-page audit is not read."""
    fv = gold_first_vol(gold)
    obs, skipped = [], {"precision_unread": 0, "recall_unknown_fact": 0, "stale": 0, "past_scope": 0,
                         "frame_error": 0}
    leak_seen: set[tuple[int, str, str]] = set()
    judged: set[tuple[int, str, str]] = set()
    audit = eval_dir / "leak_audit" / f"{series}.jsonl"
    tree = eval_dir / "leak_audit" / f"{series}_tree.jsonl"
    if tree_sha and tree.exists():  # C20 / R6
        for line in tree.open(encoding="utf-8"):
            r = json.loads(line)
            t, ch, claim = int(r["t"]), r["character"], r["claim"]
            if t > upto:
                continue
            if r.get("tree_sha") not in tree_sha(t):
                skipped["stale"] += 1
                continue
            leak_seen.add((t, ch, claim))
            # "uncertain" is not a disclosure in the headline; `label` keeps it for the sensitivity run
            obs.append({"work": work, "system": system, "cutoff": t, "character": slugify(ch), "metric": "M4",
                        "num": int(r["verdict"] in ("stated", "implied")), "den": 1, "label": r["verdict"],
                        "reader": r.get("reader", "unrecorded"), "source": tree.name})
    elif audit.exists():
        for line in audit.open(encoding="utf-8"):
            r = json.loads(line)
            t, ch, claim = int(r["t"]), r["character"], r["claim"]
            if t > upto or fv.get((ch, claim), 0) <= t:
                continue  # control facts in the audit are recall material, read via recall_hand
            if fv[(ch, claim)] > last_vol:
                skipped["past_scope"] += 1
                continue
            if r.get("page_sha") != page_sha(t, ch):
                skipped["stale"] += 1
                continue
            leak_seen.add((t, ch, claim))
            obs.append({"work": work, "system": system, "cutoff": t, "character": slugify(ch), "metric": "M4",
                        "num": int(r["verdict"] in ("stated", "implied")), "den": 1,
                        "reader": r.get("reader", "unrecorded"), "source": audit.name})
    for t in range(1, upto + 1):
        hand = eval_dir / "recall_hand" / f"{series}_v{t}.json"
        # ponytail: file-level mtime, so a byte-identical re-render also marks it stale; per-row sha if that bites
        if hand.exists() and hand.stat().st_mtime < pages_mtime(t):
            skipped["stale"] += 1
        elif hand.exists():
            for key, verdict in json.loads(hand.read_text(encoding="utf-8")).items():
                key = key[2:] if key[:2] in ("C ", "F ") else key  # recall_review.py's C/F prefix
                ch, claim = (s.strip() for s in key.split(" | ", 1))
                if (ch, claim) not in fv:
                    skipped["recall_unknown_fact"] += 1
                    continue
                future = fv[(ch, claim)] > t
                if fv[(ch, claim)] > last_vol:
                    skipped["past_scope"] += 1
                    continue
                if future and (t, ch, claim) in leak_seen:
                    continue
                judged.add((t, ch, claim))
                obs.append({"work": work, "system": system, "cutoff": t, "character": slugify(ch),
                            "metric": "M4" if future else "M1", "num": int(verdict == "yes"), "den": 1,
                            "reader": "recall_hand", "source": hand.name})
        prec = eval_dir / "precision" / f"{series}_v{t}_all.jsonl"
        if prec.exists() and prec.stat().st_mtime < pages_mtime(t):
            skipped["stale"] += 1
        elif prec.exists():
            for line in prec.open(encoding="utf-8"):
                r = json.loads(line)
                human = (r.get("human") or "").strip().lower()
                if human == "frame_error":  # R2: the harness misstated the page, not the system
                    skipped["frame_error"] += 1
                    continue
                if human not in ("supported", "unsupported", "partial", "uncertain"):
                    skipped["precision_unread"] += 1
                    continue
                ok = human == "supported" or (partial_supported and human == "partial")
                obs.append({"work": work, "system": system, "cutoff": t, "character": slugify(r["entity"]),
                            "metric": "M3", "num": int(ok), "den": 1, "weight": float(r.get("weight", 1.0)),
                            "label": human, "label_atom": (r.get("human_atom") or "").strip().lower() or None,
                            "reader": r.get("reader", "unrecorded"), "source": prec.name})
                # M8 (plan 0015 step 5): citation sufficiency of the same sampled atom, when it was read.
                # "sufficient" = 1; partial / insufficient / none (no paragraph citation) = 0.
                cite = (r.get("cite") or "").strip().lower()
                if cite and cite != "frame_error":
                    obs.append({"work": work, "system": system, "cutoff": t, "character": slugify(r["entity"]),
                                "metric": "M8", "num": int(cite == "sufficient"), "den": 1,
                                "label": cite, "reader": r.get("cite_reader", "unrecorded"), "source": prec.name})
    judged |= leak_seen
    skipped["unassessed_gold"] = sum((t, ch, c) not in judged for t in range(1, upto + 1)
                                     for (ch, c), v in fv.items() if v <= last_vol)
    return obs, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--system", default="B5")
    ap.add_argument("--upto", type=int, default=5)
    ap.add_argument("--work", help="paper work name (default: the gold series)")
    ap.add_argument("--last-vol", type=int, default=5, help="N, the last volume in scope (future = t < reveal <= N)")
    ap.add_argument("--partial-supported", action="store_true")
    a = ap.parse_args()
    from narrativewiki import paths
    from narrativewiki.config import load_settings
    sys.path.insert(0, str(Path(__file__).parent))
    import leak_screen_audit as lsa
    paths.set_active_series(a.series)

    def page_sha(t: int, ch: str) -> str:
        # C13: no page hashes as sha([]), exactly as leak_screen_audit.py records it.
        return lsa.sha(lsa.page_of(a.series, ch, t))

    def tree_sha(t: int) -> set[str]:
        # Two recipes are on file: the B5 pilot hashed every .md, later cells every .md outside source/.
        import hashlib
        pages = sorted(paths.wiki_cutoff_dir(t).rglob("*.md"))
        return {hashlib.sha256(b"".join(p.read_bytes() for p in ps)).hexdigest()[:16]
                for ps in (pages, [p for p in pages if p.parent.name != "source"])}

    def pages_mtime(t: int) -> float:
        return max((p.stat().st_mtime for p in (paths.wiki_cutoff_dir(t) / "character").glob("*.md")), default=0.0)
    cfg = load_settings(a.series).series  # a variant `<base>@<system>` resolves to its base's gold
    gold_name = cfg.get("gold_from") or (cfg.get("decontaminate") or {}).get("from_series") or a.series
    gold = yaml.safe_load((EVAL / "parametric" / f"{gold_name}.yaml").read_text(encoding="utf-8"))
    obs, skipped = observations(a.series, a.system, a.upto, a.work or gold_name, gold, page_sha=page_sha,
                                pages_mtime=pages_mtime, last_vol=a.last_vol, partial_supported=a.partial_supported,
                                tree_sha=tree_sha)
    out = EVAL / "observations" / f"{a.series}_{a.system}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in obs), encoding="utf-8")
    counts = {}
    for o in obs:
        counts[(o["metric"], o["cutoff"])] = counts.get((o["metric"], o["cutoff"]), 0) + 1
    print(f"{out}: {len(obs)} observations; " + ", ".join(f"{m} t{t}: {n}" for (m, t), n in sorted(counts.items()))
          + f"; skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
