"""[3] Shared surface-text resolution helpers, used identically by `extract/claims.py` (mention-
major) and `extract/scenes.py` (chapter-major, Phase 18).

Inputs:     N/A — pure functions, no I/O.
Outputs:    `normalize_para_id`, `unwrap_quote`, `resolve_surface`, `group_known_entities`.
Invariants: `resolve_surface` never returns an id absent from `candidates` — both callers rely on
            this so a relation object, scene participant, speaker, or epithet referent can never
            be invented, only matched against text already known to be in scope (never the whole
            gazetteer). Split out of `claims.py` so the two extraction drivers share one
            implementation instead of maintaining diverging copies.
Contract:   docs/CONTRACTS.md §3.3 (verbatim quote + closed-vocabulary resolution rules), reused
            identically for scene records (§3b).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_PARA_ID_RE = re.compile(r"^(v\d+:c\d+:p)(\d+)$")
_WORD_RE = re.compile(r"[^\s]+")


def normalize_para_id(para_id: str, valid_ids: tuple[str, ...]) -> str | None:
    """Repair a common model mistake: dropping the paragraph number's leading zero-padding when
    copying a para_id label (observed against real Ollama output: "v01:c03:p96" instead of the
    real "v01:c03:p0096"). Only ever returns an id already in `valid_ids` — this repairs a known
    formatting slip against the caller's own known-real paragraph list, it never invents one."""
    if para_id in valid_ids:
        return para_id
    match = _PARA_ID_RE.match(para_id)
    if not match:
        return None
    prefix, digits = match.groups()
    for width in (4, 3, 5):  # 4 is the real corpus width; a couple neighbors covered defensively
        candidate = f"{prefix}{int(digits):0{width}d}"
        if candidate in valid_ids:
            return candidate
    return None


def unwrap_quote(quote: str) -> str:
    """Strip one matching leading/trailing straight `"` pair the model sometimes wraps around an
    otherwise-verbatim excerpt, on top of the corpus's own curly dialogue quotes (observed
    against real Ollama output: a copied line like Aldrecht chuckled loudly... comes back as
    `"Aldrecht chuckled loudly...` with an extra leading `"` that isn't in the source paragraph
    at all). The corpus text uses curly “ ” for dialogue, never straight quotes, so this can only
    ever strip the model's own added wrapper, never genuine paragraph content."""
    if len(quote) >= 2 and quote[0] == '"' and quote[-1] == '"':
        return quote[1:-1].strip()
    return quote


_QUOTE_MARKS = "\"'“”‘’"


def recover_verbatim(quote: str, paragraph: str) -> str | None:
    """The paragraph's own text for `quote`, or None when the quote is not in it.

    [30] Models drop the corpus's curly dialogue marks when copying a line (`We accept your
    proposal, said the head...` for `“We accept your proposal,” said the head...`); measured on
    the infobox pass, 3 of 4 `quote_not_verbatim` drops were exactly this. Matching ignores
    quotation marks only, and what is returned is the paragraph's substring, so a stored quote is
    still byte-for-byte source text. Any other difference -- a paraphrase, a stitched second
    paragraph, a changed word -- still fails."""
    if quote in paragraph:
        return quote
    kept = [i for i, ch in enumerate(paragraph) if ch not in _QUOTE_MARKS]
    stripped_para = "".join(paragraph[i] for i in kept)
    stripped_quote = "".join(ch for ch in quote if ch not in _QUOTE_MARKS).strip()
    at = stripped_para.find(stripped_quote) if stripped_quote else -1
    if at >= 0:
        return paragraph[kept[at]:kept[at + len(stripped_quote) - 1] + 1]
    return _recover_stitched_speech(stripped_quote, paragraph)


def _recover_stitched_speech(stripped_quote: str, paragraph: str) -> str | None:
    """[31] A model copying interrupted dialogue keeps only the spoken words: `Oh! certainly, by
    making a curve.` for `“Oh! certainly,” I answered, evasively, “by making a curve.”`. 384 of
    1,523 scene quotes on a Gutenberg novel failed this way (MEASUREMENTS §44). Match against the
    paragraph's “…” spans joined by one space; return the paragraph's own text from the first
    matched character to the last, speech tag included, so a stored quote stays source text."""
    if not stripped_quote:
        return None
    chars: list[str] = []
    index: list[int] = []
    for span in _SPEECH_SPAN_RE.finditer(paragraph):
        if chars:
            chars.append(" ")
            index.append(-1)
        g = 1 if span.group(1) is not None else 2
        body_start = span.start(g)
        body = span.group(g).strip()
        offset = span.group(g).find(body) if body else 0
        for k, ch in enumerate(body):
            chars.append(ch)
            index.append(body_start + offset + k)
    # The comma before a tag often comes back as a full stop ("Professor,” replied Ned. “That" ->
    # "Professor. That"), so , . ; : are ignored on both sides. Words and their order must match.
    ignored = ",.;:" + _QUOTE_MARKS   # the quote side already lost its quote marks and apostrophes
    keep = [k for k, ch in enumerate(chars) if ch not in ignored]
    joined = "".join(chars[k] for k in keep)
    wanted = "".join(ch for ch in stripped_quote if ch not in ignored)
    at = joined.find(wanted) if wanted.strip() else -1
    if at < 0:
        return None
    covered = [index[keep[k]] for k in range(at, at + len(wanted)) if index[keep[k]] >= 0]
    if not covered:
        return None
    end = covered[-1] + 1
    while end < len(paragraph) and paragraph[end] in ",.;:!?" and stripped_quote[-1] in ",.;:!?":
        end += 1
    return paragraph[covered[0]:end]


# [31] Curly “…” or straight "…" speech: Rilla of Ingleside (#3796) uses straight quotes, and
# 481 of its scene quotes failed as not verbatim / not spoken with the curly-only pattern.
_SPEECH_SPAN_RE = re.compile(r'“([^”]*)(?:”|$)|"([^"]*)(?:"|$)')
_DIALOGUE_SPAN_RE = re.compile(r'“[^”]*(?:”|$)|"[^"]*(?:"|$)')


def within_dialogue(quote: str, paragraph: str) -> bool:
    """True when `quote` lies inside one of the paragraph's own quotation-mark spans. An
    unclosed span runs to the paragraph's end: a speech continued into the next paragraph opens
    with “ and does not close."""
    return any(quote in span for span in _DIALOGUE_SPAN_RE.findall(paragraph))


def chapter_of_para_id(para_id: str) -> int:
    """'v03:c02:p0015' -> 2. Mention records (entities/automaton.py::index_mentions) carry no
    chapter_idx field of their own, only para_id — this is the one place that needs it. Mirrors
    `cli.py::_chapter_of_para_id` exactly; kept here too so `extract/scenes.py` (a leaf module
    that must not import from `cli.py`) can filter mentions by chapter without duplicating the
    parsing logic a third time."""
    return int(para_id.split(":")[1][1:])


def _name_parts(text: str) -> set[str]:
    return set(_WORD_RE.findall(text))


_LABEL_PAREN_RE = re.compile(r"\s*\(.*$")  # strips a trailing "(also: ...)" / "(Kraft Lawrence)" etc.


def _strip_prompt_label_formatting(text: str) -> str:
    """Undo `group_known_entities`' own `"Canonical (also: alias1, alias2)"` formatting when a
    model echoes it back verbatim instead of copying a name from the passage (Phase 23 A1: the
    root cause of the false "Kraft Lawrence, Spouse of Marheit" relation — the model returned
    `object: "Lawrence Kraft (also: Lawrence)"`, a scrambled echo of the prompt's own known-entity
    header, not a name it read in the text). Keeps only the text before the first `(`; a name
    with a genuine parenthetical in the source text is not a real risk in this corpus."""
    return _LABEL_PAREN_RE.sub("", text).strip()


def resolve_surface(text: str, candidates: list[dict[str, Any]], passage_text: str) -> str | None:
    """Resolve free text against a closed list of `{"surface": str, "entity_id": str, ...}`
    candidates — never against the whole gazetteer, so the model cannot conjure a name it did not
    actually read. `passage_text` is the exact text the model was shown (a claim window or a scene
    span): a candidate only qualifies if its surface actually occurs there, so a name can never be
    resolved from words the model invented (Phase 23 A1).

    Exact surface match first (after stripping any `(also: ...)`-style label formatting the model
    echoed back rather than copying from the passage — see `_strip_prompt_label_formatting`), then
    a name-part fallback: `text` resolves to a candidate if it is, whole-token-for-whole-token,
    either equal to one word of the candidate's surface or a superset containing the candidate's
    surface as a run of words (both directions, e.g. "Kraft" against "Kraft Lawrence" and vice
    versa) — a Phase 22 B2 tightening (fixes S3): the previous raw substring check ("man" in
    "Norman") could misresolve a bare fragment sharing no whole word with the candidate at all.

    When more than one *distinct entity* qualifies at the longest matching surface length, the
    match is genuinely ambiguous and this returns `None` rather than silently picking one (Phase
    23 A1: previously `len(surface) > best[0]` — strict inequality — let a tie resolve to whichever
    candidate happened to come first in passage order, an invisible coin-flip on any real name
    collision). The same entity appearing under multiple qualifying surfaces is not ambiguity."""
    norm = _strip_prompt_label_formatting(text.strip()).lower()
    if not norm:
        return None
    passage_lower = passage_text.lower()

    exact_entities = {
        m["entity_id"]
        for m in candidates
        if m["surface"].strip().lower() == norm and m["surface"].strip().lower() in passage_lower
    }
    if exact_entities:
        return next(iter(exact_entities)) if len(exact_entities) == 1 else None

    norm_parts = _name_parts(norm)
    best_len = -1
    best_entities: set[str] = set()
    for m in candidates:
        surface = m["surface"].strip().lower()
        if not surface or surface not in passage_lower:
            continue
        surface_parts = _name_parts(surface)
        matches = norm_parts <= surface_parts or surface_parts <= norm_parts
        if not matches:
            continue
        if len(surface) > best_len:
            best_len = len(surface)
            best_entities = {m["entity_id"]}
        elif len(surface) == best_len:
            best_entities.add(m["entity_id"])
    if len(best_entities) == 1:
        return next(iter(best_entities))
    return None


def group_known_entities(entity_ids: Iterable[str], entities_by_id: dict[str, dict[str, Any]]) -> str:
    """Format a set of entity_ids as `"Canonical Name (also: alias1, alias2)"` lines, one per
    entity, instead of a flat list of raw surface strings — Phase 22 B2 (fixes S3). Both drivers
    used to list every surface seen in the passage/window independently
    (`{m["surface"] for m in mentions}`), so two aliases of the same person — "Lawrence" and
    "Kraft Lawrence" — appeared to the model as two different names with nothing tying them
    together. That is the actual cause of a beat_summary hallucinating "Lawrence sells furs to
    Kraft Lawrence": `beat_summary` is a free paraphrase, never resolved against entities, so no
    downstream resolution rule can fix a name collision the prompt itself created. Grouping by
    entity_id up front removes the ambiguity before the model ever sees it. Returns an empty
    string for an empty `entity_ids` — callers supply their own "(none known)"-style fallback."""
    lines = []
    for entity_id in entity_ids:
        entity = entities_by_id.get(entity_id)
        if entity is None:
            lines.append(entity_id)
            continue
        canonical = entity.get("canonical", entity_id)
        aliases = sorted({a for a in entity.get("aliases", []) if a and a != canonical})
        lines.append(f"{canonical} (also: {', '.join(aliases)})" if aliases else canonical)
    return ", ".join(sorted(lines))
