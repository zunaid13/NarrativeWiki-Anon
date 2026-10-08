"""[5] Personalized PageRank over the volume-filtered relation subgraph — multi-hop evidence for
`wiki explain` and `synth/assemble.py`'s `mentions_of` field.

Inputs:     `data/04_graph/graph.db`'s `edges` table via `graph/temporal.py::all_edges_at`, already
            filtered to a volume cutoff.
Outputs:    `personalized_pagerank()` — entities ranked by relevance to one seed entity, restarting
            the walk at the seed on every jump. This surfaces relations a page's direct fields miss:
            a friend-of-a-friend, or a faction-mate reached through two AFFILIATED_WITH hops, not
            just the entities directly named in this character's own relation intervals.
Invariants: - Only ever built from `all_edges_at(conn, upto_vol)`, never a raw `edges` query — the
            volume cutoff and interval-end filter must already be applied before an edge reaches networkx, or a
            reader below the cutoff gets nudged toward an entity they cannot know is connected yet
            (CLAUDE.md §1).
            - The graph is undirected and unweighted-by-predicate: `PARENT_OF`/`SIBLING_OF`/
            `AFFILIATED_WITH`/... are all "a relationship exists" for the purpose of finding who is
            contextually close to whom. Direction and predicate label are a display concern
            (synth/assemble.py's relationships field reads `relations_at` for that, not this
            module) — PPR only needs connectivity.
            - The seed entity is always excluded from its own ranked list.
            - PageRank is hand-rolled as a pure-Python power iteration rather than
            `networkx.pagerank()`, whose only implementation in the installed networkx (>=3.2)
            requires scipy — an undeclared dependency this project does not otherwise need for a
            few-hundred-node graph. `wiki doctor` does not check for scipy; do not start calling
            `nx.pagerank()` here without adding it there and to pyproject.toml first.
Contract:   CLAUDE.md file map ("graph/ppr.py ... multi-hop evidence"); docs/handover/PHASE_5.md.
"""

from __future__ import annotations

import sqlite3

import networkx as nx

from . import temporal

_ALPHA = 0.85          # standard PageRank damping factor
_MAX_ITER = 100
_TOL = 1e-10            # L1 change under which the power iteration is considered converged


def build_subgraph(conn: sqlite3.Connection, upto_vol: int) -> nx.Graph:
    """The undirected relation graph visible at `upto_vol`. Parallel edges between the same pair
    (e.g. both COMMANDS and FRIEND_OF between two characters) collapse to one edge with a `weight`
    equal to how many distinct relations connect them — more shared relations means the pair is
    more tightly bound, which personalized_pagerank should weight accordingly.
    """
    graph: nx.Graph = nx.Graph()
    for edge in temporal.all_edges_at(conn, upto_vol):
        subject, obj = edge["subject"], edge["object"]
        if graph.has_edge(subject, obj):
            graph[subject][obj]["weight"] += 1
        else:
            graph.add_edge(subject, obj, weight=1)
    return graph


def _personalized_pagerank_scores(graph: nx.Graph, seed: str) -> dict[str, float]:
    """Power-iteration personalized PageRank, restarting at `seed` on every jump — see module
    docstring for why this is hand-rolled instead of `nx.pagerank()`. Every node in `graph` has
    degree >= 1 by construction (`build_subgraph` only ever calls `add_edge`), so the dangling-node
    branch below is a defensive no-op today, not a case this graph can actually produce.
    """
    nodes = list(graph.nodes)
    scores = {node: 1.0 / len(nodes) for node in nodes}
    weighted_degree = {node: sum(graph[node][nbr].get("weight", 1) for nbr in graph[node]) for node in nodes}

    for _ in range(_MAX_ITER):
        new_scores = {node: (1 - _ALPHA) * (1.0 if node == seed else 0.0) for node in nodes}
        for node in nodes:
            degree = weighted_degree[node]
            if degree == 0:  # pragma: no cover - unreachable given build_subgraph, kept defensive
                new_scores[seed] += _ALPHA * scores[node]
                continue
            share = _ALPHA * scores[node] / degree
            for neighbor in graph[node]:
                new_scores[neighbor] += share * graph[node][neighbor].get("weight", 1)
        delta = sum(abs(new_scores[n] - scores[n]) for n in nodes)
        scores = new_scores
        if delta < _TOL:
            break
    return scores


def personalized_pagerank(
    conn: sqlite3.Connection, entity_id: str, upto_vol: int, *, top_k: int = 8
) -> list[dict[str, float]]:
    """Entities most relevant to `entity_id` at cutoff `upto_vol`, ranked by a PageRank walk that
    restarts at `entity_id` on every jump. Returns `[{"entity_id": ..., "score": ...}, ...]`,
    highest score first, `entity_id` itself excluded, capped at `top_k`.

    Returns `[]` when the entity has no visible relation at this cutoff (a graph of size 0 or 1,
    or the entity simply is not a node yet) — not an error. A character can be real and have a
    page with no relations yet at an early cutoff.
    """
    graph = build_subgraph(conn, upto_vol)
    if entity_id not in graph or graph.number_of_nodes() < 2:
        return []

    scores = _personalized_pagerank_scores(graph, entity_id)
    scores.pop(entity_id, None)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"entity_id": eid, "score": score} for eid, score in ranked[:top_k]]
