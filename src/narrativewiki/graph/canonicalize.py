"""[17] Value canonicalization — dedupe near-duplicate free-text values for one
(subject, predicate) group before they reach graph/temporal.py::assign_multi_valued.

Inputs:     One group's worth of `{"value", "confidence", "first_vol", "claim_id"}` entries
            (multi-valued attributes and traits only — see config/extraction.yaml `canonicalize:`
            and CLAUDE.md §2 "structured fields are never LLM-generated": this module never
            invents text, it only elects one already-extracted value as the representative of a
            cluster of near-duplicate phrasings).
Outputs:    `list[Cluster]` — one per elected canonical value, `members` in priority order
            (confidence desc, then first_vol asc), `members[0]` is always the canonical entry.
Invariants: - Exact `temporal.normalize_value` duplicates are merged first, at zero embed cost —
            embeddings are spent only on genuinely different phrasings.
            - `embed_fn=None` (embed model unreachable) or fewer than `min_group_size` distinct
            values after normalization: no embedding call is made, every distinct normalized
            value keeps its own cluster. Canonicalization degrades to a no-op, never a crash or a
            dropped value — `graph build` must keep working with no local embed model pulled.
            - Clustering is a single greedy pass over values sorted by priority: each value joins
            the first existing cluster whose representative similarity crosses `threshold`, else
            starts a new one. Deterministic given a fixed input order and a fixed embed_fn.
Contract:   docs/CONTRACTS.md §4.2 ("canonicalization" doc); called from graph/contradictions.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import temporal


class EmbedFn(Protocol):
    def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class Cluster:
    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def canonical(self) -> str:
        return self.members[0]["value"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _priority_key(entry: dict[str, Any]) -> tuple[float, int]:
    return (-entry["confidence"], entry["first_vol"])


def cluster_values(
    entries: list[dict[str, Any]],
    embed_fn: EmbedFn | None,
    threshold: float,
    min_group_size: int = 2,
) -> list[Cluster]:
    """Group near-duplicate values in one `(subject, predicate)` group.

    `entries`: any number of raw observations, possibly repeating the same value across volumes
    (extract/claims.py already collapses exact same-volume duplicates, but not across volumes or
    across differently-worded phrasings of the same fact). Each entry keeps its own `claim_id`
    and `first_vol` through to the output, so the caller can still build correct interval
    observations from whichever cluster a value landed in.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        buckets.setdefault(temporal.normalize_value(entry["value"]), []).append(entry)

    # One representative entry per normalized bucket: highest confidence, earliest first_vol —
    # same tie-break the whole codebase already uses for "which duplicate wins" (e.g.
    # graph/contradictions.py::_resolve_single_valued's top-two-by-confidence rule).
    representatives: list[dict[str, Any]] = []
    for members in buckets.values():
        members.sort(key=_priority_key)
        representatives.append(members[0])
    representatives.sort(key=_priority_key)

    if embed_fn is None or len(representatives) < min_group_size:
        return [Cluster(members=buckets[temporal.normalize_value(rep["value"])]) for rep in representatives]

    vectors = embed_fn([rep["value"] for rep in representatives])

    clusters: list[Cluster] = []
    cluster_vectors: list[list[float]] = []
    for rep, vector in zip(representatives, vectors):
        norm_key = temporal.normalize_value(rep["value"])
        placed = False
        for cluster, cluster_vector in zip(clusters, cluster_vectors):
            # [30] None: the embed model could not encode this text (LLMClient.embed); it
            # stays its own value rather than being compared against anything.
            if vector is not None and cluster_vector is not None and cosine(vector, cluster_vector) >= threshold:
                cluster.members.extend(buckets[norm_key])
                placed = True
                break
        if not placed:
            clusters.append(Cluster(members=list(buckets[norm_key])))
            cluster_vectors.append(vector)

    for cluster in clusters:
        cluster.members.sort(key=_priority_key)
    return clusters


def subsume_values(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phase 22 A2, seam 3: drop redundant values for one `(subject, predicate)` group's
    observations, for predicates marked `subsume: true` in `config/extraction.yaml` (currently
    only APPEARANCE — the "24% of the graph is APPEARANCE noise" finding). Deterministic, no embed
    call, so it degrades exactly like the rest of this module when the embed model is down.
    `observations`: the same `{"value_or_object", "qualifier", "first_vol", "claim_id"}` shape
    `graph/temporal.py::assign_multi_valued` takes, so the caller can call this immediately before
    handing the (possibly pruned) list off to it.

    Two rules, evaluated over the group's DISTINCT `normalize_value`d values:
      1. A value whose words are all present, in any order, in a strictly longer sibling value is
         redundant next to it -- "hair" says nothing "long brown hair" doesn't already say. Token
         SETS, not a raw substring check: "man" is not dropped next to "woman" just because the
         characters happen to appear in order.
      2. A single-token value ("hair", "eyes", "scar") standing alone is too vague to be worth a
         row even with no sibling to subsume it into -- the literal "bare noun... no modifier"
         case. This pipeline has no POS tagger, so a 2-token value is always kept here (the common
         English adjective+noun pattern already supplies rule 2's "modifier"); a genuine 2-word
         compound noun that is just as vague ("hair color") is a rarer case this conservative
         version does not chase.

    Observations for a dropped value are removed entirely from the return value -- the underlying
    claim is untouched in `claims.jsonl` (this only prunes what becomes a graph interval), so the
    evidence stays auditable via `wiki explain`/`wiki trace`, just not rendered as a distinct row.
    """
    by_norm: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_norm.setdefault(temporal.normalize_value(obs["value_or_object"]), []).append(obs)

    token_sets = {value: value.split() for value in by_norm}
    keep: set[str] = set()
    for value, tokens in token_sets.items():
        if len(tokens) == 1:
            continue  # rule 2
        this_set = set(tokens)
        if any(
            other != value and len(other_tokens) > len(tokens) and this_set <= set(other_tokens)
            for other, other_tokens in token_sets.items()
        ):
            continue  # rule 1
        keep.add(value)

    return [obs for obs in observations if temporal.normalize_value(obs["value_or_object"]) in keep]
