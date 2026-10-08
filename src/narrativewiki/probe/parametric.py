"""[31] L_param on the page itself: does a page written from volumes <= t state later facts?

Inputs:     the active series' parsed volumes <= t (one condition: original, remap-only or
            decontaminated), the hand-written fact file `docs/eval/parametric/<source>.yaml`
            (future facts first established after t, control facts at or before t), and the
            series' `decontaminate.entity_map` (empty for the original).
Outputs:    one row per (character, mode, fact) and a summary per mode (CONTRACTS §9.5).
            Modes: `context`, where the generator gets the full text of volumes <= t and is told
            to reveal nothing later; `context_noguard`, the same text without that instruction;
            and `closed`, where it gets the character's name only.
            Two tiers per future fact: `supported` (MiniCheck >= 0.5) and `lexical` (a marker
            keyword on the page that never occurs in the volumes <= t text).
Invariants: - In `context` mode no text after t is ever in the prompt. A future fact the page
              supports therefore came from the model's memory. That is the measurement.
            - Facts and names are remapped with the same table as the text, so every condition
              is scored against the same facts.
            - Support is MiniCheck (local, $0) at 0.5 through `LLMClient.score_support`. The
              page is the document and the fact is the claim.
            - Every call goes through `llm/client.py`: stage `parametric_page` for the page,
              `support` for the scoring.
Contract:   docs/CONTRACTS.md §9.5. Plan: docs/vision/plans/0009-parametric-leakage-gutenberg.md (E3).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .. import paths
from ..entities.gazetteer import slugify
from ..ingest.decontaminate import remap

STAGE = "parametric_page"
THRESHOLD = 0.5

CONTEXT_PROMPT = """Below is the full text of the part of a novel that the reader has finished so far. Write an encyclopedia-style wiki page about {name}: background, personality, relationships, and what happens to them.

{guard}TEXT:
{text}

Now write the wiki page about {name}."""

GUARD = "The reader has read only this part. Use only this text, and do not reveal anything that happens later in the novel.\n\n"

CLOSED_PROMPT = """Write an encyclopedia-style wiki page about {name}, a character in a novel: background, personality, relationships, and what happens to them."""


def gold_pages(series: str, character: str, cutoff_dir: Path) -> list[Path]:
    """[33] The existing page(s) that carry gold `character` in `series`'s wiki at one cutoff dir.
    `docs/eval/parametric/pages/<series>.yaml` maps a gold name to the system's own page slugs (B5's
    canonical names differ from the gold's: "Diana" for Diana Barry, "Anne" + "Anne Shirley" for one
    person); a name it does not list, or a series without the file (every baseline, whose cast IS
    the gold names), is looked up by slugify(name). Callers score the union of the pages returned;
    none returned = a page-less character, still in every denominator."""
    f = paths.DOCS_DIR / "eval" / "parametric" / "pages" / f"{series}.yaml"
    mapping = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.is_file() else {}
    slugs = mapping.get(character) or [slugify(character)]
    return [page for slug in slugs if (page := cutoff_dir / "character" / f"{slug}.md").is_file()]


def volume_text(records: list[dict[str, Any]]) -> str:
    """Paragraphs in reading order, with a chapter heading line wherever the chapter changes."""
    out, last = [], None
    for r in records:
        key = (r["vol"], r["chapter_idx"])
        if key != last and r.get("chapter_title"):
            out.append(f"\n{r['chapter_title']}\n")
        last = key
        out.append(r["text"])
    return "\n".join(out)


def build_prompts(gold: dict[str, Any], text: str, entity_map: dict[str, str],
                  t: int | None = None) -> list[dict[str, Any]]:
    """Three pages per character. With `t`, only characters a reader of volumes <= t has met
    (at least one fact with first_vol <= t) -- Rilla's page at t=1 would be a page about no one."""
    jobs = []
    for character, entry in gold["characters"].items():
        if t is not None and not any(f["first_vol"] <= t for f in facts_of(entry)):
            continue
        name = remap(character, entity_map)
        jobs.append({"character": name, "mode": "context", "prompt": CONTEXT_PROMPT.format(name=name, text=text, guard=GUARD)})
        jobs.append({"character": name, "mode": "context_noguard", "prompt": CONTEXT_PROMPT.format(name=name, text=text, guard="")})
        jobs.append({"character": name, "mode": "closed", "prompt": CLOSED_PROMPT.format(name=name)})
    return jobs


def facts_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """[31] A character's gold facts with `first_vol` taken from the evidence id ("v03:c07:p0012"
    -> 3). `facts:` is the multi-cutoff form; the older `future:`/`control:` lists are read too,
    since their evidence ids already encode the volume."""
    out = []
    for fact in (entry.get("facts") or []) + (entry.get("future") or []) + (entry.get("control") or []):
        out.append({**fact, "first_vol": int(fact.get("first_vol") or fact["evidence"][1:3])})
    return out


def valid_keywords(gold: dict[str, Any], text: str, entity_map: dict[str, str], t: int = 1) -> dict[str, list[str]]:
    """Future-fact markers (first_vol > t), remapped, keeping only those absent from the volumes
    <= t text."""
    lowered = text.lower()
    return {fact["claim"]: [k for k in (remap(k, entity_map) for k in fact.get("keywords", []))
                            if k.lower() not in lowered]
            for entry in gold["characters"].values() for fact in facts_of(entry) if fact["first_vol"] > t}


def score(client, jobs: list[dict[str, Any]], pages: list[str], gold: dict[str, Any],
          entity_map: dict[str, str], keywords: dict[str, list[str]] | None = None,
          t: int = 1) -> list[dict[str, Any]]:
    """A fact is `future` at cutoff t when first established after t, else `control`."""
    keywords = keywords or {}
    rows = []
    for job, page in zip(jobs, pages):
        entry = next(v for k, v in gold["characters"].items() if remap(k, entity_map) == job["character"])
        for fact in facts_of(entry):
            kind = "future" if fact["first_vol"] > t else "control"
            claim = remap(fact["claim"], entity_map)
            # An absent or empty page states nothing: 0 without a classifier call. Its control facts
            # stay in recall's denominator (a recall miss); its future facts are counted apart
            # (`n_future_no_page`), since a page that does not exist cannot leak and would only
            # dilute the leak rate.
            s = client.score_support([page], claim)["score"] if page.strip() else 0.0
            markers = [k for k in keywords.get(fact["claim"], []) if re.search(re.escape(k), page, re.I)]
            rows.append({"channel": "L_param", "probe": "parametric_page", "character": job["character"],
                         "mode": job["mode"], "kind": kind, "claim": claim, "evidence": fact["evidence"],
                         "first_vol": fact["first_vol"], "upto_vol": t,
                         "score": round(s, 4), "supported": s >= THRESHOLD,
                         "lexical": bool(markers), "markers": markers, "page_empty": not page.strip()})
    return rows


def summarize(rows: list[dict[str, Any]], pages: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    out = []
    for mode in sorted({r["mode"] for r in rows}):
        sub = [r for r in rows if r["mode"] == mode]
        fut = [r for r in sub if r["kind"] == "future" and not r.get("page_empty")]
        ctl = [r for r in sub if r["kind"] == "control"]
        out.append({
            "channel": "L_param", "probe": "parametric_page_summary", "mode": mode,
            "n_future": len(fut),
            "n_future_no_page": sum(r["kind"] == "future" and bool(r.get("page_empty")) for r in sub), "future_leak_rate": round(sum(r["supported"] for r in fut) / len(fut), 4) if fut else None,
            "future_leak_rate_lexical": round(sum(r.get("lexical", False) for r in fut) / len(fut), 4) if fut else None,
            "future_leak_rate_either": round(sum(r["supported"] or r.get("lexical", False) for r in fut) / len(fut), 4) if fut else None,
            "n_control": len(ctl), "control_recall": round(sum(r["supported"] for r in ctl) / len(ctl), 4) if ctl else None,
            "leaked": [f"{r['character']}: {r['claim']}" for r in fut if r["supported"]],
            "page_words": sum(len(p.split()) for (c, m), p in pages.items() if m == mode),
        })
    return out
