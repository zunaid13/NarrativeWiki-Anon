"""[24] L_query -- exact future-fact values found in the cutoff page artifact.

Inputs:     The active series' graph.db and already-generated 05_pages/*/v*.json files.
Outputs:    `measure_future_fact_leak(...)` -- a row counting future claims whose literal
            value (or relation object's canonical name) occurs in the cutoff-visible pages,
            with one reproducible first-match witness per counted claim and a hashed page inventory.
Invariants: - Zero LLM calls; never regenerates pages, claims, or the graph.
            - One page per character: the exact cutoff, otherwise the nearest earlier file.
            - Case-sensitive substring matching only; this measures value occurrence, not
              whether the artifact entails the future claim.
            - Diagnostics describe the first witness and never change the numerator.
            - No selected page files means a missing artifact, even with zero future claims.
Contract:   docs/CONTRACTS.md §5 and §9.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any

from ... import paths
from ...graph import store
from ...synth.cache import previous_page_path
from ..futureset import future_claims_at


def _pointer_key(key: str) -> str:
    """Escape a JSON object key for a JSON Pointer (RFC 6901)."""
    return key.replace("~", "~0").replace("/", "~1")


def _page_fields(page: dict[str, Any]) -> list[tuple[str, str]]:
    """The original probe's text fields and order, paired with JSON Pointers."""
    parts = [("/lead", page.get("lead"))]
    for key, section in (page.get("prose") or {}).items():
        if section:
            parts.append((f"/prose/{_pointer_key(key)}/text", section.get("text")))
    for key, field in (page.get("fields") or {}).items():
        pointer = f"/fields/{_pointer_key(key)}"
        if isinstance(field, dict):
            parts.append((f"{pointer}/value", field.get("value")))
        elif isinstance(field, list):
            text_key = {"relationships": "blurb", "affiliations": "role"}.get(key, "value")
            parts.extend((f"{pointer}/{i}/{text_key}", item.get(text_key))
                         for i, item in enumerate(field))
    return [(pointer, text) for pointer, text in parts if isinstance(text, str)]


def _cutoff_corpus(upto_vol: int) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    """Keep the existing newline-joined corpus and map its offsets back to real files."""
    texts: list[str] = []
    segments: list[dict[str, Any]] = []
    pages: list[dict[str, str]] = []
    offset = 0
    for directory in sorted(paths.PAGES_DIR.glob("*")):
        if not directory.is_dir():
            continue
        exact = paths.page_json(directory.name, upto_vol)
        page_path = exact if exact.is_file() else previous_page_path(directory.name, upto_vol)
        if page_path is None:
            continue
        raw = page_path.read_bytes()
        page = json.loads(raw.decode("utf-8"))
        pages.append({"page": paths.relative(page_path), "entity_id": directory.name,
                      "sha256": hashlib.sha256(raw).hexdigest()})
        fields = _page_fields(page)
        if texts:
            offset += 1  # newline between pages, even an empty page
        for i, (pointer, text) in enumerate(fields):
            if i:
                offset += 1  # newline between fields
            segments.append({"page": paths.relative(page_path), "entity_id": directory.name,
                             "field": pointer, "text": text, "corpus_start": offset})
            offset += len(text)
        texts.append("\n".join(text for _, text in fields))
    if not pages:
        raise FileNotFoundError(
            f"No cutoff-visible page artifacts at t={upto_vol} under {paths.PAGES_DIR}. "
            "Build pages with `wiki synthesize` before measuring the leak channel."
        )
    return "\n".join(texts), segments, pages


def _locations(segments: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    """All fields overlapped by a hit, including matches spanning synthetic separators."""
    locations = []
    for segment in segments:
        base = segment["corpus_start"]
        stop = base + len(segment["text"])
        if base < end and stop > start:
            locations.append({**segment, "start": max(start - base, 0),
                              "end": min(end - base, len(segment["text"]))})
    return locations


def _fact_key(claim: dict[str, Any]) -> tuple[Any, ...]:
    """Exact graph fact identity, including qualifiers and asserted/denied/presumed status."""
    return tuple(claim.get(key) for key in (
        "subject", "predicate", "kind", "object", "value", "qualifier", "polarity"
    ))


def _within_word(corpus: str, start: int, end: int) -> bool:
    """Whether either end cuts through Unicode alphanumerics or underscores."""
    def word(char: str) -> bool:
        return char.isalnum() or char == "_"

    return start < end and (
        (start > 0 and word(corpus[start - 1]) and word(corpus[start]))
        or (end < len(corpus) and word(corpus[end - 1]) and word(corpus[end]))
    )


def measure_future_fact_leak(series_id: str, upto_vol: int) -> dict[str, Any]:
    """Search future claim values across the active series' cutoff-visible page text."""
    if not paths.graph_db().is_file():
        raise FileNotFoundError(f"No graph at {paths.graph_db()} -- run `wiki graph build` first.")

    searchable: list[tuple[dict[str, Any], str]] = []
    prior_facts: dict[tuple[Any, ...], list[str]] = {}
    with closing(store.connect(read_only=True)) as conn:
        for row in conn.execute(
            "SELECT * FROM claims WHERE first_vol <= ? ORDER BY claim_id", (upto_vol,)
        ):
            prior = dict(row)
            prior_facts.setdefault(_fact_key(prior), []).append(prior["claim_id"])
        for claim in future_claims_at(conn, upto_vol):
            if claim["value"] is not None:
                searchable.append((claim, claim["value"]))
            elif claim["object"] is not None:
                entity = conn.execute(
                    "SELECT canonical FROM entities WHERE entity_id = ?", (claim["object"],)
                ).fetchone()
                if entity is not None:
                    searchable.append((claim, entity["canonical"]))

    corpus, segments, pages = _cutoff_corpus(upto_vol)
    matches = []
    for claim, value in searchable:
        start = corpus.find(value)
        if start < 0:
            continue
        end = start + len(value)
        locations = _locations(segments, start, end)
        matches.append({
            **{key: claim[key] for key in ("claim_id", "subject", "predicate", "kind",
                                          "object", "value", "first_vol", "qualifier", "polarity")},
            "search_value": value, "corpus_start": start, "corpus_end": end,
            "locations": locations,
            "prior_claim_ids": prior_facts.get(_fact_key(claim), []),
            "within_word": _within_word(corpus, start, end),
            "other_page": any(loc["entity_id"] != claim["subject"] for loc in locations),
        })
    leaked_count = len(matches)
    return {
        "channel": "L_query",
        "probe": "future_fact_leak",
        "series": series_id,
        "upto_vol": upto_vol,
        "future_claims_total": len(searchable),
        "leaked_count": leaked_count,
        "future_fact_leak_rate": leaked_count / len(searchable) if searchable else 0.0,
        "matches": matches,
        "artifact_pages": pages,
        "diagnostic_counts": {
            "prior_fact": sum(bool(m["prior_claim_ids"]) for m in matches),
            "within_word": sum(m["within_word"] for m in matches),
            "other_page": sum(m["other_page"] for m in matches),
        },
    }
