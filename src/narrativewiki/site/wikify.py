"""[7] Aho-Corasick over already-generated prose -> inline Markdown hyperlinks.

Inputs:     Plain text — a page's `prose.background`/`prose.personality`, or a codex entry's
            `summary` — already generated at a fixed volume cutoff by `synth/prose.py` or this
            phase's own `site/bundle.py::_generate_codex_summary`. The text is already
            spoiler-safe for that cutoff (CLAUDE.md §1); this module only adds links, it never
            adds or removes a fact. A corpus-wide `MentionAutomaton` (`entities/automaton.py`,
            built once over the WHOLE gazetteer by the caller — never rebuilt per call) and the
            gazetteer's `entities_by_id` map.
Outputs:    `wikify_text(...)` -> the same text with entity mentions wrapped as Markdown links
            `[surface](route)`, where `route` is `Settings.route_for(entity_type, entity_id)`
            (VISION.md 2026-09-01: characters get `/character/<slug>`, everything else an anchor
            on a shared codex page).
Invariants: - A mention is only linked when the TARGET is itself safe to reveal at this cutoff:
            `entity.first_vol <= upto_vol`, AND — if the matched surface form is itself an alias
            revealed later than the entity, e.g. a call sign from Volume 3 — that surface form's
            OWN `first_vol <= upto_vol` too (CONTRACTS §2.1: "an alias revealed in Volume 3 must
            not appear on a Volume-1 page even though the character does" applies exactly as much
            to a link target as to the field it decorates).
            - An `ambiguous` surface form (CONTRACTS §2.1) is never linked — it is indexed for
            recall, not for confident wikification; linking it risks sending a reader to the
            wrong page.
            - `exclude_entity_id` (the page's own subject) is never linked to itself.
            - `written_ids` (optional): a CHARACTER target with no page written for this cutoff
              is never linked — the same rule `site/bundle.py::_resolves` applies to bare ids.
            - Longest-match-wins, non-overlapping, word-boundary matches come straight from
            `entities/automaton.py::MentionAutomaton.find_all` — this module adds no matching
            logic of its own, only the cutoff/ambiguity/self filter and the Markdown wrap.
Contract:   docs/CONTRACTS.md §6 — bundle files carry links baked in at build time, never
            reconstructed client-side.
"""

from __future__ import annotations

from typing import Any

from ..entities.automaton import MentionAutomaton


def wikify_text(
    text: str | None,
    automaton: MentionAutomaton,
    entities_by_id: dict[str, dict[str, Any]],
    settings: Any,
    upto_vol: int,
    *,
    exclude_entity_id: str | None = None,
    written_ids: set[str] | None = None,
) -> str | None:
    """`text` with each safely-revealed entity mention wrapped as a Markdown link.

    `None` in, `None` out — prose fields are nullable (CONTRACTS §5), and a codex entry with no
    evidence yet has no summary to wikify either.
    """
    if not text:
        return text

    matches = automaton.find_all(text, upto_vol)
    if not matches:
        return text

    out: list[str] = []
    last = 0
    for start, end, surface, entity_id in matches:
        if entity_id == exclude_entity_id:
            continue
        entity = entities_by_id.get(entity_id)
        if entity is None or entity.get("first_vol", 1) > upto_vol:
            continue

        # The automaton is built only from each entity's OWN surface_forms
        # (entities/automaton.py::build_automaton), so this lookup structurally always succeeds —
        # guarded rather than trusted, matching this project's usual "shouldn't happen" style
        # (e.g. synth/assemble.py::_scalar_field).
        surface_form = next(
            (sf for sf in entity.get("surface_forms", []) if sf["text"] == surface), None
        )
        if surface_form is None:
            continue
        if surface_form.get("ambiguous"):
            continue
        # Phase 26: an epithet-mined form is indexed for RECALL, never linked. `extract/scenes.py
        # ::_build_epithet` applies no content filtering at all beyond a confidence floor, so this
        # channel carries generic descriptors alongside real epithets -- `merchant`, `the wolf`,
        # `the spice`, `Shepherd`, `the old man`, `her prey`. The `ambiguous` flag cannot catch
        # them: it marks a text claimed by MORE THAN ONE entity, and a generic common noun is
        # usually claimed by exactly one, so it stayed False and got linked. The live v1-2 wiki
        # shipped 21 such links, including "Liebert is a timid, high-strung [merchant](kraft-
        # lawrence.md)" on Liebert's own page and "[the wolf](holo.md)-god of the harvest", which
        # splits the phrase mid-token. Precision over recall: in a reference work a wrong link
        # costs more than a missing one, and these forms still reach the reader through the
        # page's alias list and the mention index.
        if surface_form.get("source") == "epithet_mining":
            continue
        if int(surface_form.get("first_vol", entity["first_vol"])) > upto_vol:
            continue
        # Phase 26 part B: never link a CHARACTER with no page on disk. `build_links`/`build_
        # index`/`build_search` have filtered on `written_ids` since Phase 23 E6 and timeline
        # participants since Phase 26 part A, but wikified PROSE never did, so a character below
        # `page_gate.min_claims` still got hyperlinked from someone else's page and 404'd -- the
        # live v1-2 build shipped `[Jakob](../character/jakob.md)` on Lawrence's page against no
        # such file, and `wiki audit links` failed on it. Same rule as `bundle.py::_resolves`:
        # CHARACTER-only, since every other type is a codex anchor that always exists.
        if written_ids is not None and entity["type"] == "CHARACTER" and entity_id not in written_ids:
            continue

        route = settings.route_for(entity["type"], entity_id)
        out.append(text[last:start])
        out.append(f"[{surface}]({route})")
        last = end
    out.append(text[last:])
    return "".join(out)
