"""[2] Assembles clustered entities into `gazetteer.json`. CONTRACTS section 2.1.

Inputs:     The entity list produced by `entities/alias.py::cluster_candidates`.
Outputs:    The gazetteer.json dict (write it with `paths.gazetteer()`), plus `slugify` — the
            one place `entity_id` values are derived from a name, so URLs stay stable.
Invariants: `entity_id` is a slug of the canonical name. It must never be recomputed downstream
            from anything else: claims, scenes and pages all key on the value assigned here.
Contract:   docs/CONTRACTS.md section 2.1.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from unidecode import unidecode

from .. import paths

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Canonical name -> URL slug. ASCII-folds accented names (Milizé -> milize) so URLs and
    filenames never carry non-ASCII characters, while the display name keeps its accent."""
    ascii_name = unidecode(name)
    slug = _SLUG_RE.sub("-", ascii_name.lower()).strip("-")
    return slug or "entity"


def compute_importance(entities: list[dict[str, Any]]) -> None:
    """Normalise `mention_count` into `importance` in [0, 1], in place.

    Importance drives which characters get routed to `--polish` prose in Phase 6
    (`budget.polish_importance_percentile` in config/models.yaml), so it must be comparable
    across a whole gazetteer build, not per volume.
    """
    if not entities:
        return
    max_count = max(e["mention_count"] for e in entities) or 1
    for e in entities:
        e["importance"] = round(e["mention_count"] / max_count, 4)


def build_gazetteer(
    entities: list[dict[str, Any]],
    volumes_covered: list[int],
    *,
    min_mentions: int | None = None,
    max_candidates_per_volume: int | None = None,
    max_llm_alias_pairs: int | None = None,
    alias_pairs_total: int | None = None,
    alias_pairs_dropped: int | None = None,
) -> dict[str, Any]:
    """Assemble the final gazetteer.json dict. Does not write it — callers own I/O.

    The keyword args, all optional, become a `provenance` block (CONTRACTS section 2.1) recording
    the build-time parameters that shaped this specific entity list -- not otherwise reconstructible
    from the entities alone, and load-bearing for `probe/channels/index.py::measure_candidate_mining`,
    whose `min_mentions: int = 3` default was previously an unverified assumption about the artifact
    it measures (LOG.md 2026-09-13, closed by this change; see also
    docs/vision/plans/0008-pre-full-scale-audit.md Sec1.5/B1/B3). Omitted entirely (not written with
    null values) when every arg is `None`, so a caller that doesn't pass any of them gets the exact
    same dict shape as before this change.
    """
    gaz: dict[str, Any] = {
        "entities": sorted(entities, key=lambda e: -e["mention_count"]),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volumes_covered": sorted(volumes_covered),
    }
    provenance = {
        k: v
        for k, v in {
            "min_mentions": min_mentions,
            "max_candidates_per_volume": max_candidates_per_volume,
            "max_llm_alias_pairs": max_llm_alias_pairs,
            "alias_pairs_total": alias_pairs_total,
            "alias_pairs_dropped": alias_pairs_dropped,
        }.items()
        if v is not None
    }
    if provenance:
        gaz["provenance"] = provenance
    return gaz


# [33] An epithet becomes an automaton surface only if it names someone: a capitalised word, and
# no leading article/demonstrative/possessive. "that girl", "a great bird" and "a dam that held
# those desires in check" (Spice and Wolf v3, Diana) matched every v2 "that girl" and pinned a
# page-less v3 character to v2 windows about Holo. Descriptions stay in epithets_vNN.jsonl.
_DESCRIPTION_START = re.compile(r"^(?:a|an|this|that|these|those|my|his|her|its|our|their|your)\b", re.I)


def _epithet_key(text: str) -> str:
    return re.sub(r"^the\s+", "", text.strip().casefold())


def is_name_like_epithet(text: str) -> bool:
    return not _DESCRIPTION_START.match(text) and any(w[:1].isupper() for w in text.split() if w.lower() != "the")


def merge_epithets(
    gaz: dict[str, Any], epithet_records: list[dict[str, Any]], min_confidence: float,
) -> dict[str, Any]:
    """Fold mined epithets (Phase 18, `wiki scenes` + `wiki gazetteer --merge-epithets`) at or
    above `min_confidence` into the matching entity's surface_forms/aliases via
    `alias.add_surface_form`, then rerun `alias.mark_ambiguous` over the whole entity list — a
    mined epithet can newly collide with another entity's existing surface form. Idempotent:
    merging the same epithets_vNN.jsonl a second time adds nothing new (`add_surface_form`'s own
    dedup). Below-floor epithets are skipped here — they still exist on disk for `wiki audit
    scenes`, just not promoted into the automaton. New surface forms this path creates carry
    `"source": "epithet_mining"` (absent on legacy entries, whose origin is unknown) so a
    human can tell mined forms from originally-clustered ones. Mutates and returns `gaz`."""
    from .alias import add_surface_form, mark_ambiguous  # local: alias.py imports this module

    by_id = {e["entity_id"]: e for e in gaz["entities"]}
    kept = [r for r in epithet_records if r["confidence"] >= min_confidence and is_name_like_epithet(r["text"])]
    # [33] One epithet, several referents: Overlord's scene pass gave "the Wise King of the Forest"
    # to Hamusuke once and to Ainz once, and every v1-2 mention of Hamusuke's title went to Ainz.
    # An epithet merges only into an entity with at least twice the records of all others.
    votes: dict[str, Counter[str]] = {}
    for r in kept:
        votes.setdefault(_epithet_key(r["text"]), Counter())[r["entity_id"]] += 1
    for rec in kept:
        v = votes[_epithet_key(rec["text"])]
        if v[rec["entity_id"]] < 2 * (sum(v.values()) - v[rec["entity_id"]]):
            continue
        entity = by_id.get(rec["entity_id"])
        if entity is None:
            continue
        add_surface_form(entity, rec["text"], rec["vol"], source="epithet_mining")

    mark_ambiguous(gaz["entities"])
    return gaz


def aliases_at(entity: dict[str, Any], vol: int) -> list[str]:
    """[31] `entity["aliases"]` a Volume-`vol` reader could know: each alias's own surface-form
    `first_vol` must be `<= vol` (CLAUDE.md §1). Anne's v01 page listed "Anne Blythe" (v5) and
    "Mistress Blythe" (v4). A form with no surface-form record falls back to the entity's
    `first_vol`, the same default `site/bundle.py`'s search index uses."""
    default = entity.get("first_vol", 1)
    first = {sf["text"]: sf.get("first_vol", default) for sf in entity.get("surface_forms", []) if sf.get("text")}
    return [a for a in entity.get("aliases", []) if first.get(a, default) <= vol]


def load(path=None) -> dict[str, Any]:
    """Read a previously built gazetteer.json. `path` defaults to `paths.gazetteer()`."""
    p = path or paths.gazetteer()
    return json.loads(p.read_text(encoding="utf-8"))
