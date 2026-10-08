"""[24] L_build -- what the future taught the index before the query-time gate ever ran.

Inputs:     `data/<series>/01_parsed/v{NN}.jsonl` (raw paragraph records, always available for
            every ingested volume regardless of how far extraction has progressed),
            `data/<series>/02_entities/{gazetteer.json,candidates.jsonl,mentions.jsonl}`.
Outputs:    `measure_candidate_mining(...)` -- the load-bearing measurement. It re-runs
            `entities/candidates.py::mine_candidates`, the one entity-discovery stage that is
            pure Python (no LLM, no clustering), restricted to volumes `<= upto_vol`, and diffs
            the surviving surface set against what the *shipped* gazetteer's cutoff-visible
            entities actually carry. Any shipped surface form absent from the cutoff-only mining
            run can depend on future volumes for `min_mentions`, candidate ranking, or a
            qualifying per-volume capitalisation ratio. TASK-0004 made that ratio a per-volume
            OR; adding future volumes can still admit candidates that cutoff-only mining drops.
            `measure_vocabulary_exposure(...)` -- a cheap secondary check: how much of the
            automaton's matchable vocabulary, and how many in-scope mentions/claims it produces,
            belong to an entity/surface first revealed after the cutoff. Kept even though it
            reads near-zero on the corpora measured so far (LOG.md 2026-09-13) because it is the
            most direct possible falsification of "the index only knows the past", and a
            near-zero result is itself the finding that motivated `measure_candidate_mining`.
Invariants: - Zero LLM calls. Both measurements are pure functions of files already on disk.
            - Never mutates `data/<series>/02_entities/` -- `mine_candidates` is called on an
              in-memory volumes dict built here, its result is compared and discarded.
Contract:   docs/vision/PHASE_24.md; LOG.md 2026-09-13.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import paths
from ...entities.candidates import mine_candidates


def _load_parsed_volumes(upto_vol: int) -> dict[int, list[dict[str, Any]]]:
    volumes: dict[int, list[dict[str, Any]]] = {}
    for vol in range(1, upto_vol + 1):
        p = paths.parsed_volume(vol)
        if not p.is_file():
            raise FileNotFoundError(f"Missing parsed volume {vol} at {p}; cannot measure cutoff {upto_vol}.")
        volumes[vol] = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return volumes


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def measure_candidate_mining(series_id: str, series_config: dict[str, Any], upto_vol: int, *, min_mentions: int | None = None) -> dict[str, Any]:
    """Compare candidate mining restricted to volumes `<= upto_vol` against the shipped,
    whole-corpus-mined candidate list, on the surfaces that ended up in a cutoff-visible entity.

    Returns:
        {
            "channel": "L_build", "probe": "candidate_mining", "series", "upto_vol",
            "shipped_gazetteer_volumes_covered": [...],
            "cutoff_only_candidate_surfaces": <int>,
            "shipped_candidate_surfaces": <int>,
            "cutoff_visible_entities": <int, entities with first_vol <= upto_vol>,
            "cutoff_visible_surface_forms": <int, their surface_forms count>,
            "surfaces_missing_at_cutoff": [<surface>, ...],  # would not survive a cutoff-only
                                                              # mining run, but are on the page
            "leak_rate": len(surfaces_missing_at_cutoff) / cutoff_visible_surface_forms,
        }
    """
    gaz_path = paths.gazetteer()
    if not gaz_path.is_file():
        raise FileNotFoundError(f"No gazetteer at {gaz_path} -- run `wiki gazetteer` first.")
    gazetteer = json.loads(gaz_path.read_text(encoding="utf-8"))
    entities = gazetteer["entities"]
    provenance = gazetteer.get("provenance") or {}
    hints = dict(series_config.get("entities") or {})
    parameters = {
        "min_mentions": min_mentions if min_mentions is not None else provenance.get("min_mentions", 3),
        "max_candidates_per_volume": provenance.get(
            "max_candidates_per_volume", hints.get("max_candidates_per_volume", 400)
        ),
    }
    parameter_sources = {
        "min_mentions": ("explicit" if min_mentions is not None else
                         "gazetteer" if "min_mentions" in provenance else "legacy_default"),
        "max_candidates_per_volume": (
            "gazetteer" if "max_candidates_per_volume" in provenance else
            "series_config" if "max_candidates_per_volume" in hints else "default"
        ),
    }
    for name, value in parameters.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid candidate-mining {name}: expected a nonnegative integer, got {value!r}.")
    hints["max_candidates_per_volume"] = parameters["max_candidates_per_volume"]

    volumes = _load_parsed_volumes(upto_vol)
    if not volumes:
        raise FileNotFoundError(f"No parsed volumes <= {upto_vol} on disk for series {series_id!r}.")

    if not paths.candidates().is_file():
        raise FileNotFoundError(f"No shipped candidates at {paths.candidates()} -- run `wiki gazetteer` first.")
    cutoff_candidates = mine_candidates(
        volumes, {**series_config, "entities": hints}, min_mentions=parameters["min_mentions"]
    )
    cutoff_surfaces = {c["surface"] for c in cutoff_candidates}

    shipped_candidates = _load_jsonl(paths.candidates())
    shipped_surfaces = {c["surface"] for c in shipped_candidates}

    visible_entities = [e for e in entities if int(e.get("first_vol", 1)) <= upto_vol]
    visible_surface_forms: set[str] = set()
    missing: set[str] = set()
    for e in visible_entities:
        for sf in e.get("surface_forms", []):
            text = sf["text"]
            visible_surface_forms.add(text)
            # A single-word surface form is exactly what mine_candidates emits; multi-word
            # aliases and any surface added later by alias clustering (not raw mining) are out
            # of scope for this probe -- compare only what the mining stage itself could produce.
            if text in shipped_surfaces and text not in cutoff_surfaces:
                missing.add(text)

    leak_rate = len(missing) / len(visible_surface_forms) if visible_surface_forms else 0.0
    return {
        "channel": "L_build",
        "probe": "candidate_mining",
        "series": series_id,
        "upto_vol": upto_vol,
        "shipped_gazetteer_volumes_covered": gazetteer.get("volumes_covered", []),
        "mining_parameters": parameters,
        "mining_parameter_sources": parameter_sources,
        "cutoff_only_candidate_surfaces": len(cutoff_surfaces),
        "shipped_candidate_surfaces": len(shipped_surfaces),
        "cutoff_visible_entities": len(visible_entities),
        "cutoff_visible_surface_forms": len(visible_surface_forms),
        "surfaces_missing_at_cutoff": sorted(missing),
        "leak_rate": leak_rate,
    }


def measure_vocabulary_exposure(series_id: str, upto_vol: int) -> dict[str, Any]:
    """How much of the cutoff-`upto_vol` mention index and cutoff-visible claim set rests on
    vocabulary (an entity or one of its surface forms) whose own `first_vol` is later than the
    cutoff. Reads the gazetteer, mentions, and both claim sources; duplicate claim IDs union
    their cited paragraphs, as graph construction unions evidence from both producers."""
    gaz_path = paths.gazetteer()
    gazetteer = json.loads(gaz_path.read_text(encoding="utf-8"))
    entities = gazetteer["entities"]

    future_entity_ids = {e["entity_id"] for e in entities if int(e.get("first_vol", 1)) > upto_vol}
    surface_first_vol: dict[tuple[str, str], int] = {}
    total_surface_forms = 0
    future_surface_forms = 0
    for e in entities:
        for sf in e.get("surface_forms", []):
            total_surface_forms += 1
            fv = int(sf.get("first_vol", e.get("first_vol", 1)))
            surface_first_vol[(e["entity_id"], sf["text"])] = fv
            if e["entity_id"] in future_entity_ids or fv > upto_vol:
                future_surface_forms += 1

    def _is_future(entity_id: str, surface: str) -> bool:
        if entity_id in future_entity_ids:
            return True
        return surface_first_vol.get((entity_id, surface), 1) > upto_vol

    future_hit_paras: set[str] = set()
    n_mentions = n_future_mentions = 0
    mentions_path = paths.mentions()
    with mentions_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)
            if int(m["vol"]) > upto_vol:
                continue
            n_mentions += 1
            if _is_future(m["entity_id"], m.get("surface", "")):
                n_future_mentions += 1
                future_hit_paras.add(m["para_id"])

    cited_by_claim: dict[str, set[str]] = {}
    claim_files: list[str] = []
    for vol in range(1, upto_vol + 1):
        sources = [p for p in (paths.claims_volume(vol), paths.scene_claims_volume(vol), paths.infobox_claims_volume(vol), paths.pass_claims_volume("backstory", vol)) if p.is_file()]
        if not sources:
            raise FileNotFoundError(f"No extraction or scene claims for volume {vol}; cannot measure cutoff {upto_vol}.")
        for p in sources:
            claim_files.append(paths.relative(p))
            for c in _load_jsonl(p):
                if int(c.get("first_vol", 1)) > upto_vol:
                    continue
                cited_by_claim.setdefault(c["claim_id"], set()).update(
                    ev["para_id"] for ev in c.get("evidence", [])
                )
    tainted_claim_ids = sorted(cid for cid, cited in cited_by_claim.items() if cited & future_hit_paras)
    n_claims = len(cited_by_claim)
    tainted_claims = len(tainted_claim_ids)

    return {
        "channel": "L_build",
        "probe": "vocabulary_exposure",
        "series": series_id,
        "upto_vol": upto_vol,
        "total_surface_forms": total_surface_forms,
        "future_surface_forms": future_surface_forms,
        "future_surface_form_rate": future_surface_forms / total_surface_forms if total_surface_forms else 0.0,
        "in_scope_mentions": n_mentions,
        "future_vocabulary_mentions": n_future_mentions,
        "future_vocabulary_mention_rate": n_future_mentions / n_mentions if n_mentions else 0.0,
        "cutoff_claims": n_claims,
        "claim_files": claim_files,
        "tainted_claim_ids": tainted_claim_ids,
        "claims_resting_on_future_vocabulary_paragraph": tainted_claims,
        "tainted_claim_rate": tainted_claims / n_claims if n_claims else 0.0,
    }
