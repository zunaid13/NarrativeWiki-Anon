"""[7] Assembles `data/06_bundle/` — everything the SPA loads (CONTRACTS §6).

Inputs:     The gazetteer, `data/05_pages/<id>/v{NN}.json` files already written by
            `wiki synthesize`, `data/04_graph/graph.db` (read only through `graph/temporal.py`
            and `graph/ppr.py`, same discipline every other module reads it), `data/04b_events/
            events.db` (read only through `graph/events.py`, Phase 21's `build_relationship_
            bundles`), the mention index and parsed paragraphs (for codex evidence), a
            corpus-wide `MentionAutomaton` (`site/wikify.py`), and an `LLMClient` for the one
            generative step this phase adds: `codex_summary`.
Outputs:    Six kinds of bundle document, one function each below, written to disk by
            `cli.py::site_build` (this module builds dicts; it does no file I/O of its own,
            matching `synth/assemble.py`'s "assemble, don't write" split):
              - `build_index`               -> `data/06_bundle/index.json`
              - `build_pages_bundle`        -> `data/06_bundle/pages/<entity_id>.json`, one per character
              - `build_codex_bundles`       -> `data/06_bundle/codex/{factions,places,tech}.json`
              - `build_relationship_bundles`-> `data/06_bundle/relationships/<a>--<b>.json` (Phase 21)
              - `build_timeline_bundles`    -> `data/06_bundle/timeline/v{NN}.json` (Phase 21 part 3)
              - `build_links`               -> `data/06_bundle/links.json`
              - `build_search`              -> `data/06_bundle/search.json`
Invariants: - CLAUDE.md §1: the bundle must never contain text whose `first_vol` exceeds the
            cutoff it is built for. Every function here filters by `entity["first_vol"] <=
            upto_vol` (and, for an alias, the alias's own `first_vol` — see `site/wikify.py`)
            before anything of that entity reaches a bundle file.
            - Judgment call (docs/handover/PHASE_7.md, extending CONTRACTS §5.1's "cache once
            per cutoff at which the claim set changes" to bundle SHAPE, not just to whether a
            page file gets written): `pages/<id>.json` and `codex/*.json` are both SPARSE
            multi-cutoff maps — `{"cutoffs": {"<vol>": {...snapshot...}}}` — carrying only the
            cutoffs at which something actually changed. A client picks a volume and resolves
            the nearest cutoff KEY <= that volume, exactly mirroring `synth/cache.py::
            previous_page`'s own on-disk walk. `pages/<id>.json`'s sparse keys come directly
            from whichever `v{NN}.json` files `wiki synthesize` actually wrote (CONTRACTS §5.1's
            "13x collapse" already did the skipping); `codex/*.json`'s sparse keys are decided
            HERE, by `_state_hash`, because no per-cutoff codex file exists on disk to mirror.
            - `_state_hash` (the codex cache key) covers evidence AND `members`/`related`, not
            evidence alone — a codex entry whose summary text is unchanged but whose member list
            just grew (a character newly affiliated) still needs a new cutoff entry, or a client
            resolving "nearest cutoff <= N" would show the stale member list at N. This is the
            one place this phase's cache logic deliberately diverges from `synth/cache.py`'s
            single-hash `unchanged()` — see docs/handover/PHASE_7.md for the worked example.
            Whether a NEW CUTOFF ENTRY is written and whether the LLM is RE-CALLED are two
            separate questions (Phase 22 B4): `build_codex_bundles` writes a new entry whenever
            `_state_hash` changes, but only calls `_generate_codex_summary` again when the
            evidence-only hash changed too — a membership-only change reuses the previous
            volume's summary text/evidence rather than re-paying for an unchanged prompt.
            - `codex_summary` follows the exact same "no evidence, no LLM call, field stays
            `None`" discipline `synth/prose.py` established for `background`/`personality`
            (CLAUDE.md §1/§2) — `_generate_codex_summary` returns `None` outright when
            `_cap_codex_evidence` found nothing, never asking the model to invent a
            definition from zero passages. Evidence is capped by BOTH window count and a total
            token budget (`window.codex_max_evidence_tokens`, Phase 22 B4) — a window-count cap
            alone let a handful of merged windows for a corpus-wide entity balloon past 30k input
            tokens for a one-sentence summary.
            - `links.json`'s value per entity is `{"route", "canonical", "type"}`, not a bare
            route string — CONTRACTS §6 only says "entity_id → route"; the extra two fields are
            this phase's judgment call (docs/handover/PHASE_7.md) so the reference static viewer
            (and any future replacement) can render an affiliation/relationship/PPR-neighbour
            link's display name without a second lookup file. `route` alone remains a strict
            subset of that object, so anything reading only `.route` still works.
            - Phase 21 part 1: `build_relationship_bundles` is fully deterministic — no LLM call,
            no `codex_summary`-style generative step — mirroring Phase 20's `build_quotes_section`,
            not `_generate_codex_summary`. It reuses the SAME sparse-multi-cutoff / state-hash
            gating `build_codex_bundles` established (`_relationship_state_hash` is that
            module's `_state_hash`, scoped to a pair instead of one entity), and the same
            cutoff-filtered reads (`graph/temporal.py::relations_between`/
            `relation_history_between`, `graph/events.py::shared_events_at`/`event_claims_at`) —
            nothing here queries `intervals`/`edges`/`events`/`event_claims` directly.
            - Phase 21 part 2: a FACTION codex entry additionally carries `key_events` — scenes
            where >= 2 of the faction's current members co-appear (`graph/events.py::
            group_events_at`, `_faction_key_events`), fully deterministic like part 1's
            `shared_scenes`. Config-gated per codex `kind` (`codex_pages.<kind>.max_key_events`,
            currently only set for `factions`) rather than hard-coded to that kind name, so
            `places`/`tech` entries stay byte-identical to their pre-Phase-21-part-2 shape and
            `_state_hash` — the config's ABSENCE, not the kind string, is what `build_codex_
            bundles` branches on.
            - Phase 22 A3 (fixing S1): `_members_and_related`'s `members` bucket is now built only
            from relations flagged `membership: true` in `config/extraction.yaml` — before this,
            ANY relation touching a codex entity counted as membership, so a captured character's
            ENEMY_OF edge to their captor rendered them as a "member" of it. ENEMY_OF/RIVAL_OF now
            route to a new `adversaries` bucket instead (sparse — omitted, not `[]`, when empty,
            same convention `key_events` already used, and folded into `_state_hash` the same way).
            `_faction_key_events` also now requires the faction itself to be a participant of the
            scene (or its `location`), not just >=2 of its members — two factions sharing most of
            their roster no longer render byte-identical `key_events` lists.
            - Phase 22 A4 (fixing S4): every `beat_summary` — in a faction's `key_events`, a
            relationship pair's `shared_scenes`, and a volume's `timeline` events — now gets the
            same `site/wikify.py` hyperlink pass prose/codex `summary` text already got, so a
            reader can follow a name mentioned in a scene recap to that entity's own page.
            `_faction_key_events`/`_shared_scenes_between` take the same `automaton`/
            `entities_by_id`/`settings` args `build_codex_bundles` already threads through for
            `_generate_codex_summary`, wikifying at each call's own `vol` (already the correct
            per-cutoff value for both); `build_timeline_bundles` gains the same three args and
            wikifies at each event's OWN `row["vol"]`, not the function's `upto_vol` parameter,
            since a volume's timeline is permanently fixed regardless of what cutoff a later
            rebuild runs at (see this function's own docstring). `_faction_key_events` excludes the
            faction's own `entity_id` from linking (matching `_generate_codex_summary`'s self-link
            exclusion); the other two have no single "self" entity to exclude. `build_timeline_
            bundles`'s outer `chapters[].chapter_idx` also becomes 1-based here — it was the raw
            0-based `graph/events.py` value, rendered directly as "Chapter 0" by `app.js`; the
            per-event `chapter_idx` `_faction_key_events`/`_shared_scenes_between` carry (unused by
            any renderer) is untouched.
            - Phase 21 part 3: `build_timeline_bundles` has no sparse-multi-cutoff/state-hash
            question at all (unlike every other function above) — a volume's own events are fixed
            the moment `wiki events-build` runs, since `events.vol_start` always equals `vol`
            (CONTRACTS §4b.1, an event is never forward-dated), so `timeline/v{NN}.json` for a
            given volume is either absent (no events) or has exactly one, permanently-stable
            shape. `graph/events.py::events_in_volume` is the new sanctioned reader this relies
            on, distinct from `events_at`'s cumulative `vol_start <= vol` semantics.
Contract:   docs/CONTRACTS.md §6, §6.3.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .. import paths
from ..extract.windows import Window, build_windows
from ..graph import events as events_store
from ..graph import ppr, publication, store, temporal
from ..llm.budget import estimate_tokens
from ..synth.prose import CITE_INSTRUCTION, cited_evidence, keyed_lines
from .wikify import wikify_text

_PAGE_VOL_RE = re.compile(r"v(\d+)\.json$")

_TYPE_LABEL = {
    "FACTION": "a faction or organization",
    "LOCATION": "a location",
    "EVENT": "a battle or event",
    "TECH": "a notable technology, item, spell or ability",
}


class CodexSummary(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    cited: list[str] = Field(default_factory=list)   # [34] keys of the passages the text rests on


class JSONClient(Protocol):
    def complete_json(
        self, stage: str, prompt: str, schema: type[BaseModel], system: str | None = None
    ) -> BaseModel: ...


# ---------------------------------------------------------------------------
# index.json / links.json / search.json — pure functions of the gazetteer
# ---------------------------------------------------------------------------


def build_index(
    gaz: dict[str, Any], settings: Any, upto_vol: int, written_ids: set[str] | None = None
) -> dict[str, Any]:
    """CONTRACTS §6 `index.json`: series metadata, volume list, and the visible character
    roster. `field_labels` is Phase 7's addition so the reference static viewer renders attribute
    display names generically, the same way every server-side renderer already does, instead of
    hard-coding predicate names in JavaScript. `sections` is Phase 13's equivalent for the page
    outline itself — `settings.page_outline`, so `app.js` never hard-codes a section title or
    order either.

    `written_ids` (Phase 23 E6, optional — `None` preserves the pre-E6 behaviour exactly):
    restricts the CHARACTER roster to entity_ids that actually have an on-disk page
    (`site/bundle.py::build_pages_bundle`'s own returned keys) — before this, a character whose
    every cutoff got gated out by `page_gate.min_claims`/S5 (or one whose gazetteer entry simply
    predates a page ever being synthesized for it) still appeared in the roster, search, and
    `links.json`, and clicking it 404'd (the audit's real example: `saint-ruvinheigen`). Restricted
    to CHARACTER only — a codex entity has its own separate, already-correct written-existence
    check (`build_codex_bundles`'s own `cutoffs` gate)."""
    manifest_titles: dict[int, str] = {}
    manifest_path = paths.parsed_manifest()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_titles = {v["vol"]: v.get("title", f"Volume {v['vol']}") for v in manifest.get("volumes", [])}

    characters = sorted(
        (
            {
                "entity_id": e["entity_id"],
                "canonical": e["canonical"],
                "first_vol": e["first_vol"],
                "importance": e["importance"],
            }
            for e in gaz["entities"]
            if e["type"] == "CHARACTER"
            and e["first_vol"] <= upto_vol
            and (written_ids is None or e["entity_id"] in written_ids)
        ),
        key=lambda c: -c["importance"],
    )

    return {
        "series": {"id": settings.series_id, "title": settings.series_title},
        "upto_vol": upto_vol,
        "volumes": [{"vol": v, "title": manifest_titles.get(v, f"Volume {v}")} for v in range(1, upto_vol + 1)],
        "characters": characters,
        "field_labels": {"attributes": {p.lower(): cfg.get("display", p) for p, cfg in settings.attributes.items()}},
        "sections": [{"key": s["key"], "kind": s["kind"], "title": s["title"]} for s in settings.page_outline],
    }


def _resolves(
    entity_id: str, entities_by_id: dict[str, Any], written_ids: set[str] | None
) -> bool:
    """Phase 26: the `build_links` membership rule, reusable by any bundle that emits a bare
    `entity_id`. A CHARACTER resolves only when it got a page written; every other type is a
    codex entity and always has an anchor to point at."""
    entity = entities_by_id.get(entity_id)
    if entity is None:
        return False
    return written_ids is None or entity["type"] != "CHARACTER" or entity_id in written_ids


def build_links(
    gaz: dict[str, Any], settings: Any, upto_vol: int, written_ids: set[str] | None = None
) -> dict[str, Any]:
    """CONTRACTS §6 `links.json`, extended per this module's docstring: `entity_id ->
    {route, canonical, type}`. Filtered to `first_vol <= upto_vol` — an entity the reader has
    not reached yet gets no bundle footprint at all, not even its existence as a link target.

    `written_ids` (Phase 23 E6, optional, CHARACTER-only — see `build_index`'s docstring for the
    full rationale): a CHARACTER with no on-disk page is excluded even if `first_vol <= upto_vol`,
    so nothing else in the bundle can link to a 404. Non-CHARACTER entities are unaffected."""
    return {
        e["entity_id"]: {
            "route": settings.route_for(e["type"], e["entity_id"]),
            "canonical": e["canonical"],
            "type": e["type"],
        }
        for e in gaz["entities"]
        if e["first_vol"] <= upto_vol
        and (written_ids is None or e["type"] != "CHARACTER" or e["entity_id"] in written_ids)
    }


def build_search(
    gaz: dict[str, Any], settings: Any, upto_vol: int, written_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """CONTRACTS §6 `search.json`: one entry per surface form actually revealed by `upto_vol`,
    each carrying its OWN `first_vol` (an alias can be revealed later than the entity itself —
    same rule `site/wikify.py` applies to link targets).

    `written_ids` (Phase 23 E6, optional, CHARACTER-only — see `build_index`'s docstring): a
    CHARACTER with no on-disk page is excluded from search results the same way it is from the
    roster and `links.json`."""
    entries: list[dict[str, Any]] = []
    for e in gaz["entities"]:
        if e["first_vol"] > upto_vol:
            continue
        if written_ids is not None and e["type"] == "CHARACTER" and e["entity_id"] not in written_ids:
            continue
        route = settings.route_for(e["type"], e["entity_id"])
        seen: set[str] = set()
        for sf in e.get("surface_forms", []):
            name = sf["text"]
            first_vol = int(sf.get("first_vol", e["first_vol"]))
            if first_vol > upto_vol or name in seen:
                continue
            seen.add(name)
            entries.append({"name": name, "entity_id": e["entity_id"], "first_vol": first_vol, "route": route})
        if e["canonical"] not in seen:
            entries.append(
                {"name": e["canonical"], "entity_id": e["entity_id"], "first_vol": e["first_vol"], "route": route}
            )
    entries.sort(key=lambda x: x["name"].lower())
    return entries


# ---------------------------------------------------------------------------
# pages/<entity_id>.json — wikified copies of what wiki synthesize already wrote
# ---------------------------------------------------------------------------


def _citations_for(evidence: list[str], print_pages: dict[str, int] | None) -> list[dict[str, Any]]:
    """Phase 23 E4: `evidence` (a prose entry's exact para_id list, CONTRACTS §5) enriched with
    `vol` (parsed from the para_id's own `vNN:` prefix — always present, needs no lookup) and
    `print_page` (from `print_pages`, a `para_id -> print_page` map built at `site build` time
    from `01_parsed/vNN.jsonl`, the same field CLAUDE.md §3's trap #8 already captures during
    ingest). `print_page` is `None` for a para_id absent from the map (no `01_parsed` file loaded
    for that volume, or a paragraph before the first `<a id="page-N">` marker — `ingest/
    segment.py`'s own "null if none seen yet" convention) rather than raising, since this is
    purely additive display data, never evidence-gating. A NEW field (`citations`), not a
    replacement of `evidence` — CONTRACTS §5's `evidence` shape is untouched, so nothing that
    already reads it (e.g. `wiki audit eval`'s citation-entailment check) needs to change."""
    citations = []
    for para_id in evidence:
        vol = int(para_id[1:3]) if para_id.startswith("v") and len(para_id) >= 3 else None
        citations.append(
            {"para_id": para_id, "vol": vol, "print_page": (print_pages or {}).get(para_id)}
        )
    return citations


def written_page_ids(upto_vol: int) -> set[str]:
    """Phase 26 part B: the CHARACTER ids that have a page on disk at or below `upto_vol` —
    exactly the keys `build_pages_bundle` will return, but computable BEFORE it runs, which is
    what lets every wikify pass in this module filter its link targets (`site/wikify.py`'s
    `written_ids`) instead of emitting links to pages the `page_gate` never wrote.

    Recomputed per call rather than cached: `paths.PAGES_DIR` is rebound by `--series` and by the
    probe's build-cutoff redirect, and one `glob` over a few dozen directories is not worth a
    cache-invalidation bug."""
    if not paths.PAGES_DIR.is_dir():
        return set()
    written: set[str] = set()
    for entity_dir in paths.PAGES_DIR.iterdir():
        if not entity_dir.is_dir():
            continue
        for f in entity_dir.glob("v*.json"):
            m = _PAGE_VOL_RE.match(f.name)
            if m and int(m.group(1)) <= upto_vol:
                written.add(entity_dir.name)
                break
    return written


def build_pages_bundle(
    characters: list[dict[str, Any]],
    automaton: Any,
    entities_by_id: dict[str, dict[str, Any]],
    settings: Any,
    upto_vol: int,
    print_pages: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """One sparse multi-cutoff document per character with an on-disk page directory — see
    module docstring for the sparse-map shape. Reads only files `wiki synthesize` already wrote;
    never touches graph.db. Prose text gets `site/wikify.py`'s hyperlinks baked in; every other
    field is copied through unchanged (structured fields already carry bare `entity_id`s — the
    client resolves those against `links.json`, never against wikified text).

    `print_pages` (Phase 23 E4, optional — `None` preserves the pre-E4 shape exactly, no
    `citations` key added, for any caller/test that doesn't pass it): a `para_id -> print_page`
    map, used to attach a `citations` field (see `_citations_for`) to every prose entry so a
    reader can see exactly which volume and print page a paragraph's claims come from — CLAUDE.md
    §3 trap #8's print-page markers, surfaced for the first time instead of only living in
    `evidence`'s bare para_id list."""
    # Phase 26 part B: the link-target filter for every wikify pass below -- a character the
    # page gate never wrote must not be hyperlinked from anyone else's prose (see wikify.py).
    # Keyed by the CUTOFF being wikified, not by `upto_vol`: `mkdocs_wiki.py::resolve_cutoff`
    # emits a character at volume N iff it has a page at some cutoff <= N, so Jakob (first page
    # at v02) is a live target on a v02 page and a 404 on a v01 one -- which is exactly the link
    # the live build shipped.
    written_at: dict[int, set[str]] = {}
    result: dict[str, dict[str, Any]] = {}
    for entity in characters:
        entity_id = entity["entity_id"]
        entity_dir = paths.page_dir(entity_id)
        if not entity_dir.is_dir():
            continue

        cutoffs: dict[str, Any] = {}
        for f in sorted(entity_dir.glob("v*.json")):
            m = _PAGE_VOL_RE.match(f.name)
            if not m:
                continue
            vol = int(m.group(1))
            if vol > upto_vol:
                continue
            page = json.loads(f.read_text(encoding="utf-8"))
            prose = dict(page.get("prose") or {})
            for s in settings.page_outline:
                if s["kind"] != "prose":
                    continue
                entry = prose.get(s["key"])
                if entry:
                    entry = dict(entry)
                    entry["text"] = wikify_text(
                        entry["text"], automaton, entities_by_id, settings, vol,
                        exclude_entity_id=entity_id, written_ids=written_at.setdefault(vol, written_page_ids(vol)),
                    )
                    if print_pages is not None:
                        entry["citations"] = _citations_for(entry.get("evidence") or [], print_pages)
                    prose[s["key"]] = entry
            page = {**page, "prose": prose}
            # Phase 22 A5 (S6): the `lead` summary string (synth/assemble.py::_build_lead) names
            # other entities as plain canonical text -- wikify it the same way prose gets done,
            # excluding this entity itself just like the prose pass above.
            if page.get("lead"):
                page["lead"] = wikify_text(
                    page["lead"], automaton, entities_by_id, settings, vol,
                    exclude_entity_id=entity_id, written_ids=written_at.setdefault(vol, written_page_ids(vol)),
                )
            # Phase 26: the per-pair relationship paragraphs name other characters too, so they
            # get the same hyperlink pass, excluding this page's own subject exactly as the
            # prose and lead passes above do -- otherwise Holo's page links "Holo" back to
            # itself in every paragraph.
            narration = page.get("relationship_prose") or {}
            if narration:
                page["relationship_prose"] = {
                    other: {
                        **entry,
                        "text": wikify_text(
                            entry.get("text"), automaton, entities_by_id, settings, vol,
                            exclude_entity_id=entity_id, written_ids=written_at.setdefault(vol, written_page_ids(vol)),
                        ),
                        **(
                            {"citations": _citations_for(entry.get("evidence") or [], print_pages)}
                            if print_pages
                            else {}
                        ),
                    }
                    for other, entry in narration.items()
                }
            cutoffs[str(vol)] = page

        if cutoffs:
            result[entity_id] = {"entity_id": entity_id, "canonical": entity["canonical"], "cutoffs": cutoffs}
    return result


# ---------------------------------------------------------------------------
# codex/{factions,places,tech}.json — the one new generative step this phase adds
# ---------------------------------------------------------------------------


def _system_prompt(entity_type: str, max_sentences: int) -> str:
    label = _TYPE_LABEL.get(entity_type, "a subject")
    plural = "sentences" if max_sentences != 1 else "sentence"
    return (
        f"You are writing a short encyclopedia definition of {label} from a novel, for a "
        f"wiki page a reader is viewing at one specific point in the story. You are given ONLY "
        f"the passages that mention it so far. Write exactly {max_sentences} {plural}, third "
        "person, encyclopedic tone, no meta-commentary. Do not mention any name, number, place "
        "or event that is not stated in the passages you were given — the reader has not read "
        "past this point in the story.\n\n"
        f"{CITE_INSTRUCTION}\n\n"
        'Respond with JSON only: {"text": "...", "cited": ["W1"]}'
    )


def _codex_windows_for_volume(
    entity_id: str,
    vol: int,
    mentions_by_vol: dict[int, list[dict[str, Any]]],
    records_by_vol: dict[int, list[dict[str, Any]]],
    window_cfg: dict[str, Any],
) -> list[Window]:
    """This entity's raw (uncapped) evidence windows from exactly one volume, reusing
    `extract/windows.py::build_windows` (same context/hard-break discipline `extract/claims.py`
    gets). Split out from the old `_gather_codex_evidence` (Phase 22 B4) so `build_codex_bundles`'s
    cutoff loop can accumulate one volume at a time instead of re-scanning volume 1..vol on every
    cutoff — that rescan was O(upto_vol^2) window builds for O(upto_vol) actual work."""
    records = records_by_vol.get(vol)
    if not records:
        return []
    para_ids = [m["para_id"] for m in mentions_by_vol.get(vol, []) if m["entity_id"] == entity_id]
    if not para_ids:
        return []
    return build_windows(entity_id, para_ids, records, window_cfg)


def _cap_codex_evidence(windows: list[Window], max_windows: int, max_tokens: int) -> list[Window]:
    """Cap a pool of raw windows to the densest subset that fits both a window-count ceiling and a
    total-TOKEN budget (Phase 22 B4). `max_windows` alone is not a token cap — with paragraph
    merging, five windows for a corpus-wide entity like "the Legion" could total ~30k input tokens
    for a one-sentence summary. The densest single window is always kept even if it alone exceeds
    `max_tokens` (same "core over budget is left as-is" rule `windows.py::_shrink_to_budget`
    documents)."""
    ranked = sorted(windows, key=lambda w: -w.mention_count)[:max_windows]
    selected: list[Window] = []
    used_tokens = 0
    for w in ranked:
        tokens = estimate_tokens(w.text)
        if selected and used_tokens + tokens > max_tokens:
            break
        selected.append(w)
        used_tokens += tokens
    selected.sort(key=lambda w: w.para_ids[0])  # stable prompt order -> stable llm/cache.py key
    return selected


def _evidence_hash(windows: list[Window]) -> str:
    para_ids = sorted({pid for w in windows for pid in w.para_ids})
    return hashlib.sha256("|".join(para_ids).encode("utf-8")).hexdigest()


_ADVERSARY_PREDICATES = frozenset({"ENEMY_OF", "RIVAL_OF"})


def _state_hash(
    evidence_hash: str,
    members: list[str],
    related: list[str],
    adversaries: list[str] | None = None,
    key_event_ids: list[str] | None = None,
) -> str:
    """The codex cache key — see module docstring for why this covers more than evidence.

    `adversaries` (Phase 22 A3) stays `None` (omitted, not `[]`) whenever the caller has none to
    report, same convention `key_event_ids` already used — so an entry that never had an
    ENEMY_OF/RIVAL_OF edge keeps a hash byte-identical to before this field existed. `key_event_ids`
    stays `None` for every codex kind except `factions` (Phase 21 part 2) — so a `places`/`tech`
    entry's hash is byte-identical to its pre-Phase-21-part-2 value and does not spuriously
    invalidate every existing codex summary's cache the first time this build runs after the
    upgrade."""
    payload: dict[str, Any] = {"evidence": evidence_hash, "members": sorted(members), "related": sorted(related)}
    if adversaries:
        payload["adversaries"] = sorted(adversaries)
    if key_event_ids is not None:
        payload["key_events"] = sorted(key_event_ids)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _members_and_related(
    conn: sqlite3.Connection,
    entity_id: str,
    upto_vol: int,
    entities_by_id: dict[str, dict[str, Any]],
    relations_cfg: dict[str, Any],
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """`members`: CHARACTER entities whose relation TO this codex entity is flagged
    `membership: true` in `config/extraction.yaml` (Phase 22 A3, fixing S1) — AFFILIATED_WITH/
    SERVES_UNDER/COMMANDS/ORIGIN_FROM/PARTICIPATED_IN/PILOTS, the predicates that actually place a
    character as an associated member/participant of the entity on the other end. Before this
    flag existed, `members` was built from ANY relation touching the entity — including an
    ENEMY_OF edge to the faction that captured a character, which rendered the captive as a
    "member" of their captor. `adversaries`: CHARACTER entities related via ENEMY_OF/RIVAL_OF —
    kept in a separate bucket rather than dropped outright, since "who opposes this faction" is
    real information, just not membership. Any other relation touching this entity (an extraction
    error under the closed vocabulary — nothing else is declared to take this entity's type as an
    object) is silently excluded from both buckets rather than guessed into one.
    `related`: this entity's own PPR neighbours (`graph/ppr.py`, already cutoff-filtered),
    restricted to OTHER non-character entities — the multi-hop "what else is this connected to"
    CONTRACTS §6.1's example `related` field shows, kept separate from `members`/`adversaries` so
    the same character is never listed twice under two labels."""
    verdicts = verdicts or {}
    rows = temporal.relations_at(conn, entity_id, upto_vol)
    members: set[str] = set()
    adversaries: set[str] = set()
    for row in rows:
        verdict = verdicts.get(row["interval_id"])
        if verdict is not None and verdict.get("verdict") == "unsupported":  # Phase 23 B2
            continue
        polarity = publication.claim_polarity(conn, json.loads(row["claim_ids_json"]), upto_vol)
        if polarity == "denied":
            continue
        other = row["object"] if row["subject"] == entity_id else row["subject"]
        if entities_by_id.get(other, {}).get("type") != "CHARACTER":
            continue
        predicate = row["predicate"]
        if relations_cfg.get(predicate, {}).get("membership"):
            members.add(other)
        elif predicate in _ADVERSARY_PREDICATES:
            adversaries.add(other)
    # [29] Membership wins over adversity when both edges survive to the same cutoff. Without
    # this, Milone Company rendered "Members: Kraft Lawrence, Marheit" directly above
    # "Adversaries: Kraft Lawrence, Yarei", and Remelio listed Norah Arendt in both -- each from
    # a real pair of intervals (an employee who is also betrayed by the company), but a reader
    # sees a contradiction, not a nuance. The narrower claim, membership, is the one a codex
    # roster is for; the betrayal is already told in prose and on the relationship page.
    adversaries -= members

    related: list[str] = []
    for ranked in ppr.personalized_pagerank(conn, entity_id, upto_vol, top_k=15):
        other = entities_by_id.get(ranked["entity_id"])
        if other and other["type"] != "CHARACTER":
            related.append(ranked["entity_id"])
        if len(related) >= 5:
            break
    return sorted(members), related, sorted(adversaries)


def _faction_key_events(
    events_conn: sqlite3.Connection | None,
    entity_id: str,
    members: list[str],
    vol: int,
    max_events: int,
    automaton: Any,
    entities_by_id: dict[str, dict[str, Any]],
    settings: Any,
    written_ids_at: dict[int, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """A faction's "key events" (Phase 21 part 2): scenes where at least two of its CURRENT
    members (`_members_and_related`'s `members`, already cutoff-filtered) are co-present —
    events that show the faction acting as a group, not just any scene touching one member (which
    would balloon to nearly every event a well-attested faction's roster ever appears in).
    `[]`, not an error, when `events_conn` is `None` (Phase 19 not yet run) or `members` has fewer
    than 2 entries — the same "no evidence, nothing invented" rule `_shared_scenes_between`
    follows for relationship pages. Ranked by how many current members are present (most
    group-like first), then reading order, then capped at `max_events` — `config/extraction.yaml`
    `codex_pages.factions.max_key_events`.

    Phase 22 A3 (fixing S1's second half): co-presence of >=2 members alone is not enough — two
    factions that happen to share most of their roster (the audit's Medio/Milone example, Holo and
    Lawrence in 43 of 54 scenes) would otherwise render byte-identical "key events" lists, because
    every scene where both are present satisfies the old check for EITHER faction regardless of
    which one the scene is actually about. `entity_id` additionally requires the faction ITSELF to
    be a participant of the scene, or the scene's `location` to be this entity — a scene the
    faction had no stated part in is never one of its key events, no matter how many of its members
    happened to be there.

    Phase 22 A4: `automaton`/`entities_by_id`/`settings` wikify each returned `beat_summary`
    (`site/wikify.py`, excluding the faction itself), the same hyperlink pass its `codex_summary`
    sibling already gets."""
    if events_conn is None:
        return []
    member_set = set(members)
    candidates: list[tuple[int, sqlite3.Row, list[str]]] = []
    seen_event_ids: set[str] = set()
    # [30] Two ways in, and the second is why places had no events. The roster route below asks
    # "were >= 2 of this entity's people together here", which needs a roster of 2 and is the
    # right aboutness test for a FACTION. A scene whose `location` IS this entity needs no such
    # proxy -- it is direct evidence that the scene happened here, strictly stronger than
    # co-presence -- so it is admitted on its own, whatever the roster size. Without this, every
    # LOCATION with fewer than two associated characters could never show a single event, and
    # `group_events_at`'s own `min_participants=2` short-circuit meant most showed none at all.
    for row in events_store.located_events_at(events_conn, entity_id, vol):
        participants = json.loads(row["participants_json"])
        seen_event_ids.add(row["event_id"])
        candidates.append((len([p for p in participants if p in member_set]), row,
                           sorted(p for p in participants if p in member_set)))
    if len(members) >= 2:
        for row in events_store.group_events_at(events_conn, members, vol, min_participants=2):
            participants = json.loads(row["participants_json"])
            if row["event_id"] in seen_event_ids:
                continue
            if entity_id not in participants and row["location"] != entity_id:
                continue
            present = sorted(p for p in participants if p in member_set)
            candidates.append((len(present), row, present))
    candidates.sort(key=lambda c: (-c[0], c[1]["vol"], c[1]["chapter_idx"], c[1]["span_index"]))

    events: list[dict[str, Any]] = []
    for _, event_row, present in candidates[:max_events]:
        quotes = [
            {
                "speaker": claim["speaker"],
                "addressee": claim["object"],
                "quote": claim["quote"],
                "para_id": claim["para_id"],
                "vol": claim["vol"],
            }
            for claim in events_store.event_claims_at(events_conn, event_row["event_id"], vol)
            if claim["kind"] == "quote" and claim["speaker"]
        ]
        events.append(
            {
                "event_id": event_row["event_id"],
                "vol": event_row["vol"],
                "chapter_idx": event_row["chapter_idx"],
                "location": event_row["location"],
                "beat_summary": wikify_text(
                    event_row["beat_summary"], automaton, entities_by_id, settings, vol,
                    exclude_entity_id=entity_id, written_ids=None if written_ids_at is None else written_ids_at.get(vol, set()),
                ),
                "participants": present,
                "quotes": quotes,
                "evidence": _summary_evidence(event_row),
            }
        )
    return events


def _summary_evidence(event_row: Any) -> list[str]:
    """[34] The paragraphs a scene summary names (OPEN_GAPS G7); `[]` for an events.db built
    before the field existed, which renders as before."""
    keys = event_row.keys() if hasattr(event_row, "keys") else ()
    return json.loads(event_row["summary_para_ids_json"]) if "summary_para_ids_json" in keys else []


def _generate_codex_summary(
    client: JSONClient, entity: dict[str, Any], upto_vol: int, windows: list[Window], settings: Any
) -> dict[str, Any] | None:
    """`None`, with no LLM call, when there is no evidence yet — the same "nothing to describe,
    no reason to spend a token" rule `synth/prose.py::_generate_one` established for
    `background`/`personality` (CLAUDE.md §1/§2)."""
    if not windows:
        return None
    max_s = int(settings.prose_config.get("codex_summary", {}).get("max_sentences", 1))
    system = _system_prompt(entity["type"], max_s)
    lines, keymap = keyed_lines([(w.text, list(w.para_ids)) for w in windows], "W")
    prompt = f"{entity['type'].title()}: {entity['canonical']}\nAs known through Volume {upto_vol}.\n\n" + "\n".join(lines)
    answer = client.complete_json("codex_summary", prompt, CodexSummary, system=system)
    text = answer.text.strip()
    # [34] The summary had no evidence gate (OPEN_GAPS G2): t=3 "Patterson Street" said Jo and
    # Phil were married where volume 3 has them engaged. A `codex_check` call (one per summary,
    # like `graph/verify.py`'s one per fact) must find every statement in it supported by the
    # passages it was written from; one regeneration, told what failed, then no summary rather
    # than an unsupported one. [C33] Not MiniCheck: scoring the best chunk of 3.5-7.5k characters,
    # it rejected 109 of 157 one-sentence definitions (median 0.125), "Aid Society" included, whose
    # every term is in its passages. A settings object routing no `codex_check` (tests) skips.
    if "codex_check" in ((getattr(settings, "models", None) or {}).get("routing") or {}):
        unsupported = _codex_unsupported(client, prompt, text)
        if unsupported is not None:
            retry = (prompt + f"\n\nA previous answer was not supported by these passages: \"{text}\" "
                     f"({unsupported}). Write only what the passages themselves state.")
            answer = client.complete_json("codex_summary", retry, CodexSummary, system=system)
            text = answer.text.strip()
            if _codex_unsupported(client, prompt, text) is not None:
                return None
    return {"text": text, "evidence": cited_evidence(getattr(answer, "cited", []), keymap)}


class CodexCheck(BaseModel):
    supported: bool
    unsupported: str = ""


_CODEX_CHECK_SYSTEM = (
    "You check a one-sentence encyclopedia definition against the passages it was written from. "
    "Answer only from the passages, never from anything you know about the book. The definition is "
    "supported when every statement in it is stated in or directly shown by the passages. It is not "
    "supported when it overstates (\"married\" where the passages say \"engaged\"), adds a detail the "
    "passages lack, or gives something to the wrong person or place.\n\n"
    'Respond with JSON only: {"supported": true/false, "unsupported": "the unsupported part, or empty"}')


def _codex_unsupported(client: JSONClient, summary_prompt: str, text: str) -> str | None:
    """None when the passages support `text`; else what they do not support."""
    passages = summary_prompt.split("\n\n", 1)[-1]
    verdict = client.complete_json("codex_check", f"Passages:\n{passages}\n\nDefinition: {text}",
                                   CodexCheck, system=_CODEX_CHECK_SYSTEM)
    return None if verdict.supported else (verdict.unsupported.strip() or "unsupported")


def build_codex_bundles(
    conn: sqlite3.Connection,
    client: JSONClient,
    gaz: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]],
    automaton: Any,
    settings: Any,
    upto_vol: int,
    mentions_by_vol: dict[int, list[dict[str, Any]]],
    records_by_vol: dict[int, list[dict[str, Any]]],
    *,
    events_conn: sqlite3.Connection | None = None,
    force: bool = False,
    wanted_ids: set[str] | None = None,
    written_ids_at: dict[int, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """CONTRACTS §6 `codex/{factions,places,tech}.json`, one sparse multi-cutoff document per
    kind (module docstring). `wanted_ids`, when given, restricts which codex entities get
    (re)processed — mirrors `wiki synthesize --entities` for a cheap smoke test or a targeted
    regeneration, never changing which kind a restricted entity belongs to. `events_conn` feeds
    `key_events` (Phase 21 part 2) — `None` when `events.db` does not exist yet, the same
    "degrade to no evidence" rule `build_relationship_bundles` follows for `shared_scenes`."""
    window_cfg = dict(settings.extraction_behaviour.get("window", {}))
    max_evidence_tokens = int(window_cfg.get("codex_max_evidence_tokens", 2000))
    codex_pages_cfg = settings.extraction.get("codex_pages", {})
    entity_types_cfg = settings.entity_types
    relations_cfg = settings.relations
    verdicts = store.read_verdicts(conn)  # Phase 23 B2 -- {} when wiki verify has not run

    result: dict[str, dict[str, Any]] = {}
    for kind in codex_pages_cfg:
        max_key_events = codex_pages_cfg.get(kind, {}).get("max_key_events")
        entities_of_kind = [
            e
            for e in gaz["entities"]
            if entity_types_cfg.get(e["type"], {}).get("codex") == kind
            and (wanted_ids is None or e["entity_id"] in wanted_ids)
        ]

        bundle: dict[str, Any] = {}
        for entity in sorted(entities_of_kind, key=lambda e: e["entity_id"]):
            entity_id = entity["entity_id"]
            first_vol = int(entity.get("first_vol", 1))
            if first_vol > upto_vol:
                continue

            cutoffs: dict[str, Any] = {}
            prev_state_hash: str | None = None
            prev_evidence_hash: str | None = None
            summary: dict[str, Any] | None = None
            raw_windows: list[Window] = []
            for vol in range(first_vol, upto_vol + 1):
                # Accumulate one volume's worth of raw windows per iteration instead of
                # re-scanning 1..vol from scratch every cutoff (Phase 22 B4) -- O(upto_vol) window
                # builds instead of O(upto_vol^2).
                raw_windows = raw_windows + _codex_windows_for_volume(
                    entity_id, vol, mentions_by_vol, records_by_vol, window_cfg
                )
                windows = _cap_codex_evidence(raw_windows, max_windows=5, max_tokens=max_evidence_tokens)
                evidence_hash = _evidence_hash(windows)
                members, related, adversaries = _members_and_related(
                    conn, entity_id, vol, entities_by_id, relations_cfg, verdicts
                )
                key_events = (
                    _faction_key_events(
                        events_conn, entity_id, members, vol, max_key_events, automaton, entities_by_id,
                        settings, written_ids_at,
                    )
                    if max_key_events
                    else None
                )
                key_event_ids = [e["event_id"] for e in key_events] if key_events is not None else None
                state_hash = _state_hash(evidence_hash, members, related, adversaries, key_event_ids)
                if not force and state_hash == prev_state_hash:
                    continue

                # The summary text only depends on `evidence_hash`, not on members/related/
                # adversaries/key_events (Phase 22 B4) -- a roster change alone (state_hash differs,
                # evidence_hash does not) still needs its own cutoff entry per CONTRACTS §6.1, but
                # must not re-pay the LLM call for a prompt whose evidence text is unchanged.
                if force or summary is None or evidence_hash != prev_evidence_hash:
                    summary = _generate_codex_summary(client, entity, vol, windows, settings)
                prev_evidence_hash = evidence_hash
                cutoff_entry: dict[str, Any] = {
                    "canonical": entity["canonical"],
                    "summary": wikify_text(
                        summary["text"] if summary else None,
                        automaton,
                        entities_by_id,
                        settings,
                        vol,
                        exclude_entity_id=entity_id,
                        written_ids=None if written_ids_at is None else written_ids_at.get(vol, set()),
                    ),
                    "evidence": summary["evidence"] if summary else [],
                    "members": members,
                    "related": related,
                }
                if adversaries:
                    cutoff_entry["adversaries"] = adversaries
                if key_events is not None:
                    cutoff_entry["key_events"] = key_events
                cutoffs[str(vol)] = cutoff_entry
                prev_state_hash = state_hash

            if cutoffs:
                bundle[entity_id] = {"first_vol": first_vol, "kind": kind, "cutoffs": cutoffs}
        result[kind] = bundle
    return result


# ---------------------------------------------------------------------------
# relationships/<a>--<b>.json — Phase 21, pair-scoped, fully deterministic (no LLM)
# ---------------------------------------------------------------------------


def _relationship_pairs(
    conn: sqlite3.Connection, entities_by_id: dict[str, dict[str, Any]], wanted_ids: set[str] | None
) -> list[tuple[tuple[str, str], int]]:
    """CHARACTER-CHARACTER pairs with at least one relation edge anywhere in the corpus, each
    paired with the earliest `vol_start` among that pair's own edges — this pair's own
    `first_vol`, existence/population metadata computed the same way a gazetteer entity's
    `first_vol` already is (at build time, from the full corpus), not re-derived per viewer
    cutoff; nothing about WHICH edge or its content is exposed by this query, only that the pair
    has one and when its earliest evidence starts. `wanted_ids`, when given, keeps a pair if
    EITHER side is in it, mirroring `wiki site build --entities`'s codex filter."""
    rows = conn.execute(
        "SELECT subject, object, MIN(vol_start) AS first_vol FROM intervals "
        "WHERE object IS NOT NULL GROUP BY subject, object"
    ).fetchall()
    pairs: dict[tuple[str, str], int] = {}
    for row in rows:
        a, b = row["subject"], row["object"]
        if entities_by_id.get(a, {}).get("type") != "CHARACTER":
            continue
        if entities_by_id.get(b, {}).get("type") != "CHARACTER":
            continue
        if wanted_ids is not None and a not in wanted_ids and b not in wanted_ids:
            continue
        key = tuple(sorted((a, b)))
        pairs[key] = min(pairs.get(key, row["first_vol"]), row["first_vol"])
    return sorted(pairs.items())


def _relation_entries_between(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    relations_cfg: dict[str, Any],
    cutoff: int,
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """`temporal.relations_between`/`relation_history_between` rows -> the public shape. A pair
    page names both parties explicitly, so `subject`/`object` render as-is (already correctly
    oriented by `graph/contradictions.py::_canonicalize_relation` — see `relations_between`'s
    docstring) with no per-viewer flip like `synth/assemble.py::_relationship_entries` needs.
    `verdicts` (Phase 23 B2): an interval `wiki verify` flagged `unsupported` is withheld here too
    — a pair page must never disagree with the character page it links from."""
    verdicts = verdicts or {}
    entries: list[dict[str, Any]] = []
    for row in rows:
        if (verdicts.get(row["interval_id"]) or {}).get("verdict") == "unsupported":
            continue
        claim_ids = json.loads(row["claim_ids_json"])
        polarity = publication.claim_polarity(conn, claim_ids, cutoff)
        if polarity == "denied":
            continue
        entries.append(
            {
                "subject": row["subject"],
                "predicate": row["predicate"],
                "display": publication.polarity_prefixed(
                    relations_cfg.get(row["predicate"], {}).get("display", row["predicate"]), polarity
                ),
                "object": row["object"],
                "qualifier": row["qualifier"],
                "since_vol": row["vol_start"],
                "evidence": sorted(
                    {item["para_id"] for item in temporal.evidence_at(conn, claim_ids, cutoff)}
                ),
                **({"until_vol": row["vol_end"]} if row["vol_end"] is not None else {}),
            }
        )
    entries.sort(key=lambda e: (e["since_vol"], e["predicate"]))
    return entries


def _shared_scenes_between(
    events_conn: sqlite3.Connection | None,
    a: str,
    b: str,
    vol: int,
    automaton: Any,
    entities_by_id: dict[str, dict[str, Any]],
    settings: Any,
    written_ids_at: dict[int, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Events where both `a` and `b` are participants, visible at `vol`, each carrying its own
    `quote` event_claims verbatim (co-presence is already established at the event level, so
    every quote in the scene is included regardless of ADDRESSEE — unlike `build_quotes_section`,
    which is scoped to one speaker). A quote whose SPEAKER never resolved is dropped (Phase 22,
    matching `app.js::renderSceneCard`'s client-side suppression and `_links_report`'s
    `check_quote_speakers` — an unresolved speaker is dead weight the client already hides, so
    there is no reason to ship it). `[]`, not an error, when `events_conn` is `None` (Phase 19
    not yet run) — relationship pages degrade to relation-history-only, the same "no evidence,
    nothing invented" rule every other generative surface in this codebase follows.

    Phase 22 A4: `automaton`/`entities_by_id`/`settings` wikify each returned `beat_summary`
    (`site/wikify.py`) — no `exclude_entity_id`, unlike `_faction_key_events`/`_generate_codex_
    summary`, since a pair page has no single "self" entity to exclude a link to."""
    if events_conn is None:
        return []
    scenes: list[dict[str, Any]] = []
    for event_row in events_store.shared_events_at(events_conn, a, b, vol):
        quotes = [
            {
                "speaker": claim["speaker"],
                "addressee": claim["object"],
                "quote": claim["quote"],
                "para_id": claim["para_id"],
                "vol": claim["vol"],
            }
            for claim in events_store.event_claims_at(events_conn, event_row["event_id"], vol)
            if claim["kind"] == "quote" and claim["speaker"]
        ]
        scenes.append(
            {
                "event_id": event_row["event_id"],
                "vol": event_row["vol"],
                "chapter_idx": event_row["chapter_idx"],
                "location": event_row["location"],
                "beat_summary": wikify_text(
                    event_row["beat_summary"], automaton, entities_by_id, settings, vol,
                    written_ids=None if written_ids_at is None else written_ids_at.get(vol, set()),
                ),
                "quotes": quotes,
                "evidence": _summary_evidence(event_row),
            }
        )
    return scenes


def _relationship_state_hash(
    relations: list[dict[str, Any]], history: list[dict[str, Any]], scenes: list[dict[str, Any]]
) -> str:
    """The relationship-bundle cache key — `build_codex_bundles::_state_hash`'s pair-scoped
    analogue. Covers relation edges, closed relation history, AND shared-scene identity (not
    scene text — an event's `beat_summary`/quotes never change once written, only whether a NEW
    event/relation enters the visible set), so an unchanged cutoff is skipped exactly like a
    codex entry whose evidence/members/related are unchanged."""
    payload = json.dumps(
        {
            "relations": sorted(
                f"{r['subject']}|{r['predicate']}|{r['display']}|{r['object']}|{r['since_vol']}"
                for r in relations
            ),
            "history": sorted(
                f"{r['subject']}|{r['predicate']}|{r['display']}|{r['object']}|"
                f"{r['since_vol']}|{r.get('until_vol')}"
                for r in history
            ),
            "scenes": sorted(s["event_id"] for s in scenes),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_relationship_bundles(
    conn: sqlite3.Connection,
    events_conn: sqlite3.Connection | None,
    entities_by_id: dict[str, dict[str, Any]],
    automaton: Any,
    settings: Any,
    upto_vol: int,
    *,
    force: bool = False,
    wanted_ids: set[str] | None = None,
    written_ids_at: dict[int, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """CONTRACTS §6.3 `relationships/<a>--<b>.json`, one sparse multi-cutoff document per
    CHARACTER pair that has at least one relation edge anywhere in the corpus (module
    docstring — not every possible pair, only ones extraction actually produced a relation for).
    Fully deterministic: no LLM call, unlike `build_codex_bundles`. Returned dict is keyed
    `"<a>--<b>"` (matching `paths.bundle_relationship`'s filename), value `{"pair": [a, b],
    "first_vol": <int>, "cutoffs": {"<vol>": {"relations", "history", "shared_scenes"}}}`.
    `automaton` (Phase 22 A4) is threaded straight through to `_shared_scenes_between`'s
    `beat_summary` wikify pass; it is not otherwise used here.
    """
    relations_cfg = settings.relations
    verdicts = store.read_verdicts(conn)  # Phase 23 B2 -- {} when wiki verify has not run
    result: dict[str, dict[str, Any]] = {}
    for (a, b), first_vol in _relationship_pairs(conn, entities_by_id, wanted_ids):
        if first_vol > upto_vol:
            continue

        cutoffs: dict[str, Any] = {}
        prev_state_hash: str | None = None
        for vol in range(first_vol, upto_vol + 1):
            relations = _relation_entries_between(
                conn, temporal.relations_between(conn, a, b, vol), relations_cfg, vol, verdicts
            )
            history = _relation_entries_between(
                conn, temporal.relation_history_between(conn, a, b, vol), relations_cfg, vol, verdicts
            )
            scenes = _shared_scenes_between(
                events_conn, a, b, vol, automaton, entities_by_id, settings, written_ids_at
            )
            state_hash = _relationship_state_hash(relations, history, scenes)
            if not force and state_hash == prev_state_hash:
                continue

            cutoffs[str(vol)] = {"relations": relations, "history": history, "shared_scenes": scenes}
            prev_state_hash = state_hash

        if cutoffs:
            result[f"{a}--{b}"] = {"pair": [a, b], "first_vol": first_vol, "cutoffs": cutoffs}
    return result


# ---------------------------------------------------------------------------
# timeline/v{NN}.json — Phase 21 part 3, one per-volume recap, fully deterministic (no LLM)
# ---------------------------------------------------------------------------


def _event_claims_split(events_conn: sqlite3.Connection, event_id: str, vol: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One event's claims (`graph/events.py::event_claims_at`), split into `(state_changes,
    quotes)` by `kind` — the two shapes a timeline card renders (`_shared_scenes_between` only
    ever needed `quotes`; a per-volume recap is the first caller that also wants `state_changes`,
    so this is a new helper rather than a third near-duplicate of that function's quote-building
    loop). A quote whose speaker never resolved is dropped, same as `_shared_scenes_between`/
    `_faction_key_events` (Phase 22) — nothing renders it, so there is no reason to ship it."""
    state_changes: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    for claim in events_store.event_claims_at(events_conn, event_id, vol):
        if claim["kind"] == "state_change":
            state_changes.append(
                {
                    "subject": claim["subject"],
                    "predicate": claim["predicate"],
                    "from_value": claim["from_value"],
                    "to_value": claim["to_value"],
                    "object": claim["object"],
                    "note": claim["note"],
                    "para_id": claim["para_id"],
                    "vol": claim["vol"],
                }
            )
        elif claim["kind"] == "quote" and claim["speaker"]:
            quotes.append(
                {
                    "speaker": claim["speaker"],
                    "addressee": claim["object"],
                    "quote": claim["quote"],
                    "para_id": claim["para_id"],
                    "vol": claim["vol"],
                }
            )
    return state_changes, quotes


def build_timeline_bundles(
    events_conn: sqlite3.Connection | None,
    upto_vol: int,
    automaton: Any,
    entities_by_id: dict[str, dict[str, Any]],
    settings: Any,
    written_ids_at: dict[int, set[str]] | None = None,
) -> dict[int, dict[str, Any]]:
    """CONTRACTS §6.5 `timeline/v{NN}.json` — one document per volume from 1 to `upto_vol` that
    has at least one event, each a chapter-by-chapter recap of every event `graph/events.py::
    events_in_volume` returns for that volume. `{}` when `events_conn` is `None` (Phase 19 not yet
    run), the same "no evidence, nothing invented" rule every other event-layer-backed bundle
    surface follows. Fully deterministic, no LLM call — a volume's own timeline never changes on a
    later rebuild (an event's `vol` is fixed at extraction time), so unlike `build_codex_bundles`/
    `build_relationship_bundles` there is no cache/state-hash question here at all: either a
    volume has events or it doesn't, and if it does its content is stable.

    Phase 22 A4: `automaton`/`entities_by_id`/`settings` wikify each event's `beat_summary` at
    that event's OWN `row["vol"]` (== the outer loop's `vol`, never the function's `upto_vol`
    parameter — a volume's timeline is fixed the moment its events are written, so wikifying at
    anything other than its own volume would make the SAME file's content depend on what `--upto`
    the caller happened to rebuild at, breaking the "permanently stable shape" this docstring's
    previous paragraph promises). [34] The outer `chapters[].chapter_idx` is the raw 0-based index,
    as everywhere else: `site/mkdocs_wiki.py::chapter_label` names it. The Phase 22 `+ 1` (for the
    deleted `app.js`) put every timeline section under the NEXT chapter's title (OPEN_GAPS G6)."""
    if events_conn is None:
        return {}
    result: dict[int, dict[str, Any]] = {}
    for vol in range(1, upto_vol + 1):
        rows = events_store.events_in_volume(events_conn, vol)
        if not rows:
            continue
        chapters: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            state_changes, quotes = _event_claims_split(events_conn, row["event_id"], vol)
            beat_summary = wikify_text(
                row["beat_summary"], automaton, entities_by_id, settings, vol,
                written_ids=None if written_ids_at is None else written_ids_at.get(vol, set()),
            )
            chapters.setdefault(row["chapter_idx"], []).append(
                {
                    "event_id": row["event_id"],
                    "vol": row["vol"],
                    "location": row["location"],
                    "beat_summary": beat_summary,
                    "evidence": _summary_evidence(row),
                    # Phase 26: drop participants that resolve to nothing. Phase 23 E6
                    # restricted the roster/links/search to entity_ids that got a page
                    # (`written_ids`), but timeline bundles were built before that filter and
                    # kept every raw participant -- which is why `wiki audit links` FAILed on
                    # the `may-god` mining artifact in both v01.json and v02.json. Same rule as
                    # `build_links`: a CHARACTER must have a page, everything else is a codex
                    # entity and always resolves (CLAUDE.md §2).
                    "participants": [
                        p
                        for p in json.loads(row["participants_json"])
                        if _resolves(
                            p, entities_by_id,
                            None if written_ids_at is None else written_ids_at.get(vol, set()),
                        )
                    ],
                    # [31] Same rule for state changes: Anne's principal-cast build left 15
                    # subjects/objects with no page (Julia Bell, Carlo...), and `wiki audit links`
                    # FAILed on them. No renderer shows state changes, so nothing visible is lost.
                    "state_changes": [
                        sc for sc in state_changes
                        if all(
                            ref is None or _resolves(
                                ref, entities_by_id,
                                None if written_ids_at is None else written_ids_at.get(vol, set()),
                            )
                            for ref in (sc["subject"], sc.get("object"))
                        )
                    ],
                    "quotes": quotes,
                }
            )
        result[vol] = {
            "vol": vol,
            "chapters": [
                {"chapter_idx": idx, "events": events} for idx, events in sorted(chapters.items())
            ],
        }
    return result
