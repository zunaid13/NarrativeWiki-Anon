"""[1] EPUB -> raw chapters and paragraphs. Absorbs every corpus trap in CLAUDE.md section 3.

Inputs:     A `config.Volume` (path to one EPUB) and the series config dict (`Settings.series`).
Outputs:    `RawVolume` — title/isbn/pub_date plus an ordered list of `RawChapter`, each holding
            ordered `RawParagraph` records. Nothing here writes to disk; `ingest/segment.py`
            turns this into the CONTRACTS section 1 JSONL records.
Invariants: - Chapters are discovered by walking the OPF spine in order and starting a new one on
              ANY tag matching `epub.heading_tags` (default `["h1"]`) — deliberately broader than
              `epub.chapter_start_selector` (default h1.chapter-number), which only extracts the
              number/title TEXT once a heading is already found; matching just the selector would
              miss differently-classed front/back-matter headings (h1.preface-title, ...) and
              silently fold them into whatever chapter happened to be open (CLAUDE.md section 3
              trap 2). A spine document with no heading-tag match is a continuation of the current
              chapter — filename order is never used. `heading_tags` exists because 86 and
              Spice and Wolf both use `<h1>` for every section boundary; an EPUB from a different
              source (Gutenberg, a different publisher toolchain) using `<h2>` or another tag
              would otherwise collapse every chapter into one with no error
              (docs/vision/plans/0008-pre-full-scale-audit.md S2) — set `heading_tags: ["h2"]` (or
              whichever tag that source actually uses) in that series' config, not here.
            - `img[src*="mdash"]` (and other `normalize.image_text_substitutions` rules) are
              replaced with their literal text ON THE LIVE TAG TREE before `.get_text()` runs
              anywhere. Stripping images first silently eats mid-sentence dashes.
            - copyright.xhtml is dropped by filename (`epub.skip_files`), never by epub:type —
              it is mistagged "bodymatter chapter" in every volume.
            - EPUB navigation documents are excluded from the spine walk by their manifest
              type, even when renamed or marked linear; their headings are not story chapters.
Contract:   docs/CONTRACTS.md section 1. config/series.86.yaml `epub`, `speech`, `scene_breaks`,
            `normalize` sections carry every tunable this module reads.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import ebooklib
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub as ebooklib_epub

from .normalize import (
    count_words,
    image_rules_from_config,
    normalize_paragraph_text,
    substitute_and_strip_images,
)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

DEFAULT_OPEN_QUOTES = ["“", "\"", "‘", "«", "„", "「", "『"]
VALID_CHAPTER_KINDS = {"prologue", "chapter", "interlude", "epilogue", "afterword", "epigraph"}


@dataclass
class RawParagraph:
    text: str
    speech: str
    is_monologue: bool
    scene_break_before: str
    print_page: int | None


@dataclass
class RawChapter:
    chapter_idx: int
    chapter_id: str
    chapter_title: str | None
    chapter_kind: str
    paragraphs: list[RawParagraph] = field(default_factory=list)
    # Every heading that opened this chapter, joined. The title keeps only the CHAPTER heading
    # onward, so a Book/Part heading sharing the segment ("BOOK II. OLD AND YOUNG." before
    # "CHAPTER XIII.") lives only here; `source.volume_breaks` matches it. Not serialised.
    headings: str = ""


@dataclass
class RawVolume:
    vol: int
    title: str
    isbn: str | None
    pub_date: str | None
    source_file: str
    chapters: list[RawChapter] = field(default_factory=list)


class _ParseState:
    """Threads print_page and pending scene-break across documents in one volume."""

    def __init__(self) -> None:
        self.print_page: int | None = None
        self.pending_break: str | None = None  # "hard" | None; soft is per-paragraph


def parse_epub(path: Path, vol: int, series_cfg: dict[str, Any]) -> RawVolume:
    book = ebooklib_epub.read_epub(str(path), {"ignore_ncx": True})
    epub_cfg = series_cfg.get("epub", {}) or {}
    speech_cfg = series_cfg.get("speech", {}) or {}
    scene_cfg = series_cfg.get("scene_breaks", {}) or {}
    image_rules, strip_remaining = image_rules_from_config(series_cfg)

    skip_files = set(epub_cfg.get("skip_files", []))
    keep_types = set(epub_cfg.get("keep_epub_types", []))
    chapter_start_selector = epub_cfg.get("chapter_start_selector", "h1.chapter-number")
    chapter_title_selector = epub_cfg.get("chapter_title_selector", "h1.chapter-title")
    heading_tags = epub_cfg.get("heading_tags") or _detect_heading_tags(book, skip_files)
    # [30] The input adapter: a series declares which of ITS selectors mean "a chapter starts
    # here" and "hard scene break here", so a new publisher's markup is a config entry, not a code
    # change. Overlord needs both: its numbered sections are also <h1> (h1.sect1), and v02's
    # chapter012.xhtml opens with "5" -- section 5 of Chapter 4 in its own file -- which the
    # any-heading rule would have split into a new chapter. Both optional; unset keeps the
    # `heading_tags` / `hard_image_class` behaviour every earlier series was verified against.
    chapter_heading_selector = ", ".join(epub_cfg.get("chapter_heading_selectors") or [])
    hard_break_selector = ", ".join(scene_cfg.get("hard_break_selectors") or [])
    image_page_max_words = int(epub_cfg.get("image_page_max_words", 10))

    para_raid_pattern = re.compile(speech_cfg.get("para_raid_class_pattern", r"^(san|special.*)$"))
    machine_container_pattern = re.compile(
        speech_cfg.get("machine_voice_container_pattern", r"^ecom_")
    )
    machine_text_pattern = re.compile(speech_cfg.get("machine_voice_text_pattern", r"^<{1,2}.*>{1,2}$"))
    monologue_wrapper = speech_cfg.get("monologue_wrapper", "em")
    # [30] Every opening quote a novel may use when the series sets none: British ‘single’,
    # straight ", French «, German „, Japanese 「『. With “ alone their dialogue read as narration.
    dialogue_open_quotes = tuple(speech_cfg.get("dialogue_open_quotes") or DEFAULT_OPEN_QUOTES)

    hard_image_class = scene_cfg.get("hard_image_class", "ornament")
    soft_classes = set(scene_cfg.get("soft_paragraph_classes", []))

    title, isbn, pub_date = _read_metadata(book)

    state = _ParseState()
    chapters: list[RawChapter] = []
    current: RawChapter | None = None

    split_tags = epub_cfg.get("split_within_documents") or []
    for item, soup in _documents(book, skip_files, split_tags):
        container = soup.find("section") or soup.find("body")
        if container is None:
            continue

        epub_type_tokens = set((container.get("epub:type") or "").split())
        if keep_types and epub_type_tokens and not (epub_type_tokens & keep_types):
            continue

        # Any tag matching heading_tags marks a new semantic section (chapter, interlude,
        # prologue, epigraph, epilogue, afterword — each uses a different heading class).
        # Continuation documents like chapter010a/chapter010b carry no heading tag at all, which
        # is what distinguishes them; matching only chapter_start_selector would miss the
        # front/back-matter sections that use a single differently-classed heading
        # (h1.preface-title, h1.appendix-title, ...) and silently fold them into whatever chapter
        # happened to be open, per CLAUDE.md section 3 trap 2. See module docstring for why this
        # is a separate, broader config knob (`epub.heading_tags`) from `chapter_start_selector`.
        all_headings = (soup.select(chapter_heading_selector) if chapter_heading_selector
                        else soup.find_all(heading_tags))
        has_heading = bool(all_headings)
        paragraphs, doc_word_count, has_image = _extract_paragraphs(
            container,
            state,
            image_rules=image_rules,
            strip_remaining=strip_remaining,
            para_raid_pattern=para_raid_pattern,
            machine_container_pattern=machine_container_pattern,
            machine_text_pattern=machine_text_pattern,
            monologue_wrapper=monologue_wrapper,
            dialogue_open_quotes=dialogue_open_quotes,
            hard_image_class=hard_image_class,
            soft_classes=soft_classes,
            hard_break_selector=hard_break_selector,
        )

        if not has_heading and has_image and doc_word_count <= image_page_max_words:
            continue  # an art plate: no prose to keep

        if has_heading:
            if current is not None:
                chapters.append(current)
            number_node = soup.select_one(chapter_start_selector)
            number_text = _text_of(number_node)
            title_node = soup.select_one(chapter_title_selector)
            if title_node is None:
                # Front/back matter has one heading tag, not the number+title pair regular
                # chapters have.
                title_node = next((h for h in all_headings if h is not number_node), None)
            title_text = _text_of(title_node)
            headings_text = " ".join(t for t in (_text_of(h) for h in all_headings) if t)
            if split_tags and len(all_headings) > 1:
                # [31] A split segment opens with consecutive headings: "CHAPTER 10" + "LESLIE
                # MOORE", or a byline before "CHAPTER I". Title = the last CHAPTER heading onward.
                texts = [t for t in (_text_of(h) for h in all_headings) if t]
                starts = [i for i, t in enumerate(texts) if t.upper().startswith("CHAPTER")]
                title_text = " ".join(texts[starts[-1] if starts else 0:]) or title_text
            has_epigraph_div = container.find("div", class_="epigraph") is not None
            # [30] No number heading (J-Novel Club): the title says "Prologue"/"Epilogue".
            kind = _classify_kind(number_text or title_text, epub_type_tokens, has_epigraph_div)
            current = RawChapter(
                chapter_idx=len(chapters),
                chapter_id=container.get("id") or item.get_id(),
                chapter_title=title_text,
                chapter_kind=kind,
                headings=headings_text,
            )
        elif current is None:
            # No chapter open yet and this doc has real prose with no heading of its own
            # (not seen in the reference corpus, but do not silently drop it).
            has_epigraph_div = container.find("div", class_="epigraph") is not None
            kind = _classify_kind(None, epub_type_tokens, has_epigraph_div)
            current = RawChapter(
                chapter_idx=len(chapters),
                chapter_id=container.get("id") or item.get_id(),
                chapter_title=None,
                chapter_kind=kind,
            )

        current.paragraphs.extend(paragraphs)

    if current is not None:
        chapters.append(current)

    return select_reading_volume(
        RawVolume(
            vol=vol,
            title=title,
            isbn=isbn,
            pub_date=pub_date,
            source_file=path.name,
            chapters=chapters,
        ),
        series_cfg,
    )


# [34] Back matter that is not the story, dropped for every series like `copyright.xhtml`: Oz
# volume 3 ended with "Books by L. Frank Baum", blurbs of books 4-7 inside a cutoff-3 text
# (OPEN_GAPS G12), and a transcriber's note is the edition's, not the author's.
BACK_MATTER_TITLES = [re.compile(r"^(?:Other )?Books by\b", re.I), re.compile(r"^Transcriber[’']s Notes?\b", re.I)]


def select_reading_volume(rv: RawVolume, series_cfg: dict[str, Any]) -> RawVolume:
    """[31] Cut one standalone book into reading volumes, and drop non-story chapters by title.

    `source.volume_breaks` is a list of regexes on chapter titles; each match starts the next
    volume (Twenty Thousand Leagues: `["^PART TWO"]` makes Part Two volume 2). `rv.vol` picks
    which slice to return. `epub.drop_chapter_titles` removes chapters whose title matches, such
    as the Gutenberg front matter. Chapter indices are renumbered from 0 within the slice, so
    `para_id`s look exactly like those of a multi-EPUB series. `BACK_MATTER_TITLES` are dropped
    for every series.
    """
    breaks = [re.compile(b) for b in (series_cfg.get("source", {}) or {}).get("volume_breaks") or []]
    drops = [re.compile(d) for d in (series_cfg.get("epub", {}) or {}).get("drop_chapter_titles") or []]
    drops += BACK_MATTER_TITLES
    kept: list[RawChapter] = []
    idx_map: dict[int, int] = {}
    volume = 1
    for chapter in rv.chapters:
        title = chapter.chapter_title or ""
        if any(b.search(chapter.headings or title) for b in breaks):
            volume += 1
        # With no breaks this file IS the volume (one EPUB per book); only drops apply.
        if breaks and volume != rv.vol:
            continue
        if any(d.search(title) for d in drops):
            # [31] A Gutenberg book whose chapters were not split parsed as ONE chapter under
            # the front-matter title, and this rule deleted the whole novel without a word.
            if len(chapter.paragraphs) > 50:
                raise ValueError(
                    f"`epub.drop_chapter_titles` matched {title!r} in {rv.source_file}, which holds "
                    f"{len(chapter.paragraphs)} paragraphs -- a story, not front matter. The chapters "
                    f"were probably not split: see `epub.split_within_documents`."
                )
            continue
        idx_map[chapter.chapter_idx] = len(kept)
        chapter.chapter_idx = len(kept)
        kept.append(chapter)
    if breaks and volume < rv.vol:
        raise ValueError(f"volume {rv.vol} requested but `source.volume_breaks` matched only "
                         f"{volume - 1} chapter title(s) in {rv.source_file}")
    rv.chapters = kept
    return rv


def _documents(book: ebooklib_epub.EpubBook, skip_files: set[str], split_tags: list[str]) -> Iterator[tuple[Any, BeautifulSoup]]:
    """Spine documents as parsed soups. [31] With `epub.split_within_documents` (heading tags,
    e.g. ["h3"]), a document holding several chapters -- Gutenberg's newer EPUBs put ~12 in one
    file -- is cut at each such heading and yielded one segment at a time, so the loop above sees
    one chapter per "document" exactly as it does for one-chapter-per-file books. Consecutive
    headings with no paragraph between them ("CHAPTER 10" then "LESLIE MOORE") stay in one segment.
    Unset: one soup per document, byte-for-byte the old behaviour."""
    opener = re.compile(r"<(?:%s)\b" % "|".join(map(re.escape, split_tags)), re.I) if split_tags else None
    for item in _spine_documents(book):
        if Path(item.file_name).name in skip_files:
            continue
        html = item.get_content().decode("utf-8", "replace")
        body = re.search(r"<body\b[^>]*>(.*)</body>", html, re.S | re.I)
        starts = [m.start() for m in opener.finditer(body.group(1))] if (opener and body) else []
        if not starts:
            yield item, BeautifulSoup(html, "lxml")
            continue
        inner = body.group(1)
        cuts = [0]
        for s in starts:  # a heading opens a new segment only if prose came since the last cut
            if re.search(r"<p\b", inner[cuts[-1]:s], re.I) or cuts == [0]:
                cuts.append(s)
        cuts.append(len(inner))
        for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
            piece = inner[a:b]
            if not piece.strip():
                continue
            yield item, BeautifulSoup(f'<html><body id="{item.get_id()}-{k}">{piece}</body></html>', "lxml")


def _detect_heading_tags(book: ebooklib_epub.EpubBook, skip_files: set[str]) -> list[str]:
    """[30] The highest-rank heading tag used at least twice across the spine, for a series that
    does not set `epub.heading_tags`. Yen Press marks every section `<h1>`; Project Gutenberg has
    one `<h1>` (the book title) and an `<h2>` per chapter, and the fixed `["h1"]` default read
    The Hound of the Baskervilles' 15 chapters as 2, with no error. A book whose top tag marks
    parts rather than chapters still needs `heading_tags` set by hand; `wiki audit ingest` warns
    on a volume that collapsed into too few chapters."""
    counts = dict.fromkeys(("h1", "h2", "h3"), 0)
    for item in _spine_documents(book):
        if Path(item.file_name).name in skip_files:
            continue
        raw = item.get_content().decode("utf-8", "ignore").lower()
        for tag in counts:
            counts[tag] += len(re.findall(rf"<{tag}[\s>]", raw))
    return [next((tag for tag, n in counts.items() if n >= 2), "h1")]


# ---------------------------------------------------------------------------
# Metadata and spine
# ---------------------------------------------------------------------------


def _read_metadata(book: ebooklib_epub.EpubBook) -> tuple[str, str | None, str | None]:
    titles = book.get_metadata("DC", "title")
    identifiers = book.get_metadata("DC", "identifier")
    dates = book.get_metadata("DC", "date")
    title = titles[0][0] if titles else "Untitled"
    isbn = identifiers[0][0] if identifiers else None
    pub_date = dates[0][0] if dates else None
    return title, isbn, pub_date


def _spine_documents(book: ebooklib_epub.EpubBook) -> Iterator[Any]:
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if (item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT
                and not isinstance(item, ebooklib_epub.EpubNav)):
            yield item


# ---------------------------------------------------------------------------
# Chapter kind classification
# ---------------------------------------------------------------------------


def _classify_kind(number_text: str | None, epub_type_tokens: set[str], has_epigraph_div: bool) -> str:
    """Heading text is the most reliable signal — it is spelled out ("PROLOGUE", "INTERLUDE",
    "EPILOGUE") far more consistently across 13 volumes than epub:type, which is only sometimes
    that specific. epub:type is the fallback, then structure (an epigraph wrapper div), then
    "chapter" as the default for numbered chapters and the v11 date-titled ones."""
    label = (number_text or "").strip().upper()
    if label.startswith("PROLOGUE"):
        return "prologue"
    if label.startswith("EPILOGUE"):
        return "epilogue"
    if label.startswith("INTERLUDE"):
        return "interlude"
    if "epilogue" in epub_type_tokens:
        return "epilogue"
    if "prologue" in epub_type_tokens:
        return "prologue"
    if "epigraph" in epub_type_tokens:
        return "epigraph"
    if "appendix" in epub_type_tokens:
        return "afterword"
    if "preface" in epub_type_tokens:
        return "epigraph" if has_epigraph_div else "prologue"
    return "chapter"


def _text_of(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    text = normalize_paragraph_text(tag.get_text())
    return text or None


# ---------------------------------------------------------------------------
# Paragraph extraction: one document-order pass over the container
# ---------------------------------------------------------------------------

_PAGE_ANCHOR_RE = re.compile(r"^page-(\d+)$")


def _extract_paragraphs(
    container: Tag,
    state: _ParseState,
    *,
    image_rules: list[dict[str, str]],
    strip_remaining: bool,
    para_raid_pattern: re.Pattern[str],
    machine_container_pattern: re.Pattern[str],
    machine_text_pattern: re.Pattern[str],
    monologue_wrapper: str,
    dialogue_open_quotes: tuple[str, ...],
    hard_image_class: str,
    soft_classes: set[str],
    hard_break_selector: str = "",
) -> tuple[list[RawParagraph], int, bool]:
    paragraphs: list[RawParagraph] = []
    doc_word_count = 0
    has_image = bool(container.find("img"))
    hard_break_nodes = {id(n) for n in container.select(hard_break_selector)} if hard_break_selector else set()

    # Snapshot before mutating: substitute_and_strip_images() rewrites the tree, and
    # BeautifulSoup's `.descendants` generator walks live `next_element` links, so mutating
    # mid-iteration skips or corrupts the remaining walk.
    for node in list(container.descendants):
        if not isinstance(node, Tag):
            continue

        if node.name == "a" and node.get("id"):
            m = _PAGE_ANCHOR_RE.match(node["id"])
            if m:
                state.print_page = int(m.group(1))
            continue

        if id(node) in hard_break_nodes:
            state.pending_break = "hard"
            continue

        if node.name == "img":
            if hard_image_class in (node.get("class") or []):
                state.pending_break = "hard"
            continue

        if node.name != "p":
            continue

        classes = set(node.get("class") or [])
        substitute_and_strip_images(node, image_rules, strip_remaining)
        text = normalize_paragraph_text(node.get_text())
        n_words = count_words(text)
        doc_word_count += n_words
        if n_words == 0:
            continue

        soft = bool(classes & soft_classes)
        scene_break_before = "hard" if state.pending_break == "hard" else ("soft" if soft else "none")
        state.pending_break = None

        speech = _classify_speech(
            node,
            classes,
            text,
            para_raid_pattern=para_raid_pattern,
            machine_container_pattern=machine_container_pattern,
            machine_text_pattern=machine_text_pattern,
            dialogue_open_quotes=dialogue_open_quotes,
        )
        is_monologue = _is_monologue(node, monologue_wrapper)

        paragraphs.append(
            RawParagraph(
                text=text,
                speech=speech,
                is_monologue=is_monologue,
                scene_break_before=scene_break_before,
                print_page=state.print_page,
            )
        )

    return paragraphs, doc_word_count, has_image


def _classify_speech(
    p: Tag,
    classes: set[str],
    text: str,
    *,
    para_raid_pattern: re.Pattern[str],
    machine_container_pattern: re.Pattern[str],
    machine_text_pattern: re.Pattern[str],
    dialogue_open_quotes: tuple[str, ...],
) -> str:
    is_machine_container = any(
        machine_container_pattern.match(c)
        for parent in p.find_parents("div")
        for c in (parent.get("class") or [])
    )
    if is_machine_container or machine_text_pattern.match(text):
        return "machine"

    span_classes = {c for span in p.find_all("span") for c in (span.get("class") or [])}
    if any(para_raid_pattern.match(c) for c in classes | span_classes):
        return "para_raid"

    if text.startswith(dialogue_open_quotes):
        return "dialogue"

    return "narration"


def _is_monologue(p: Tag, wrapper: str) -> bool:
    children = [c for c in p.children if not (isinstance(c, str) and not c.strip())]
    return len(children) == 1 and isinstance(children[0], Tag) and children[0].name == wrapper
