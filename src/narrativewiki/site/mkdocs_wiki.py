"""[25] Emits the wiki as MkDocs Material-ready Markdown (CONTRACTS §7), replacing the ES5
hash-routed SPA `site/static_site.py` used to build.

Inputs:     The same in-memory bundle dicts `cli.py::site_build` already builds in one run --
            `pages_bundle`, `codex_bundles`, `relationship_bundles`, `timeline_bundles`,
            `index_doc` (all from `site/bundle.py`, unchanged by this module) -- plus
            `entities_by_id` and `settings`. Nothing here re-reads `data/06_bundle/` from disk;
            the JSON bundle stays the `codex_summary` cache and `wiki audit links`'s target, not
            an intermediate this module has to parse back.
Outputs:    `dist/<series>/wiki/v{NN}/` per cutoff volume 1..upto -- `index.md`,
            `character/<id>.md` (via `site/okf.py::render_markdown`), `codex/{kind}.md`,
            `relationships/<a>--<b>.md`, `timeline/v{NN}.md` -- and a generated `mkdocs.yml`. No
            images: portraits and the illustration gallery were removed in Phase 32 (out of the
            study's scope; future work, `docs/vision/PHASE_32.md` req. 4).
Invariants: - CLAUDE.md §1: every page written for cutoff dir `vNN/` comes from data already
            filtered to `first_vol <= NN` by the bundle builders this module consumes --
            `resolve_cutoff` (below) is the one new place that filtering happens, since a sparse
            multi-cutoff bundle dict (CONTRACTS §6.2/§6.1/§6.4's shared shape) is resolved to one
            cutoff's snapshot HERE, once, centrally -- not re-derived per renderer. Get `<=` wrong
            here (a `<` or a bare `max()`) and a later cutoff's snapshot silently reaches an
            earlier cutoff dir with nothing else in the system positioned to notice; this is why
            `tests/test_spoiler_leak.py::test_sparse_cutoff_resolution_never_selects_a_future_snapshot`
            exists and must keep passing.
            - Prose in a page dict is ALREADY wikified with absolute routes (`[text](/route)`,
            `site/wikify.py`, baked in by `build_pages_bundle`/`build_codex_bundles`/etc. before
            this module ever sees it) -- `_relativize` rewrites those into paths relative to the
            emitting page's own depth with one regex substitution; it never re-wikifies text.
            - `href()`/`_relativize()` are pure functions of a route string and an integer depth,
            with no I/O and no cutoff logic -- `tests/test_mkdocs_wiki.py` exercises them
            directly, since a wrong depth silently produces a broken link mkdocs's own
            `strict: true` build (or `wiki audit links`) is what would catch downstream.
Contract:   docs/CONTRACTS.md §6b (new).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import paths
from .okf import _evidence_suffix, _source_links, render_markdown

_ABS_LINK = re.compile(r"\]\((/[^)\s#]+)((?:#[^)\s]+)?)\)")
_PARA_ID = re.compile(r"^v(\d+):c(\d+):p(\d+)$")


def href(route: str, depth: int) -> str:
    """A `settings.route_for(...)`-shaped route ("/character/holo", "/codex/factions#holo") to a
    path relative to a page `depth` levels below its cutoff root: depth 1 for a page directly in
    `vNN/` (`index.md`), depth 2 for a page one directory down (`character/*.md`,
    `codex/*.md`, `relationships/*.md`, `timeline/*.md`) -- the only two depths this tree's
    layout ever produces."""
    path, _, anchor = route.lstrip("/").partition("#")
    return "../" * (depth - 1) + path + ".md" + (f"#{anchor}" if anchor else "")


def source_anchor(para_id: str) -> str:
    """`v02:c00:p0044` -> `nw-v02-c00-p0044`, the HTML id stamped on that paragraph in the
    source view. Colons are legal in an HTML id but awkward in a CSS/Markdown anchor
    reference, so the separator is normalised to `-` once, here."""
    return "nw-" + para_id.replace(":", "-")


def source_route(para_id: str) -> str | None:
    """`v02:c00:p0044` -> `/source/v02-c00#nw-v02-c00-p0044`, a `settings.route_for`-shaped
    route `href()` can turn into a relative path. `None` for anything not shaped like a
    `para_id`, so a malformed citation degrades to plain text rather than a broken link.

    Phase 26. This is the whole evidence-deep-link mechanism: `para_id` is already an exact,
    stable address for one paragraph (`ingest/segment.py:25`), and `_write_source_pages`
    emits a page per (volume, chapter) with every paragraph carrying `source_anchor`'s id.
    Nothing here reads the EPUB, deliberately -- the LuCaZ Spice and Wolf EPUBs carry 54
    `<a id="page-N">` anchors for 3,529 paragraphs and no per-paragraph ids at all, and
    `ingest/epub.py` records only the FIRST spine document's `<section id>` per chapter even
    though a chapter absorbs continuation documents, so ~60% of paragraphs cannot be mapped
    back to their source file without a full re-parse. Addressing our own normalized text
    sidesteps both and is strictly more precise: the anchor lands on exactly the text the
    extractor was shown.
    """
    match = _PARA_ID.match(para_id or "")
    if not match:
        return None
    vol, chapter, _ = match.groups()
    return f"/source/v{vol}-c{chapter}#{source_anchor(para_id)}"


# Build-scoped, set once by `build_wiki` from the parsed records -- the same module-level-state
# idiom `paths.set_active_series` already uses. Empty means "fall back to chapter_idx + 1", which
# is every caller's pre-[30] behaviour and what the pure-function tests exercise.
_CHAPTER_LABELS: dict[tuple[int, int], str] = {}


def set_chapter_labels(chapters_by_volume: dict[int, dict[int, list[dict[str, Any]]]]) -> None:
    """[30] Number the chapters a reader would number, and name the ones the book names.

    Labels were `chapter_idx + 1` everywhere. Volume 1 opens with a titled PROLOGUE at index 0,
    so every chapter after it rendered one too high: the source index read "PROLOGUE, Chapter 2,
    Chapter 3 ...", the book's own Chapter One was labelled "Chapter 2", and every citation
    inherited it -- `v1 ch.2 P62` sent a reader holding the book to the wrong chapter. Volume 2
    has no front matter, so it was correct by luck, which is how this survived.

    A chapter carrying a title keeps it; an untitled one is numbered by its position among the
    untitled chapters of its own volume. Front matter is counted out of the numbering rather
    than into it, for any volume of any series."""
    _CHAPTER_LABELS.clear()
    for vol, chapters in (chapters_by_volume or {}).items():
        numbered = 0
        for chapter_idx in sorted(chapters):
            records = chapters[chapter_idx]
            title = (records[0].get("chapter_title") or "").strip() if records else ""
            if title:
                _CHAPTER_LABELS[(int(vol), int(chapter_idx))] = title
            else:
                numbered += 1
                _CHAPTER_LABELS[(int(vol), int(chapter_idx))] = f"Chapter {numbered}"


def chapter_label(vol: int, chapter_idx: int) -> str:
    """The reader-facing name of one chapter. Falls back to the pre-[30] `chapter_idx + 1` when
    `set_chapter_labels` has not run, so the renderers here stay pure functions in tests."""
    return _CHAPTER_LABELS.get((int(vol), int(chapter_idx)), f"Chapter {int(chapter_idx) + 1}")


def citation_label(para_id: str) -> str:
    """`v02:c00:p0044` -> `v2 ch.1 P44`, or `v1 PROLOGUE P12` for a chapter the book titles.
    Chapter naming comes from `chapter_label`, so a citation names the chapter a reader holding
    the volume would actually find, not the raw 0-based index plus one."""
    match = _PARA_ID.match(para_id or "")
    if not match:
        return para_id
    vol, chapter, para = match.groups()
    label = chapter_label(int(vol), int(chapter))
    shown = f"ch.{label.split()[-1]}" if label.startswith("Chapter ") else label
    return f"v{int(vol)} {shown} P{int(para)}"


def _relativize(md: str, depth: int) -> str:
    """Rewrite every already-wikified `[text](/route#anchor)` link in `md` to `href(route#anchor,
    depth)`. Anything not shaped like an absolute route (an external http(s) link, a plain
    already-relative path) is left untouched by `_ABS_LINK`'s leading-`/` requirement."""
    return _ABS_LINK.sub(lambda m: f"]({href(m.group(1) + m.group(2), depth)})", md)


def resolve_cutoff(cutoffs: dict[str, Any], vol: int) -> Any | None:
    """CONTRACTS §6.2's own client resolution rule, done once here instead of once per renderer
    or (as before) once in `app.js`: the sparse multi-cutoff map's entry at the greatest key
    `<= vol`, or `None` if `vol` is before every key (nothing visible yet at this cutoff)."""
    keys = sorted((int(k) for k in cutoffs if int(k) <= vol), reverse=True)
    return cutoffs[str(keys[0])] if keys else None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-page-kind renderers
# ---------------------------------------------------------------------------


def _entity_link(
    entity_id: str, entities_by_id: dict[str, dict[str, Any]], settings: Any, depth: int
) -> str:
    """[29] `entity_id` -> `[Canonical](relative/path)`, or the bare canonical name when the id
    is not in `entities_by_id` (nothing to point at). The codex rosters used to print these as
    plain text, so Pasloe named Holo while Holo's page linked back to Pasloe -- a one-way edge
    in a wiki whose whole claim is that it is well connected."""
    entity = entities_by_id.get(entity_id)
    if not entity:
        return entity_id
    canonical = entity.get("canonical", entity_id)
    return f"[{canonical}]({href(settings.route_for(entity['type'], entity_id), depth)})"


def _codex_kinds_at(codex_bundles: dict[str, dict[str, Any]], vol: int) -> list[str]:
    """[29] The codex kinds with at least one entry visible at `vol`. An empty kind is not
    written, not linked from the volume index and not in the nav -- `tech.md` shipped for two
    volumes as a bare `# Technology` heading with no body, reachable from both."""
    return [
        kind
        for kind, entries in codex_bundles.items()
        if any(
            e["first_vol"] <= vol and resolve_cutoff(e["cutoffs"], vol) is not None
            for e in entries.values()
        )
    ]


def _render_codex_kind(
    kind: str,
    title: str,
    entries: dict[str, Any],
    vol: int,
    settings: Any,
    entities_by_id: dict[str, dict[str, Any]],
    written_ids: set[str] | None = None,
) -> str:
    """One Markdown page per codex kind (factions | places | tech), every visible entry as its
    own `## Canonical {#slug}` anchor section -- `href("/codex/<kind>#<slug>", depth)` resolves
    straight to it via Material's `attr_list` extension."""
    parts = [f"# {title}\n"]
    rows = sorted(
        ((eid, e) for eid, e in entries.items() if e["first_vol"] <= vol),
        key=lambda pair: entities_by_id.get(pair[0], {}).get("canonical", pair[0]),
    )
    for entity_id, entry in rows:
        snapshot = resolve_cutoff(entry["cutoffs"], vol)
        if snapshot is None:
            continue
        canonical = entities_by_id.get(entity_id, {}).get("canonical", entity_id)
        parts.append(f"\n## {canonical} {{#{entity_id}}}\n")
        if snapshot.get("summary"):
            parts.append(f"\n{snapshot['summary']}\n")
            # [34] the passages the summary rests on (OPEN_GAPS G7: codex prose cited nothing)
            if sources := _source_links(snapshot.get("evidence"), 2, limit=4):
                parts.append(f"\n_Sources: {sources}_\n")
        else:
            parts.append("\n_Not yet known._\n")
        # [29] `related` (the entry's PPR neighbours) is deliberately NOT rendered. It stays in
        # the bundle and in CONTRACTS §6.1 for any consumer that wants it, but on this corpus it
        # printed the same four globally-central entities -- Medio/Milone/Remelio/Rowen -- under
        # every place and faction alike, which is a property of personalized PageRank on a
        # 2-volume graph, not a fact about any one entry.
        # ponytail: unconditional suppression; render it again if a bigger graph makes the
        # neighbour sets actually differ per entry.
        # [30] The roster heading follows the entry's own entity type. "Members" is a FACTION
        # word: Pasloe, a village, shipped "**Members:** Count Ehrendott, Holo" because
        # ORIGIN_FROM carries `membership: true`, so being *from* a place filed you as one of its
        # members. Config supplies the word (`entity_types.<TYPE>.roster_labels`), defaulting to
        # today's text, so a series names its own rosters rather than inheriting a faction's.
        entity_type = entities_by_id.get(entity_id, {}).get("type", "FACTION")
        labels = (settings.entity_types.get(entity_type, {}) or {}).get("roster_labels") or {}
        for bucket, default_label in (("members", "Members"), ("adversaries", "Adversaries")):
            label = labels.get(bucket, default_label)
            ids = snapshot.get(bucket) or []
            if not ids:
                continue
            # [33] A character with no page at this cutoff is named, not linked: Anne's places
            # roster linked Wilson/Blythe/Margaret, and the strict build failed on the dead links.
            names = ", ".join(
                entities_by_id.get(i, {}).get("canonical", i)
                if written_ids is not None and entities_by_id.get(i, {}).get("type") == "CHARACTER" and i not in written_ids
                else _entity_link(i, entities_by_id, settings, depth=2)
                for i in ids)
            parts.append(f"\n**{label}:** {names}\n")
        for event in snapshot.get("key_events") or []:
            where = f"v{event['vol']}" + (
                f" · {chapter_label(event['vol'], event['chapter_idx'])}"
                if event.get("chapter_idx") is not None else "")
            parts.append(f"\n> {event.get('beat_summary', '')}\n>\n> — {where}{_scene_cite(event)}\n")
    return _relativize("".join(parts), depth=2)


def _scene_cite(event: dict[str, Any]) -> str:
    """[34] The paragraphs a scene summary rests on (OPEN_GAPS G7: 63% of the Anne build's
    assertions were scene summaries that named only a chapter), as its own `_Sources:` paragraph
    the way a prose section cites. Empty for an older events.db."""
    sources = _source_links(event.get("evidence"), 2, limit=4)
    return f"\n\n_Sources: {sources}_" if sources else ""


def _render_relationship(
    doc: dict[str, Any], vol: int, entities_by_id: dict[str, dict[str, Any]],
    settings: Any = None, written_ids: set[str] | None = None,
) -> str | None:
    """[30] Two things this page threw away, both already in its own bundle.

    It named both characters in **plain text** while their character pages linked back here --
    exactly the one-way edge `_entity_link`'s docstring describes for the codex rosters, still
    live on all 18 relationship pages. And it rendered `display` alone, dropping each relation's
    `qualifier` and `since_vol`: "Holo - Comrade of - Kraft Lawrence", where the row itself said
    "traveling companion" since v1. The qualifier is the part that says what the relationship
    actually IS, and character pages have rendered it (as `blurb`) since Phase 22.
    `settings=None` keeps the old plain-text names for a caller that cannot supply routes."""
    snapshot = resolve_cutoff(doc["cutoffs"], vol)
    # 2026-09-25: a pair whose every relation is withheld (verification, denial) has nothing to say
    # about the relationship, and no character page lists it, so nothing could link here. Nine
    # such pages shipped -- one a bare heading. Not written, rather than written as a dead end.
    if snapshot is None or not (snapshot.get("relations") or snapshot.get("history")):
        return None
    a, b = doc["pair"]
    # [33] No pair page naming someone a reader has not met by `vol` (see `emitted_here`).
    if any(int(entities_by_id.get(x, {}).get("first_vol") or 1) > vol for x in (a, b)):
        return None

    def name(entity_id: str) -> str:
        entity = entities_by_id.get(entity_id, {})
        # [31] A character with no page at this cutoff is named, not linked (Anne: the pair
        # Leslie Moore & West exists from v4, West's page only from v5).
        no_page = written_ids is not None and entity.get("type") == "CHARACTER" and entity_id not in written_ids
        if settings is None or no_page:
            return entity.get("canonical", entity_id)
        return _entity_link(entity_id, entities_by_id, settings, depth=2)

    def detail(row: dict[str, Any]) -> str:
        bits = [str(row[k]) for k in ("qualifier",) if row.get(k)]
        if row.get("since_vol"):
            bits.append(f"since v{row['since_vol']}")
        return f" — {' · '.join(bits)}" if bits else ""

    name_a = entities_by_id.get(a, {}).get("canonical", a)
    name_b = entities_by_id.get(b, {}).get("canonical", b)
    parts = [f"# {name_a} & {name_b}\n"]
    for row in snapshot.get("relations") or []:
        line = f"\n- {name(row['subject'])} — {row['display']} — {name(row['object'])}{detail(row)}"
        line += _evidence_suffix(row.get("evidence"), link_depth=2, limit=3)
        parts.append(line)
    for row in snapshot.get("history") or []:
        line = (
            f"\n- _previously:_ {name(row['subject'])} — {row['display']} — "
            f"{name(row['object'])} (until v{row.get('until_vol', '?')})"
        )
        line += _evidence_suffix(row.get("evidence"), link_depth=2, limit=3)
        parts.append(line)
    scenes = snapshot.get("shared_scenes") or []
    if scenes:
        parts.append("\n\n## Shared scenes\n")
        for event in scenes:
            where = f"v{event['vol']}" + (
                f" · {chapter_label(event['vol'], event['chapter_idx'])}"
                if event.get("chapter_idx") is not None else "")
            parts.append(f"\n> {event.get('beat_summary', '')}\n>\n> — {where}{_scene_cite(event)}\n")
    return _relativize("".join(parts), depth=2)


def _render_timeline(doc: dict[str, Any], entities_by_id: dict[str, dict[str, Any]],
                     written_ids: set[str] | None = None) -> str:
    parts = [f"# Volume {doc['vol']} timeline\n"]
    for chapter in doc.get("chapters") or []:
        # [30] The raw index: v2's "The Road to Washi" rendered as "Chapter 1", and every
        # volume with a prologue was off by one -- the bug `chapter_label` fixed everywhere else.
        parts.append(f"\n## {chapter_label(doc['vol'], chapter['chapter_idx'])}\n")
        for event in chapter.get("events") or []:
            # [30] Linked: a character named only in a participant list had no inbound link from
            # anywhere (Bookworm's Gerda and both barons were reachable from the index alone).
            names = ", ".join(
                f"[{e['canonical']}](/character/{p})" if (e := entities_by_id.get(p, {})).get("type") == "CHARACTER"
                and (written_ids is None or p in written_ids)
                else e.get("canonical", p)
                for p in event.get("participants", []))
            beat = f"{event.get('beat_summary', '')}{_scene_cite(event)}"
            parts.append(f"\n**{names}** — {beat}\n" if names else f"\n{beat}\n")
    return _relativize("".join(parts), depth=2)


# Phase 26: a Markdown block construct only triggers at the START of a line, so a paragraph of
# prose is safe unless its own first character happens to be one of these. Escaping just that
# character keeps the emitted source text a faithful rendering of what was indexed.
_MD_BLOCK_STARTERS = tuple("#-*+>|=~")


def _escape_block_start(text: str) -> str:
    return "\\" + text if text[:1] in _MD_BLOCK_STARTERS else text


def _render_source_chapter(vol: int, chapter_idx: int, records: list[dict[str, Any]]) -> str:
    """One chapter of the source view: every paragraph rendered verbatim, with its `para_id`
    stamped as an HTML id through the `attr_list` extension this emitter already enables.

    This is what a citation link on a character page resolves to. The text is the normalized
    text from `data/<series>/01_parsed/vNN.jsonl` -- exactly what the extractor was shown --
    which is the property that makes an anchor here usable as evidence rather than as an
    approximate pointer.
    """
    parts = [f"# Volume {vol} — {chapter_label(vol, chapter_idx)}\n"]
    parts.append(
        '\n!!! note "Source text"\n'
        "    Passages cited on the wiki pages link into this page. Only volumes at or before\n"
        "    this page's cutoff are present.\n"
    )
    last_page: int | None = None
    for record in records:
        text = _escape_block_start(" ".join((record.get("text") or "").split()))
        if not text:
            continue
        if record.get("scene_break_before") not in (None, "", "none"):
            parts.append("\n---\n")
        print_page = record.get("print_page")
        if print_page is not None and print_page != last_page:
            parts.append(f"\n**p. {print_page}**\n")
            last_page = print_page
        parts.append(f"\n{text}\n{{: #{source_anchor(record['para_id'])} }}\n")
    return "".join(parts)


def _render_source_index(
    vol: int, chapters_by_volume: dict[int, dict[int, list[dict[str, Any]]]]
) -> str:
    parts = [f"# Source text — up to Volume {vol}\n"]
    for source_vol in range(1, vol + 1):
        chapters = chapters_by_volume.get(source_vol)
        if not chapters:
            continue
        parts.append(f"\n## Volume {source_vol}\n")
        for chapter_idx, records in sorted(chapters.items()):
            parts.append(
                f"\n- [{chapter_label(source_vol, chapter_idx)}]"
                f"(v{source_vol:02d}-c{chapter_idx:02d}.md)"
            )
        parts.append("\n")
    return "".join(parts)


def load_parsed_chapters(upto: int) -> dict[int, dict[int, list[dict[str, Any]]]]:
    """`{vol: {chapter_idx: [paragraph record, ...]}}` for volumes 1..upto, read once per run.

    The only place this module reads `data/` rather than a bundle `site/bundle.py` already
    assembled -- the source view is the one page kind whose content is the corpus itself.
    """
    by_volume: dict[int, dict[int, list[dict[str, Any]]]] = {}
    for vol in range(1, upto + 1):
        path = paths.parsed_volume(vol)
        if not path.exists():
            continue
        chapters: dict[int, list[dict[str, Any]]] = defaultdict(list)
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    chapters[record["chapter_idx"]].append(record)
        by_volume[vol] = dict(chapters)
    return by_volume


def _write_source_pages(
    vol: int, chapters_by_volume: dict[int, dict[int, list[dict[str, Any]]]]
) -> None:
    """Write `vNN/source/` for ONE cutoff.

    CLAUDE.md §1: the `range(1, vol + 1)` bound is the spoiler gate. A cutoff directory may
    only ever contain source text from volumes at or before its own cutoff. This is the one
    place in the wiki tree that emits raw corpus text, so that bound is load-bearing and is
    asserted directly by `tests/test_spoiler_leak.py`.
    """
    source_dir = paths.wiki_cutoff_dir(vol) / "source"
    for source_vol in range(1, vol + 1):
        for chapter_idx, records in sorted(chapters_by_volume.get(source_vol, {}).items()):
            _write(
                source_dir / f"v{source_vol:02d}-c{chapter_idx:02d}.md",
                _render_source_chapter(source_vol, chapter_idx, records),
            )
    _write(source_dir / "index.md", _render_source_index(vol, chapters_by_volume))


def _render_root_index(index_doc: dict[str, Any], upto: int) -> str:
    """[30] The product's front door. It was a title and one sentence — "Pick the volume you've
    read up to from the navigation" — carrying no link at all, so the one page every reader
    arrives on was the only dead end in the wiki, and the property that makes this wiki
    different from any other reference went unexplained. Counts and links come from `index_doc`,
    which is already cutoff-aware; nothing here is generated."""
    lines = [
        f"# {index_doc['series']['title']}\n",
        "\nEvery page here is written for a reader who has finished a particular volume, and "
        "shows only what that reader could already know. Nothing on a Volume 1 page rests on "
        "evidence from a later one.\n",
        "\nPick how far you have read:\n",
    ]
    for vol in range(1, upto + 1):
        n = sum(1 for c in index_doc["characters"] if c["first_vol"] <= vol)
        lines.append(
            f"\n- [Up to Volume {vol}](v{vol:02d}/index.md)"
            f" — {n} {'character' if n == 1 else 'characters'}"
        )
    return "".join(lines) + "\n"


def _render_volume_index(
    index_doc: dict[str, Any],
    vol: int,
    settings: Any,
    codex_kinds: list[str] | None = None,
    leads: dict[str, str] | None = None,
) -> str:
    parts = [f"# {index_doc['series']['title']} — up to Volume {vol}\n"]
    parts.append("\n## Characters\n")
    for c in index_doc["characters"]:
        if c["first_vol"] > vol:
            continue
        char_href = href(f"/character/{c['entity_id']}", depth=1)
        # [30] This list was fourteen bare names. Each page already carries a deterministic
        # one-line `lead` (status, primary affiliation) assembled from graph rows, so the index
        # can say who these people are without one generated word or a new evidence read. A
        # character whose lead is empty renders as a bare name, which is the honest result
        # rather than a filler phrase.
        # The stored lead carries absolute routes (`/codex/factions#...`) that each renderer
        # relativizes to its own depth; the index sits one level down, not two like a character
        # page. `tests/test_wiki_emit.py` caught this as an escaped link, not a broken one.
        lead = (leads or {}).get(c["entity_id"])
        lead = _relativize(lead, depth=1) if lead else None
        parts.append(f"\n- [{c['canonical']}]({char_href})" + (f" — {lead}" if lead else ""))
    codex_cfg = settings.extraction.get("codex_pages", {})
    # [29] `codex_kinds is None` keeps the pre-Phase-29 behaviour (every configured kind listed)
    # for any caller that does not know which kinds this cutoff actually filled.
    visible = sorted(codex_cfg) if codex_kinds is None else sorted(set(codex_kinds) & set(codex_cfg))
    if visible:
        parts.append("\n\n## Codex\n")
        for kind in visible:
            parts.append(f"\n- [{codex_cfg[kind]['title']}]({href(f'/codex/{kind}', depth=1)})")
    # Phase 26: source/ and timeline/ are both written into this cutoff dir but were
    # unreachable from it -- this index was the only navigation the tree had, and
    # `_write_mkdocs_yml` emitted no `nav:` at all until this phase.
    parts.append("\n\n## Reference\n")
    parts.append("\n- [Source text](source/index.md)")
    parts.append(f"\n- [Timeline](timeline/v{vol:02d}.md)")
    return "".join(parts)


# ---------------------------------------------------------------------------
# mkdocs.yml
# ---------------------------------------------------------------------------

_MKDOCS_YML = """\
site_name: {site_name}
docs_dir: {docs_dir}
site_dir: {site_dir}
theme:
  name: material
  # [30] One top tab per cutoff ("Up to Volume N"): the reader picks how far they have read
  # before seeing anything, which is the site's whole contract.
  features: [navigation.tabs, navigation.top, toc.follow]
  palette:
    - scheme: default
      toggle: {{icon: material/brightness-7, name: Dark mode}}
    - scheme: slate
      toggle: {{icon: material/brightness-4, name: Light mode}}
# [30] No search. MkDocs' default index spans every cutoff, so a Volume 1 reader searching a name
# was shown snippets quoted from Volume 2 pages without clicking into them -- a passive spoiler,
# the one thing this site must never do. Navigation stays per volume.
plugins: []
markdown_extensions:
  - attr_list
  - admonition
strict: true
{nav}"""


def _yaml_title(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_nav(
    upto: int,
    index_doc: dict[str, Any],
    codex_titles: dict[str, str],
    codex_kinds_at: dict[int, list[str]] | None = None,
) -> str:
    """A curated `nav:` block, one section per cutoff volume.

    Phase 26. `_write_mkdocs_yml` emitted no `nav:` at all, so MkDocs inferred one from the
    directory tree -- alphabetical, uncurated, and with `wiki/index.md` telling the reader to
    "pick the volume you've read up to from the navigation" that did not exist. That was
    survivable while the tree was small; the source view adds 28 pages per cutoff, which would
    bury everything else. Relationship and timeline pages are deliberately NOT enumerated here
    (21 pair pages would swamp the sidebar). Pair pages are reached from each party's
    Relationships heading (okf `pair_pages`, 2026-09-25; before that, from nowhere)."""
    lines = ["nav:", "  - Home: index.md"]
    for vol in range(1, upto + 1):
        lines.append(f"  - Up to Volume {vol}:")
        lines.append(f"      - Overview: v{vol:02d}/index.md")
        # [33] No character names in the nav. MkDocs Material writes the WHOLE nav into every
        # page's HTML (sidebar, mobile drawer), so a Volume 1 page carried every later cutoff's
        # cast ("Enek", "Hans Remelio"; for Anne, her future children). Characters are linked
        # from each cutoff's Overview. ponytail: a per-cutoff site would restore a per-volume
        # character sidebar; do that if the sidebar is missed.
        # [29] Only the kinds this cutoff actually filled -- an empty `tech.md` is not written,
        # so navigating to it would be a `strict: true` build failure, not just a bare heading.
        kinds = sorted(codex_titles) if codex_kinds_at is None else sorted(
            set(codex_kinds_at.get(vol, [])) & set(codex_titles)
        )
        if kinds:
            lines.append("      - Codex:")
            for kind in kinds:
                lines.append(f"          - {_yaml_title(codex_titles[kind])}: v{vol:02d}/codex/{kind}.md")
        lines.append(f"      - Timeline: v{vol:02d}/timeline/v{vol:02d}.md")
        lines.append(f"      - Source text: v{vol:02d}/source/index.md")
    return "\n".join(lines) + "\n"


def _write_mkdocs_yml(
    settings: Any,
    *,
    upto: int,
    index_doc: dict[str, Any],
    codex_titles: dict[str, str],
    codex_kinds_at: dict[int, list[str]] | None = None,
) -> None:
    # mkdocs refuses `docs_dir` to be its own config file's parent directory (tried, see
    # docs/vision/PHASE_25.md's 2026-09-17 entry) -- mkdocs.yml lives at DIST_DIR, a sibling of
    # SITE_DIR (docs_dir: wiki), not inside SITE_DIR itself.
    _write(
        paths.wiki_mkdocs_yml(),
        _MKDOCS_YML.format(
            site_name=settings.series_title,
            docs_dir=paths.SITE_DIR.name,
            site_dir=paths.wiki_html_dir().name,
            nav=_render_nav(upto, index_doc, codex_titles, codex_kinds_at),
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_wiki(
    settings: Any,
    *,
    pages_bundle: dict[str, Any],
    codex_bundles: dict[str, dict[str, Any]],
    relationship_bundles: dict[str, Any],
    timeline_bundles: dict[int, Any],
    index_doc: dict[str, Any],
    entities_by_id: dict[str, dict[str, Any]],
    upto: int,
) -> None:
    """Writes `dist/<series>/wiki/v{NN}/` for every `NN` in `1..upto`, plus `mkdocs.yml`.

    Called once per `wiki site build --upto N` run, after every other bundle write -- this is
    the module `cli.py::site_build` calls in place of the deleted `site/static_site.py::
    build_site`. Deterministic: no LLM call, no new read of anything `site/bundle.py` did not
    already assemble this run.
    """
    # [31] A character is listed (home counts, volume index, nav) from its first PAGE, not its
    # first mention: Anne's Irene Howard is mentioned in v4 but has claims only from v6, so the
    # v04 index linked a page nobody wrote and the `strict: true` build failed. max() keeps the
    # listing no earlier than first_vol; a character with no page bundle is not listed at all.
    first_page = {eid: min(int(k) for k in doc["cutoffs"]) for eid, doc in pages_bundle.items() if doc.get("cutoffs")}
    index_doc = {**index_doc, "characters": [
        {**c, "first_vol": max(c["first_vol"], first_page[c["entity_id"]])}
        for c in index_doc.get("characters", []) if c["entity_id"] in first_page
    ]}

    codex_titles = {
        kind: cfg["title"] for kind, cfg in (settings.extraction.get("codex_pages") or {}).items()
    }

    # [26] Read the parsed corpus once, not once per cutoff; `_write_source_pages` slices it
    # per cutoff. Empty when the ingest stage has not run, which degrades to no source pages
    # (and therefore plain-text citations) rather than failing the build.
    chapters_by_volume = load_parsed_chapters(upto)
    # [30] Must run before anything renders a citation or a source page: `citation_label` reads
    # this, and a stale/empty map silently falls back to `chapter_idx + 1`, which is the bug.
    set_chapter_labels(chapters_by_volume)

    # [29] Which codex kinds have anything to say at each cutoff -- the index, the nav and the
    # write loop below all have to agree, or the tree grows a link to a page nobody wrote.
    codex_kinds_at = {vol: _codex_kinds_at(codex_bundles, vol) for vol in range(1, upto + 1)}

    # [33] A cutoff dir above `upto` is an older, wider build's (Anne's v06 from --upto 6): MkDocs
    # would still publish it, and nothing in this run regenerates or links it.
    for old in paths.SITE_DIR.glob("v[0-9][0-9]"):
        if old.is_dir() and int(old.name[1:]) > upto:
            shutil.rmtree(old, ignore_errors=True)

    for vol in range(1, upto + 1):
        cutoff_dir = paths.wiki_cutoff_dir(vol)

        # [26C] Clear the cutoff dir before rewriting it. Every file under it is regenerated from
        # this run's bundles, so anything left behind is a page an EARLIER graph supported and
        # this one does not -- the shipped v1-2 wiki carried six of them, including a relationship
        # page asserting "Holo -- Killed -- Saint Ruvinheigen" from a build three days older than
        # its own graph. `wiki audit links` cannot catch this: a stale page's links resolve fine,
        # it is the page's existence that is wrong.
        shutil.rmtree(cutoff_dir, ignore_errors=True)

        _write(
            cutoff_dir / "index.md",
            _render_volume_index(
                index_doc,
                vol,
                settings,
                codex_kinds=codex_kinds_at[vol],
                leads={
                    eid: snap["lead"]
                    for eid, doc in pages_bundle.items()
                    if (snap := resolve_cutoff(doc["cutoffs"], vol)) and snap.get("lead")
                },
            ),
        )

        # [26] Source view — the target every citation link on this cutoff's pages resolves to.
        if chapters_by_volume:
            _write_source_pages(vol, chapters_by_volume)

        # [26] Exactly the character pages this cutoff dir will contain -- the only CHARACTER
        # link targets that resolve here. `okf.py::_label` names the rest instead of linking
        # them (a character whose first page is v02 is a 404 on every v01 page).
        # [33] ...and never a page for an entity none of whose names a reader has met by `vol`
        # (gazetteer `first_vol`, the rule the volume index already applied): Anne's v3 "Will
        # Leslie" line was resolved to Leslie Moore (every surface form first seen in v4), and the
        # page's title and slug disclosed her name at t=3.
        emitted_here = {
            eid for eid, doc in pages_bundle.items()
            if resolve_cutoff(doc["cutoffs"], vol) is not None
            and int(entities_by_id.get(eid, {}).get("first_vol") or 1) <= vol
        }

        # Rendered before the character pages, which link to exactly these (okf `pair_pages`).
        relationship_pages = {
            pair_key: rendered
            for pair_key, doc in relationship_bundles.items()
            if (rendered := _render_relationship(doc, vol, entities_by_id, settings, emitted_here)) is not None
        }

        for entity_id, doc in pages_bundle.items():
            snapshot = resolve_cutoff(doc["cutoffs"], vol)
            if snapshot is None or entity_id not in emitted_here:
                continue
            entity = entities_by_id.get(entity_id, {"type": "CHARACTER"})

            md = render_markdown(snapshot, entity, settings, entities_by_id,
                                 link_depth=2,
                                 written_ids=emitted_here, pair_pages=set(relationship_pages))
            _write(cutoff_dir / "character" / f"{entity_id}.md", _relativize(md, depth=2))

        for kind in codex_kinds_at[vol]:
            title = codex_titles.get(kind, kind.title())
            _write(
                cutoff_dir / "codex" / f"{kind}.md",
                _render_codex_kind(kind, title, codex_bundles[kind], vol, settings, entities_by_id, emitted_here),
            )

        for pair_key, rendered in relationship_pages.items():
            _write(cutoff_dir / "relationships" / f"{pair_key}.md", rendered)

        for tvol, doc in timeline_bundles.items():
            if tvol <= vol:
                _write(cutoff_dir / "timeline" / f"v{tvol:02d}.md", _render_timeline(doc, entities_by_id, emitted_here))

    _write(paths.SITE_DIR / "index.md", _render_root_index(index_doc, upto))
    _write_mkdocs_yml(
        settings,
        upto=upto,
        index_doc=index_doc,
        codex_titles=codex_titles,
        codex_kinds_at=codex_kinds_at,
    )
