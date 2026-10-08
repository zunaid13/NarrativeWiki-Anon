"""[1] Text cleanup and DOM-level image substitution. CLAUDE.md section 3 traps 5 and 6.

Inputs:     A BeautifulSoup Tag (for `substitute_and_strip_images`) or raw extracted
            text (for `normalize_paragraph_text`).
Outputs:    The tag is mutated in place; text normalization returns a new string.
Invariants: - Image-to-dash substitution happens on the live tag tree, BEFORE `.get_text()`
              is called anywhere. Once images are stripped, an interrupted line of dialogue
              silently loses its dash with no trace left to recover it from.
            - `normalize_paragraph_text` is defensive about entities (`html.unescape`) but
              BeautifulSoup already decodes them at parse time; the tag-tree ordering above is
              what actually matters, this is just a safety net for double-escaped input.
Contract:   docs/CONTRACTS.md section 1 (fields `text`, `n_words`).
"""

from __future__ import annotations

import html
import re
from typing import Any

from bs4 import NavigableString, Tag

_WHITESPACE_RE = re.compile(r"\s+")


def substitute_and_strip_images(
    tag: Tag,
    image_rules: list[dict[str, str]],
    strip_remaining: bool,
) -> None:
    """Replace matching `<img>` descendants with literal text, then drop what's left.

    `image_rules` is `normalize.image_text_substitutions` from the series config: a list of
    `{src_contains, text}` mappings checked in order, first match wins. Must run before any
    `.get_text()` call on `tag` or its ancestors.
    """
    for img in list(tag.find_all("img")):
        src = img.get("src") or ""
        replacement = next(
            (rule["text"] for rule in image_rules if rule.get("src_contains", "") in src),
            None,
        )
        if replacement is not None:
            img.replace_with(NavigableString(replacement))
        elif strip_remaining:
            img.decompose()


def normalize_paragraph_text(text: str) -> str:
    """Collapse whitespace and guard against double-encoded entities.

    Idempotent on already-clean text, so it is always safe to call on BeautifulSoup's
    `.get_text()` output.
    """
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def count_words(text: str) -> int:
    return len(text.split())


def image_rules_from_config(series_config: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    normalize_cfg = series_config.get("normalize", {}) or {}
    rules = normalize_cfg.get("image_text_substitutions", []) or []
    strip_remaining = bool(normalize_cfg.get("strip_remaining_images", True))
    return rules, strip_remaining
