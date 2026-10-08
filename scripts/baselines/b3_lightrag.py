"""[33] S10 B3 — prefix-built LightRAG (lightrag-hku==1.5.7, pinned) as the retriever; our generator.

Run (in the isolated env, never the pipeline's .venv):
      .venv-b3/Scripts/python.exe scripts/baselines/b3_lightrag.py <series> --upto <t> [--estimate]
      [--dry] [--budget-tokens 12000] [--mode mix]

Adaptation (appendix D records it): LightRAG builds its entity/relation graph and vector index
from volumes 1..t ONLY (prefix-built; the t index is the t-1 index copied, plus volume t's
chapters, so no index ever sees a later volume). For each cast character, `aquery_data` returns
LightRAG's retrieved entities, relations and text chunks — its retrieval, no generation. That
context goes to the SAME generator, prompt, output contract, citation check and renderer as every
other system (scripts/baselines/common.py), and the page lands at
`dist/<series>@b3/wiki/vNN/character/<slug>.md`. Chunks keep their paragraph ids because every
indexed line is `[vNN:cNN:pNNNN] text`; a page may cite only ids in its retrieved chunks.

LightRAG's own calls (entity extraction, keyword extraction, summaries) go through
`llm/client.py` as stage `baseline_lightrag` — cached, budgeted, behind the paid-scope interlock —
and its embeddings through `baseline_embed` (local bge-m3). `--estimate` prices the index build and
the page calls without any model call; `--dry` runs the whole plumbing on the first chapter with
a stub model and random vectors ($0), to test the adapter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402
from narrativewiki import paths, provenance  # noqa: E402
from narrativewiki.config import load_settings  # noqa: E402
from narrativewiki.entities.gazetteer import slugify  # noqa: E402
from narrativewiki.llm.client import LLMClient, LLMError  # noqa: E402

LIGHTRAG_VERSION = "1.5.7"
CHUNK_TOKENS = 1200          # LightRAG's default fixed-token chunk
LLM_TIMEOUT_S = 3600         # > Flex X-Server-Timeout, so LightRAG never abandons a queued call
EXTRACT_OVERHEAD_TOKENS = 2500  # its entity-extraction prompt around each chunk (estimate only)
QUERY = "{name}: who this character is, their appearance, personality, abilities, goals, relationships and history"


def chapter_docs(paras: list[dict]) -> list[tuple[str, str]]:
    """(doc id, text) per chapter; each line keeps its paragraph id."""
    docs: dict[str, list[str]] = {}
    for p in paras:
        docs.setdefault(p["para_id"].rsplit(":", 1)[0], []).append(f"[{p['para_id']}] {p['text']}")
    return [(k, "\n".join(v)) for k, v in docs.items()]


def make_rag(workdir: Path, client: LLMClient | None, dry: bool):
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc

    async def llm(prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **_):
        if dry:
            return '{"high_level_keywords": [], "low_level_keywords": []}' if keyword_extraction else ""
        return await asyncio.to_thread(client.complete, "baseline_lightrag", prompt, system_prompt,
                                       bool(keyword_extraction))

    async def embed(texts, **_):
        texts = [texts] if isinstance(texts, str) else list(texts)
        if dry:
            return np.random.default_rng(0).random((len(texts), 1024), dtype=np.float32)
        vecs = await asyncio.to_thread(client.embed, "baseline_embed", texts)
        # A text bge-m3 cannot encode (empty keywords, a NaN case) comes back as None; LightRAG needs a
        # 1024-d row for every input, so it gets a zero vector (it matches nothing).
        return np.array([v if v is not None else [0.0] * 1024 for v in vecs], dtype=np.float32)

    # A Flex call may queue up to X-Server-Timeout (1800 s); LightRAG's default 240 s (worker 2x) dropped
    # five v2 chapters as FAILED on the first Anne run (MEASUREMENTS 2026-09-29).
    return LightRAG(working_dir=str(workdir), llm_model_func=llm, chunk_token_size=CHUNK_TOKENS,
                    default_llm_timeout=LLM_TIMEOUT_S, embedding_func=EmbeddingFunc(embedding_dim=1024, max_token_size=8192, func=embed))


async def build_and_query(workdir: Path, docs: list[tuple[str, str]], names: list[str], client, dry: bool,
                          mode: str, budget: int, all_docs: dict[str, str] | None = None
                          ) -> dict[str, tuple[str, set[str]]]:
    from lightrag import QueryParam
    from lightrag.base import DocStatus
    from lightrag.kg.shared_storage import initialize_pipeline_status

    rag = make_rag(workdir, client, dry)
    await rag.initialize_storages()
    await initialize_pipeline_status()
    if docs:
        await rag.ainsert([d for _, d in docs], ids=[i for i, _ in docs])
    if all_docs:
        # LightRAG 1.5.7 never retries a FAILED document on its own (only a manual reset does): delete
        # each one and insert it again — its finished calls are cache hits. Then refuse to query an
        # index that does not hold every chapter of volumes 1..t.
        for _ in range(3):
            failed = sorted(await rag.doc_status.get_docs_by_statuses([DocStatus.FAILED]))
            if not failed:
                break
            print(f"re-inserting {len(failed)} FAILED chapters: {', '.join(failed)}")
            for i in failed:
                await rag.adelete_by_doc_id(i)
            await rag.ainsert([all_docs[i] for i in failed], ids=failed)
        done = set(await rag.doc_status.get_docs_by_statuses([DocStatus.PROCESSED]))
        missing = sorted(set(all_docs) - done)
        if missing:
            await rag.finalize_storages()
            raise SystemExit(f"index incomplete, {len(missing)} chapters not processed: {', '.join(missing)}")
    out = {}
    for name in names:
        # LightRAG allocates its own context (entities, relations, then chunks with what is left)
        # under max_total_tokens = our evidence budget; no reranker is configured, so it is off.
        # Our earlier re-truncation let the id-less graph descriptions crowd out the citable chunks
        # and made `mix` and `hybrid` send identical pages (MEASUREMENTS 2026-09-28).
        param = QueryParam(mode=mode, max_total_tokens=budget, enable_rerank=False)
        data = await rag.aquery_data(QUERY.format(name=name), param=param)
        d = data.get("data", data) if isinstance(data, dict) else {}
        facts = [f"- {e.get('entity_name')}: {e.get('description', '')}" for e in d.get("entities", [])]
        facts += [f"- {r.get('src_id')} -- {r.get('tgt_id')}: {r.get('description', '')}" for r in d.get("relationships", [])]
        chunks = [c.get("content", "") for c in d.get("chunks", [])]
        text = "LightRAG graph context:\n" + "\n".join(facts) + "\n\nLightRAG text chunks:\n" + "\n\n".join(chunks)
        out[name] = (text.strip(), set(re.findall(r"\[(v\d+:c\d+:p\d+)\]", text)))
    await rag.finalize_storages()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--upto", type=int, required=True)
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--mode", default="mix")
    ap.add_argument("--budget-tokens", type=int, default=20000)  # = B2's tuned evidence budget
    a = ap.parse_args()
    import lightrag
    if lightrag.__version__ != LIGHTRAG_VERSION:
        sys.exit(f"LightRAG {lightrag.__version__} installed; B3 is pinned to {LIGHTRAG_VERSION}")

    t, variant = a.upto, f"{a.series}@b3"
    paths.set_active_series(variant)
    settings = load_settings(variant)
    paras = C.load_paras(paths.PARSED_DIR, t)
    prefix_text = "\n".join(p["text"] for p in paras)
    from run_baseline import gold_cast  # the same cast rule as every other system
    cast = gold_cast(settings)
    named = [c for c in cast if C.present(c, cast[c], prefix_text, t)]
    new_docs = chapter_docs([p for p in paras if p["vol"] == t])

    build_tokens = sum(C.estimate_tokens(d) for _, d in new_docs)
    chunks = max(1, round(build_tokens / (CHUNK_TOKENS - 100)))
    lr, page = settings.resolve_role("baseline_lightrag"), settings.resolve_role("baseline_page")
    idx_cost = chunks * ((CHUNK_TOKENS + EXTRACT_OVERHEAD_TOKENS) * lr.price_in + 800 * lr.price_out) / 1e6
    page_cost = len(named) * (a.budget_tokens * page.price_in + 1500 * page.price_out) / 1e6
    print(f"{variant} t={t}: index adds volume {t} (~{build_tokens:,} tokens, ~{chunks} chunks, "
          f"~${idx_cost:.2f} extraction on {lr.model}, one gleaning pass would add ~the same); "
          f"{len(named)} pages ~${page_cost:.2f} on {page.model}")
    if a.estimate:
        return 0

    base_dir = ROOT / "data" / variant / "lightrag"
    workdir = base_dir / ("dry" if a.dry else f"v{t:02d}")
    if a.dry:
        shutil.rmtree(workdir, ignore_errors=True)
        new_docs, named = new_docs[:1], named[:2]
    elif not workdir.exists():
        prev = base_dir / f"v{t - 1:02d}"
        if t > 1 and not prev.exists():
            sys.exit(f"build v{t - 1:02d} first: the t index is the t-1 index plus volume t")
        if t > 1:
            shutil.copytree(prev, workdir)
    else:
        new_docs = []  # this cutoff's index already holds volume t; query only
    workdir.mkdir(parents=True, exist_ok=True)

    run = client = None
    if not a.dry:
        run = provenance.start_run(variant, "baseline", scope=f"b3-v{t}", volumes=list(range(1, t + 1)),
                                   volume_scope=t, stage_keys=[], settings=settings)
        client = LLMClient(settings, run=run, volume_scope=t)
    contexts = asyncio.run(build_and_query(workdir, new_docs, named, client, a.dry, a.mode, a.budget_tokens,
                                           dict(new_docs) if a.dry else dict(chapter_docs(paras))))
    if a.dry:
        print({n: (len(c), len(ids)) for n, (c, ids) in contexts.items()})
        return 0

    out_dir = paths.wiki_cutoff_dir(t) / "character"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):  # the directory is exactly this run's pages (no stale cast)
        old.unlink()
    written, failures, stats = [], [], {}
    for name, (ctx, allowed) in contexts.items():
        try:
            pg = client.complete_json("baseline_page", C.prompt(ctx, name, t, settings.series.get("series", {}).get("title", a.series)),
                                      C.Page, system=C.SYSTEM)
        except LLMError as exc:
            failures.append({"character": name, "error": str(exc)[:300]})
            if len(failures) >= 3:
                break
            continue
        pg, st = C.enforce(pg, allowed)
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v
        (out_dir / f"{slugify(name)}.md").write_text(C.render(pg, name, slugify(name), t, "B3"), encoding="utf-8")
        written.append(name)
    run.finish("ok" if not failures else "partial")
    (paths.wiki_cutoff_dir(t) / "_baseline.json").write_text(json.dumps({
        "system": "B3", "lightrag": LIGHTRAG_VERSION, "mode": a.mode, "series": a.series, "upto": t,
        "run_id": run.run_id, "cast": list(cast), "written": written, "no_page": [c for c in cast if c not in named],
        "failures": failures, "stats": stats, "settings": {"budget_tokens": a.budget_tokens, "chunk_tokens": CHUNK_TOKENS},
    }, indent=1), encoding="utf-8")
    print(f"wrote {len(written)} pages, {len(failures)} failures -> {paths.relative(out_dir)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
