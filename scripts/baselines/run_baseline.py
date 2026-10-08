"""[33] S10 — run one baseline for one work at one cutoff; pages land where every eval tool looks.

Run:  .venv/Scripts/python.exe scripts/baselines/run_baseline.py <series> <X1|B1|B2|B4|X2> --upto <t>
      [--estimate] [--cast names.txt] [--budget-tokens 12000] [--chunk-words 250]
      [--context-limit 1000000] [--set KEY=VALUE ...]

Writes `dist/<series>@<system>/wiki/vNN/character/<slug>.md` (the variant id `<series>@<system>`
reads the base's config, text and gold; paths.variant_base) plus `_baseline.json` beside them:
cast, pages written, characters with no page (never named in the passages' volumes), unsupported
cells, citations dropped, sentences cut, run id. Then score with the same commands as B5, e.g.
`python scripts/eval/pipeline_page_leak.py anne@b1 --upto 1`.

`--estimate` builds every context and prints tokens and a cost ceiling without any model call
(plan 0013 §4.1: an estimate precedes every billed run). The ceiling ignores implicit caching of the
shared passage prefix (Gemini 3: 90% off repeated input), so the real bill should be lower for
B1/X2. Three failed characters in a row stop the run (standing rule: stop on repeated API failure).

Cast: the gold characters of the work (docs/eval/parametric/<gold>.yaml) until the frozen cast
exists (plan 0014 S05); `--cast` takes one name per line. Settings chosen on Middlemarch are
passed explicitly and recorded in `_baseline.json`; defaults are starting points, not tuned values.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402
from narrativewiki import paths, provenance  # noqa: E402
from narrativewiki.config import apply_set_overrides, load_settings  # noqa: E402
from narrativewiki.entities.gazetteer import slugify  # noqa: E402
from narrativewiki.graph import temporal  # noqa: E402
from narrativewiki.llm.client import LLMClient, LLMError  # noqa: E402
from narrativewiki.llm.parallel import map_calls  # noqa: E402
from narrativewiki.ingest.decontaminate import remap  # noqa: E402

SYSTEMS = ("X1", "B1", "B2", "B4", "X2")
OUTPUT_TOKENS_EST = 1500  # a B5-sized page as JSON; used only for the estimate


def gold_cast(settings) -> dict[str, int]:
    """Gold characters -> the volume of their earliest gold fact (C.present's `first_vol`)."""
    # A decontaminated series reads its source's gold, names remapped with the text's own table
    # (probe/parametric.py does the same for the leak screen), so its cast is the same people.
    dc = settings.series.get("decontaminate") or {}
    name = settings.series.get("gold_from") or dc.get("from_series") or settings.series_id
    gold = yaml.safe_load((paths.DOCS_DIR / "eval" / "parametric" / f"{name}.yaml").read_text(encoding="utf-8"))
    emap = dc.get("entity_map") or {}
    return {remap(c, emap): min(int(f["evidence"][1:3]) for f in v["facts"]) for c, v in gold["characters"].items()}


def graph_context(base: str, character: str, upto: int, by_id: dict[str, dict], budget: int) -> tuple[str, str] | None:
    """B4: facts about the character from the FULL-built graph, filtered at retrieval to cutoff t
    through graph/temporal.py (the only sanctioned reads), with their cited paragraphs.

    [34] A gold name with no exact canonical or alias entry ("Carl Meredith" vs the entity "Carl")
    returned empty context, and the prompt fell to the title-only branch written for X1: the page
    was written from memory (OPEN_GAPS G10). Such a name is now looked up through the graph
    series' page map, as B5 is scored, and None (no page) when that finds nothing either."""
    gaz = json.loads((ROOT / "data" / base / "02_entities" / "gazetteer.json").read_text(encoding="utf-8"))
    ents = gaz.get("entities", gaz)
    ids = [e["entity_id"] for e in ents if e.get("canonical") == character or character in e.get("aliases", [])]
    if not ids:
        page_map = ROOT / "docs" / "eval" / "parametric" / "pages" / f"{base}.yaml"
        mapped = (yaml.safe_load(page_map.read_text(encoding="utf-8")) or {}).get(character, []) if page_map.is_file() else []
        known = {e["entity_id"] for e in ents}
        ids = [slug for slug in mapped if slug in known]
    if not ids:
        return None
    conn = sqlite3.connect(f"file:{ROOT / 'data' / base / '04_graph' / 'graph.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    names = {e["entity_id"]: e["canonical"] for e in ents}
    rows = [r for eid in ids for r in (*temporal.state_at(conn, eid, upto), *temporal.history_at(conn, eid, upto),
                                       *temporal.relations_at(conn, eid, upto))]
    facts, para_ids = [], []
    for r in rows:
        obj = names.get(r["object"], r["object"]) if r["object"] else None
        subj = names.get(r["subject"], r["subject"])
        facts.append(f"- {subj} {r['predicate']} {obj or r['value']}" + (f" ({r['qualifier']})" if r["qualifier"] else ""))
        for ev in temporal.evidence_at(conn, json.loads(r["claim_ids_json"] or "[]"), upto):
            if ev["para_id"] not in para_ids:
                para_ids.append(ev["para_id"])
    conn.close()
    used, keep = sum(C.estimate_tokens(f) for f in facts), []
    for pid in para_ids:
        p = by_id.get(pid)
        if p is None:
            continue
        cost = C.estimate_tokens(p["text"])
        if used + cost > budget:
            break
        keep.append(p)
        used += cost
    keep.sort(key=lambda p: (p["vol"], p["chapter_idx"], p["seq"]))
    return "Graph facts (retrieved at this cutoff):\n" + "\n".join(facts), C.format_passages(keep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("system", choices=SYSTEMS)
    ap.add_argument("--upto", type=int, required=True)
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--cast")
    ap.add_argument("--budget-tokens", type=int, default=12000)
    ap.add_argument("--chunk-words", type=int, default=250)
    ap.add_argument("--context-limit", type=int, default=1_000_000)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--graph", help="[34] B4 only: the series whose gazetteer and graph it reads "
                    "(default: the series itself); a variant's own suffix is added to the output name")
    a = ap.parse_args()

    base, system, t = a.series, a.system, a.upto
    graph = a.graph or base
    variant = f"{base}@{system.lower()}" + (f"-{graph.split('@', 1)[1]}" if "@" in graph else "")
    paths.set_active_series(variant)
    settings = load_settings(variant)
    apply_set_overrides(settings, a.set)
    vmax = 5 if system == "X2" else t
    paras = C.load_paras(paths.PARSED_DIR, vmax)
    prefix = [p for p in paras if p["vol"] <= t]
    by_id = {p["para_id"]: p for p in paras}
    prefix_text = "\n".join(p["text"] for p in prefix)
    title = (settings.series.get("series") or {}).get("title", base)
    cast = ({l.strip(): None for l in Path(a.cast).read_text(encoding="utf-8").splitlines() if l.strip()}
            if a.cast else gold_cast(settings))
    named = [c for c in cast if C.present(c, cast[c], prefix_text, t)]
    no_page = [c for c in cast if c not in named]

    client = None
    vectors = qvecs = chunks = None
    if system == "B2":
        chunks = C.chunk(prefix, a.chunk_words)
        probe = LLMClient(settings, volume_scope=t)
        vectors = probe.embed("baseline_embed", [c["text"] for c in chunks])
        qs = C.facet_queries(named)
        qvecs = dict(zip(qs, probe.embed("baseline_embed", qs)))

    jobs, unsupported = [], []
    for ch in named:
        extra = ""
        if system == "X1":
            passages, allowed = "", set()
        elif system in ("B1", "X2"):
            chosen = prefix if system == "B1" else paras
            passages, allowed = C.format_passages(chosen), {p["para_id"] for p in chosen}
        elif system == "B2":
            sel = C.hybrid_select(chunks, vectors, qvecs, ch, a.budget_tokens)
            ps = [p for c in sel for p in c["paras"]]
            passages, allowed = C.format_passages(ps), {p["para_id"] for p in ps}
        else:  # B4
            context = graph_context(graph, ch, t, by_id, a.budget_tokens)
            if context is None:
                unsupported.append({"character": ch, "tokens": 0, "reason": f"no entity in {graph}'s graph"})
                continue
            extra, passages = context
            allowed = {line[1:].split("]", 1)[0] for line in passages.splitlines() if line.startswith("[")}
        if system != "X1" and not passages.strip():
            # [34] G10: a text-reading system's prompt without passages is the closed-book prompt
            unsupported.append({"character": ch, "tokens": 0, "reason": "no passages"})
            continue
        body = (C.prompt_future_informed(passages, ch, t, vmax) if system == "X2" else
                C.prompt((extra + "\n\n" + passages).strip() if extra else passages, ch, t, title))
        sys_prompt = C.SYSTEM_CLOSED_BOOK if system == "X1" else C.SYSTEM
        tokens = C.estimate_tokens(sys_prompt + body)
        if tokens > a.context_limit:
            unsupported.append({"character": ch, "tokens": tokens, "reason": f"context over {a.context_limit}"})
            continue
        jobs.append({"character": ch, "prompt": body, "system": sys_prompt, "allowed": allowed, "tokens": tokens})

    profile = settings.resolve_role("baseline_page")
    tin = sum(j["tokens"] for j in jobs)
    ceiling = (tin * profile.price_in + len(jobs) * OUTPUT_TOKENS_EST * profile.price_out) / 1e6
    print(f"{variant} t={t}: {len(cast)} cast, {len(jobs)} pages to write, {len(no_page)} never named, "
          f"{len(unsupported)} unsupported; ~{tin:,} input tokens; ceiling ${ceiling:.2f} on {profile.model} "
          f"(no caching credit)")
    if a.estimate:
        return 0

    run = provenance.start_run(variant, "baseline", scope=f"{system.lower()}-v{t}", volumes=list(range(1, vmax + 1)),
                               volume_scope=vmax, stage_keys=[], settings=settings)
    client = LLMClient(settings, run=run, volume_scope=vmax)
    out_dir = paths.wiki_cutoff_dir(t) / "character"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):  # the directory is exactly this run's pages (no stale cast)
        old.unlink()
    failures, streak, stats = [], [0], {}

    def one(job: dict):
        if streak[0] >= 3:
            return {"character": job["character"], "error": "skipped after 3 consecutive failures"}
        try:
            page = client.complete_json("baseline_page", job["prompt"], C.Page, system=job["system"])
        except LLMError as exc:
            streak[0] += 1
            return {"character": job["character"], "error": str(exc)[:300]}
        streak[0] = 0
        page, st = C.enforce(page, job["allowed"])
        slug = slugify(job["character"])
        (out_dir / f"{slug}.md").write_text(C.render(page, job["character"], slug, t, system), encoding="utf-8")
        return {"character": job["character"], "stats": st}

    results = map_calls(one, jobs, workers=profile.concurrency)
    for r in results:
        if "error" in r:
            failures.append(r)
        for k, v in (r.get("stats") or {}).items():
            stats[k] = stats.get(k, 0) + v
    run.finish("ok" if not failures else "partial")
    manifest = {"system": system, "series": base, "variant": variant, "upto": t, "run_id": run.run_id,
                "model": profile.model, "cast": list(cast), "written": [r["character"] for r in results if "error" not in r],
                "no_page": no_page, "unsupported": unsupported, "failures": failures, "stats": stats,
                "settings": {"budget_tokens": a.budget_tokens, "chunk_words": a.chunk_words,
                             "context_limit": a.context_limit, "set": a.set}}
    (paths.wiki_cutoff_dir(t) / "_baseline.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(manifest['written'])} pages, {len(failures)} failures, stats {stats} -> {paths.relative(out_dir)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
