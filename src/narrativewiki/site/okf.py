"""[6, 20] OKF Markdown + YAML front matter for one already-assembled page (CONTRACTS §5.2).

Inputs:     One `synth/assemble.py::assemble_page()` dict, its subject's gazetteer entity dict,
            Settings (for `attributes`/`relations` display labels — the same config-driven
            rendering discipline `synth/assemble.py` uses), and an optional `entities_by_id` map
            so affiliation/relationship targets render a canonical name instead of a bare slug.
Outputs:    `render_markdown(...)` -> a single Markdown string: YAML front matter
            (`entity_id`/`canonical`/`type`/`aliases`/`upto_vol`/`categories`/`first_vol`) plus
            body sections, in `settings.page_outline`'s declared order (Phase 13) — the base
            taxonomy's order is Overview, Affiliations, Relationships, Chronology, Quotes,
            Background, Personality, Appearances, but this module never hard-codes that; it
            dispatches on each section's `kind` (`_section_body`).
Invariants: - Pure function of its inputs, no LLM, no graph.db read — same "assemble from
            already-filtered data, nothing new fetched" discipline as `synth/assemble.py`,
            because this module runs on `assemble_page()`'s OUTPUT, which is already
            cutoff-correct (CLAUDE.md §1). There is nothing here that could leak a later volume
            that page dict does not already contain.
            - Section order/titles/labels are all config-driven (`settings.page_outline`,
            `settings.attributes[...]["display"]`), never hard-coded per section or predicate
            name, mirroring CLAUDE.md §2 / `synth/assemble.py`'s own header.
            - `categories` and `Appearances` are not CONTRACTS §5 JSON fields — CONTRACTS §5.2
            names them without defining their content. Judgment call (documented in
            docs/handover/PHASE_6.md): `categories` is the sorted list of affiliation
            `entity_id`s already in `page["fields"]["affiliations"]` (which volume the reader is
            at is irrelevant here, since that list is already cutoff-filtered); `Appearances` is
            every volume number referenced by ANY field's `since_vol`/`vols`/history entry in the
            page, i.e. every volume that contributed a visible fact — derived from the page dict
            alone, not a new evidence read.
            - A scalar field prints every superseded value as "previously: X (vA–vB)", the volumes
            in which it held (Phase 32, req. 6). Nothing marks a current value as changing later:
            that would tell a v1 reader a later volume changes it (req. 9).
            - Phase 20: `kind: quotes` sections render as Markdown blockquotes from
            `page["quotes"]` — a list of {quote, speaker, vol} dicts built deterministically
            by `synth/assemble.py::build_quotes_section`. If `page["quotes"]` is absent or None,
            the section renders as "_No notable quotes yet._".
            - Phase 22 A5: `_overview_lines` shows "(since vN)" only when `field["show_vol"]`
            (U3) — a value known since the character's first appearance renders bare, since
            history/expandable spans are unaffected either way; a value's `polarity` (U7) prefixes
            "presumed "/"not " onto both the current value and every history entry via the
            module-level `_polarity_prefixed` helper (mirroring `synth/assemble.py::
            polarity_prefixed`, kept local rather than imported across the synth/site boundary,
            the same split every other renderer here already has); `field.get("inferred")`
            appends "_(assumed)_" (U6, `attributes.*.default`, never backed by evidence).
            `_relationship_lines` renders `r["blurb"]` (U4, the config-driven label+qualifier
            composition) instead of a raw qualifier-or-label choice, shows "(since vN)" only
            when `r["show_vol"]`, and appends "_(ended vN)_" when `not r["current"]` (the
            `relations_at`/`closed` fix — a superseded relation no longer renders as if live).
            `kind: lead` (S6) renders `page.get("lead")` as a bare paragraph with no "## " header
            — `render_markdown` special-cases it rather than routing through the normal
            `## {title}` loop, since its `page_outline` entry declares `title: ""`.
Contract:   docs/CONTRACTS.md §5.2.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from ..entities.gazetteer import aliases_at
from ..graph import publication


_POLARITY_PREFIX = {"presumed": "presumed ", "denied": "not "}

# Phase 26: how many source links one section's citation line renders before collapsing the
# rest into a count. Not config-driven on purpose -- it is a typographic limit on one line of
# one renderer, not a property of the corpus or the extraction.
_MAX_RENDERED_CITATIONS = 6
_MAX_RELATION_CITATIONS = 3


def _polarity_prefixed(value: str, polarity: str | None) -> str:
    """Phase 22 A5 (U7) — mirrors `synth/assemble.py::polarity_prefixed`; see that module's
    docstring for why this repo keeps the lookup duplicated per renderer instead of shared."""
    return f"{_POLARITY_PREFIX.get(polarity or 'asserted', '')}{value}"


def _label(
    entity_id: str,
    entities_by_id: dict[str, dict[str, Any]] | None,
    settings: Any = None,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
) -> str:
    """Phase 25: `link_depth` (default `None`, today's plain-name behaviour) turns the label into
    a relative Markdown link for the MkDocs wiki emitter -- `site/mkdocs_wiki.py::href` does the
    actual path math; this function only decides WHETHER to call it, from the same `settings`/
    `entities_by_id` every other renderer here already threads through `_section_body`."""
    entity = (entities_by_id or {}).get(entity_id)
    name = entity["canonical"] if entity else entity_id
    if link_depth is None or entity is None or settings is None:
        return name
    # Phase 26 part B: a CHARACTER with no page at THIS cutoff is named, not linked. The live
    # v1-2 wiki emitted `### [Jakob](../character/jakob.md)` on v01's Lawrence page -- Jakob's
    # first page is v02, so the file does not exist at v01 and `wiki audit links` failed on it.
    # Only CHARACTER is filtered: every other type is a codex anchor that always exists.
    if written_ids is not None and entity["type"] == "CHARACTER" and entity_id not in written_ids:
        return name
    from .mkdocs_wiki import href  # local import: mkdocs_wiki -> nothing in site/, no cycle risk

    route = settings.route_for(entity["type"], entity_id)
    return f"[{name}]({href(route, link_depth)})"


def _names_a_character(text: str) -> bool:
    """Does this surface form read as a NAME rather than a description?

    Phase 26 display heuristic: a proper name carries a capitalised token that is not merely
    sentence-initial. "Holo the Wisewolf", "the Wisewolf of Yoitsu" and the bare "Wisewolf"
    pass; "her prey", "a mere dog", "the companions' anxiety" and "The wisewolf" do not.
    Imperfect by construction -- "a representative of almighty God" passes on "God" -- but it
    errs toward keeping a real title in the infobox, which is the failure that matters less.

    This is deliberately NOT the rule `site/wikify.py` uses to decide what to hyperlink. There
    the bar is higher (no mined epithet is ever linked at all), because a wrong link sends a
    reader to the wrong page while a stray infobox entry only looks odd.
    """
    tokens = text.split()
    if not tokens:
        return False
    if len(tokens) == 1:
        return tokens[0][:1].isupper()
    return any(token[:1].isupper() for token in tokens[1:])


def _split_aliases(entity: dict[str, Any], upto_vol: int) -> tuple[list[str], list[str]]:
    """Phase 26: separate real aliases from mined narrative descriptions.

    `entity["aliases"]` is every surface form bar the canonical, with no provenance; the
    provenance lives on `surface_forms[*]["source"]`. The live v1-2 wiki dumped all 45 of
    Holo's into her front matter, so `the companions' anxiety`, `her prey`, `a mere dog`,
    `the merchant` (Lawrence's) and `the shepherd girl` (Norah's) rendered as if they were
    names she goes by. A form from the epithet channel has to look like a name to stay in
    `aliases`; the rest move to `epithets`, kept but not presented as names.
    Returns `(aliases, epithets)`, both sorted.
    """
    sources = {
        sf["text"]: sf.get("source") for sf in entity.get("surface_forms", []) if sf.get("text")
    }
    aliases, epithets = [], []
    for text in aliases_at(entity, upto_vol):
        mined = sources.get(text) == "epithet_mining"
        (aliases if not mined or _names_a_character(text) else epithets).append(text)
    return sorted(aliases), sorted(epithets)


def _front_matter(page: dict[str, Any], entity: dict[str, Any]) -> str:
    affiliations = page["fields"].get("affiliations", [])
    aliases, epithets = _split_aliases(entity, page["upto_vol"])
    doc = {
        "entity_id": page["entity_id"],
        "canonical": page["canonical"],
        "type": entity.get("type", "CHARACTER"),
        "aliases": aliases,
        "upto_vol": page["upto_vol"],
        "categories": sorted({a["entity_id"] for a in affiliations}),
        "first_vol": entity.get("first_vol"),
    }
    if epithets:
        # Kept, but under their own key so nothing reads them as names the character goes by.
        doc["epithets"] = epithets
    return "---\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True) + "---\n"


def _vol_span(start: int, end: int) -> str:
    return f"v{start}" if start == end else f"v{start}–v{end}"


def _overview_lines(
    page: dict[str, Any], attributes_cfg: dict[str, Any], link_depth: int | None = None
) -> list[str]:
    lines: list[str] = []
    for predicate, cfg in sorted(attributes_cfg.items()):
        key = predicate.lower()
        field = page["fields"].get(key)
        if field is None:
            continue
        display = cfg.get("display", predicate)
        if cfg.get("single"):
            value_text = _polarity_prefixed(field["value"], field.get("polarity"))
            line = f"- **{display}:** {value_text}"
            if field.get("show_vol"):
                line += f" (since v{field['since_vol']})"
            if field.get("inferred"):
                line += "  _(assumed)_"
            else:
                line += _evidence_suffix(field.get("evidence"), link_depth)
            for h in field.get("history", []):
                h_value = _polarity_prefixed(h["value"], h.get("polarity"))
                line += f"  \n  _previously: {h_value} ({_vol_span(*h['vols'])})_"
                line += _evidence_suffix(h.get("evidence"), link_depth)
            lines.append(line)
        else:
            values = "; ".join(
                (
                    f"{v['value']} (since v{v['since_vol']})" if v.get("show_vol") else v["value"]
                )
                + _evidence_suffix(v.get("evidence"), link_depth)
                for v in field
            )
            lines.append(f"- **{display}:** {values}")
    return lines


def _family_lines(
    page: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]] | None,
    settings: Any,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
) -> list[str]:
    """[30] One "Family" line in the Overview, as a Fandom infobox has. A relation's `family:`
    value in the taxonomy is the OTHER party's role as seen from this page (`CHILD_OF: parent`).
    The extracted qualifier is not used: it mixes perspectives -- Myne's entry for Effa reads
    "daughter" (Myne) while her entry for Gunther reads "father" (Gunther). A specific role
    outranks the generic `relative` for the same person. Built from the rows the Relationships
    section already renders, so it adds a summary, not a claim."""
    roles = {p: str(cfg["family"]) for p, cfg in (getattr(settings, "relations", None) or {}).items()
             if cfg.get("family")}
    members: dict[str, str] = {}
    for r in page["fields"].get("relationships", []):
        role = roles.get(r["predicate"])
        if (entities_by_id or {}).get(r["entity_id"], {}).get("type", "CHARACTER") != "CHARACTER":
            continue  # family is people: a stray RELATIVE_OF the Merchant's Guild is not
        # Symmetric kinship words hold from either perspective, so a generic relation whose
        # note says "older sister" (Myne and Tuuli) is safely a sibling; "mother" is not.
        if role == "relative" and (kin := re.search(r"\b(sister|brother|sibling|twin|cousin)", r.get("note") or "", re.I)):
            role = "cousin" if kin.group(1).lower() == "cousin" else "sibling"
        if role and r.get("current", True) and members.get(r["entity_id"]) in (None, "relative"):
            members[r["entity_id"]] = role
    return [
        "- **Family:** " + ", ".join(
            f"{_label(e, entities_by_id, settings, link_depth, written_ids)} ({role})"
            for e, role in members.items())
    ] if members else []


def _affiliation_lines(
    page: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]] | None,
    settings: Any = None,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
) -> list[str]:
    def _bullet(a: dict[str, Any]) -> str:
        span = f"v{a['vols'][0]}-{'present' if a['current'] else a['vols'][1]}"
        role = f" — {a['role']}" if a.get("role") else ""
        return (
            f"- {_label(a['entity_id'], entities_by_id, settings, link_depth, written_ids)}{role} ({span})"
            + _evidence_suffix(a.get("evidence"), link_depth, limit=_MAX_RELATION_CITATIONS)
        )

    # Phase 26: `synth/assemble.py` now files a non-membership tie under `other_ties`, not
    # `affiliations` -- Holo's page listed "Medio Company — held captive" and "The Church —
    # member" (she is hunted by them) as if both were places she belonged. Keeping the second
    # group but under its own heading is the honest rendering: the fact is real, the framing
    # was not.
    members = page["fields"].get("affiliations", [])
    others = page["fields"].get("other_ties", [])
    lines = [_bullet(a) for a in members]
    if others:
        if lines:
            lines.append("")
        lines.append("**Other ties**")
        lines.append("")
        lines.extend(_bullet(a) for a in others)
    return lines


def _relationship_lines(
    page: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]] | None,
    settings: Any = None,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
    pair_pages: set[str] | None = None,
) -> list[str]:
    """Phase 26: grouped by the OTHER party, people before places.

    `pair_pages` (2026-09-25): the `<a>--<b>` keys with a relationship page at this cutoff. Each
    person heading links to its pair page, which until now nothing linked to -- all 30 were
    orphans, the nav leaves them out on purpose and the pages "that cite them" never did.

    The shipped v1-2 wiki rendered one flat list sorted by `(since_vol, entity_id)`, so Holo's
    page carried 31 bullets of which 9 were Kraft Lawrence -- and not even adjacent, because
    everything first seen in v1 sorted ahead of everything first seen in v2. Organisations and
    locations were interleaved with people. A reader could not see what any one relationship
    amounted to, which is the complaint this grouping answers: a Fandom page gives each
    significant character its own heading.
    """

    def _bullet(r: dict[str, Any]) -> str:
        line = f"- {r['blurb']}"
        if r.get("show_vol"):
            line += f" (since v{r['since_vol']})"
        if not r.get("current", True):
            line += "  _(ended v{})_".format(r["vol_end"]) if r.get("vol_end") else "  _(ended)_"
        # A relation bullet is one line; it gets a tighter cap than a prose section's
        # `Sources:` footer, which sits on its own line and can afford more.
        line += _evidence_suffix(r.get("evidence"), link_depth, limit=_MAX_RELATION_CITATIONS)
        return line

    entries = page["fields"].get("relationships", [])
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for r in entries:
        by_entity.setdefault(r["entity_id"], []).append(r)

    def _is_character(entity_id: str) -> bool:
        return (entities_by_id or {}).get(entity_id, {}).get("type", "CHARACTER") == "CHARACTER"

    narration = page.get("relationship_prose") or {}
    lines: list[str] = []
    people = [e for e in by_entity if _is_character(e)]
    others = [e for e in by_entity if not _is_character(e)]

    # Earliest-met first, so the section reads in story order rather than alphabetically.
    for entity_id in sorted(people, key=lambda e: (min(r["since_vol"] for r in by_entity[e]), e)):
        heading = f"### {_label(entity_id, entities_by_id, settings, link_depth, written_ids)}"
        pair_key = "--".join(sorted((page.get("entity_id", ""), entity_id)))
        if link_depth is not None and pair_key in (pair_pages or set()):
            from .mkdocs_wiki import href  # local import, as `_label`

            heading += f" · [the relationship]({href(f'/relationships/{pair_key}', link_depth)})"
        lines.append(heading)
        lines.append("")
        entry = narration.get(entity_id)
        if entry and entry.get("text"):
            lines.append(entry["text"])
            lines.append("")
            # [34] the blurb cited nothing (OPEN_GAPS G7); now the paragraphs of the lines it named,
            # as a `_Sources:` line like a prose section's (the inventory reads it as the unit's)
            sources = _source_links(entry.get("evidence"), link_depth, limit=_MAX_RELATION_CITATIONS)
            if sources:
                lines.append(f"_Sources: {sources}_")
                lines.append("")
        lines.extend(_bullet(r) for r in by_entity[entity_id])
        lines.append("")

    if others:
        lines.append("### Organisations & places")
        lines.append("")
        for entity_id in sorted(others, key=lambda e: (min(r["since_vol"] for r in by_entity[e]), e)):
            label = _label(entity_id, entities_by_id, settings, link_depth, written_ids)
            lines.extend(f"- {label} — {_bullet(r)[2:]}" for r in by_entity[entity_id])
    return [line for line in lines if line is not None]


def _source_links(
    para_ids: list[str] | None, link_depth: int | None, limit: int | None = None
) -> str:
    """Phase 26: `para_id`s -> a run of Markdown links into this cutoff's source view.

    Returns `""` when `link_depth is None`. That is the `data/05_pages/**/v{NN}.md` path
    (CONTRACTS §5.2), whose output this module's `render_markdown` docstring promises stays
    byte-identical without a `link_depth`; the source view only exists inside the emitted wiki
    tree, so a citation link has nowhere to point from the intermediate artifact anyway.
    """
    if link_depth is None or not para_ids:
        return ""
    from .mkdocs_wiki import citation_label, href, source_route  # local: no cycle, as `_label`

    # Dedupe, preserving reading order. The `history` section cites every scene it summarised
    # -- 422 paragraphs for Holo v2 -- so cap the rendered run and count the rest. A citation
    # line long enough to bury the prose defeats the purpose of having one.
    cap = _MAX_RENDERED_CITATIONS if limit is None else limit
    ordered = list(dict.fromkeys(para_ids))
    shown, remainder = ordered[:cap], len(ordered) - cap
    links = []
    for para_id in shown:
        route = source_route(para_id)
        label = citation_label(para_id)
        links.append(f"[{label}]({href(route, link_depth)})" if route else label)
    if remainder > 0:
        links.append(f"+{remainder} more")
    return ", ".join(links)


def _evidence_suffix(
    para_ids: list[str] | None, link_depth: int | None, limit: int | None = None
) -> str:
    """Reader-visible provenance: cite traceable evidence or mark its absence explicitly."""
    notice = publication.evidence_notice(para_ids)
    if notice:
        return f"  _({notice})_"
    sources = _source_links(para_ids, link_depth, limit=limit)
    return f"  <sub>{sources}</sub>" if sources else ""


def _quotes_lines(
    page: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]] | None = None,
    link_depth: int | None = None,
) -> str:
    """Render the quotes section as Markdown blockquotes.

    Phase 20: `page["quotes"]` is a list of {quote, speaker, para_id, vol} dicts (or
    None/absent when no qualifying quotes were found for this character at this cutoff).

    Phase 26: the attribution line now carries the speaker's canonical name and a link into
    the source view. Both `speaker` and `para_id` were already on every record and were simply
    discarded here -- the shipped v1-2 wiki rendered a bare "— *(v2)*", which is why not one
    of its 66 pages carried a verifiable citation.
    """
    quotes = page.get("quotes")
    if not quotes:
        return "_No notable quotes yet._"
    parts: list[str] = []
    for entry in quotes:
        text = entry.get("quote", "").strip()
        speaker = entry.get("speaker")
        name = (entities_by_id or {}).get(speaker, {}).get("canonical") if speaker else None
        citation = _source_links([entry["para_id"]] if entry.get("para_id") else None, link_depth)
        if not citation:
            vol = entry.get("vol")
            citation = f"*(v{vol})*" if vol else ""
        attribution = " — ".join(part for part in (name, citation) if part)
        # [31] Speech stitched across a tag keeps the tag (`Well,” replied the Captain, “we`), so
        # straight outer marks left it unbalanced; curly ones close it as the book would.
        text = f"“{text}”" if "”" in text else f'"{text}"'
        parts.append(f"> {text}\n>\n> — {attribution}" if attribution else f"> {text}")
    return "\n\n".join(parts)


def _appearances(page: dict[str, Any]) -> list[int]:
    """Every volume any field's `since_vol`/`vols`/history entry names — see module docstring."""
    vols: set[int] = set()
    for field in page["fields"].values():
        entries = field if isinstance(field, list) else [field]
        for entry in entries:
            if entry.get("since_vol") is not None:
                vols.add(entry["since_vol"])
            for v in entry.get("vols") or ():
                if v is not None:
                    vols.add(v)
            for h in entry.get("history", []):
                for v in h.get("vols") or ():
                    if v is not None:
                        vols.add(v)
    return sorted(vols)


def _format_vol_ranges(vols: list[int]) -> str:
    if not vols:
        return "Unknown."
    ranges: list[str] = []
    start = prev = vols[0]
    for v in vols[1:]:
        if v == prev + 1:
            prev = v
            continue
        ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = v
    ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
    label = "Volume" if len(ranges) == 1 and "-" not in ranges[0] else "Volumes"
    return f"{label} {', '.join(ranges)}"


def _section_body(
    section: dict[str, Any],
    page: dict[str, Any],
    settings: Any,
    entities_by_id: dict[str, dict[str, Any]] | None,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
    pair_pages: set[str] | None = None,
) -> str:
    kind = section["kind"]
    if kind == "lead":  # Phase 22 A5 (S6): bare paragraph, no "## " header (render_markdown)
        return page.get("lead") or ""
    if kind == "fields":
        lines = _overview_lines(page, settings.attributes, link_depth)
        lines += _family_lines(page, entities_by_id, settings, link_depth, written_ids)
        return "\n".join(lines) if lines else "_Nothing known yet._"
    if kind == "affiliations":
        lines = _affiliation_lines(page, entities_by_id, settings, link_depth, written_ids)
        return "\n".join(lines) if lines else "_None known yet._"
    if kind == "relationships":
        lines = _relationship_lines(page, entities_by_id, settings, link_depth, written_ids, pair_pages)
        return "\n".join(lines) if lines else "_None known yet._"
    if kind == "prose":
        entry = page["prose"].get(section["key"])
        if not entry:
            return "_Not yet known._"
        # Phase 26: `synth/prose.py` has always returned the `para_id`s its text was generated
        # from (`{"text", "evidence"}`); nothing rendered them. This is the section-level
        # counterpart of a Fandom page's <ref>s.
        sources = _source_links(entry.get("evidence"), link_depth)
        return f"{entry['text']}\n\n_Sources: {sources}_" if sources else entry["text"]
    if kind == "traits":
        # Phase 30: deterministic, straight from graph rows -- no LLM call, like `kind: fields`.
        rows = (page.get("traits") or {}).get(section["key"]) or []
        if not rows:
            return "_Not yet known._"
        lines = []
        by_volume = section.get("by_volume")
        for i, row in enumerate(rows):
            if by_volume and (i == 0 or rows[i - 1]["since_vol"] != row["since_vol"]):
                lines.append(("\n" if lines else "") + f"**Volume {row['since_vol']}**\n")
            line = f"- {_polarity_prefixed(row['value'], row.get('polarity'))}"
            if row.get("show_vol") and not by_volume:
                line += f" (since v{row['since_vol']})"
            line += _evidence_suffix(row.get("evidence"), link_depth)
            lines.append(line)
        return "\n".join(lines)
    if kind == "related":
        # Phase 30: `mentions_of` has been computed since Phase 22 and rendered nowhere, so a
        # connection the graph knows about reached the reader only if it happened to be named in
        # prose. 12 of 40 character-to-character links in the v1-2 wiki ran one way for that
        # reason. Only entries this pipeline can EXPLAIN are shown: `note` is None for a
        # multi-hop-only PPR result, and an unexplained "related" chip is the noise Phase 29
        # removed from the codex pages, not connectivity.
        rows = [m for m in (page.get("mentions_of") or []) if m.get("note")]
        if not rows:
            return "_None known yet._"
        return "\n".join(
            f"- {_label(m['entity_id'], entities_by_id, settings, link_depth, written_ids)}"
            f" — {m['note']}"
            for m in rows
        )
    if kind == "quotes":
        # Phase 20: deterministic quote section from events.db (synth/assemble.py::build_quotes_section).
        return _quotes_lines(page, entities_by_id, link_depth)
    if kind == "derived":  # only "appearances" exists today (module docstring)
        return _format_vol_ranges(_appearances(page))
    raise ValueError(f"unknown page_outline kind: {kind!r}")


def render_markdown(
    page: dict[str, Any],
    entity: dict[str, Any],
    settings: Any,
    entities_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    link_depth: int | None = None,
    written_ids: set[str] | None = None,
    pair_pages: set[str] | None = None,
) -> str:
    """Phase 25: `link_depth` defaults to `None`, which keeps this function's output byte-identical
    to before it existed -- `data/05_pages/**/v{NN}.md` (CONTRACTS §5.2) is written without it.
    `site/mkdocs_wiki.py` is the only caller that passes it: a `link_depth` turns every
    affiliation/relationship entry into a relative Markdown link (prose links are already baked in
    by `site/wikify.py` before this function ever sees the page dict — see that module's docstring
    on why the CALLER must hand this the bundle's wikified page, not `data/05_pages`'s raw one)."""
    parts = [_front_matter(page, entity), f"\n# {page['canonical']}"]
    for section in settings.page_outline:
        body = _section_body(section, page, settings, entities_by_id, link_depth, written_ids, pair_pages)
        if section["kind"] == "lead":  # Phase 22 A5 (S6): no "## " heading for the lead strip
            if body:
                parts.append(f"\n\n{body}")
            continue
        parts.append(f"\n\n## {section['title']}\n\n" + body)
    parts.append("\n")
    return "".join(parts)
