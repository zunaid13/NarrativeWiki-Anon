"""[2] Aho-Corasick build + corpus mention index. THE TOKEN LEVER (VISION.md 2026-09-01).

Inputs:     A built gazetteer's `entities` list (CONTRACTS section 2.1: every `surface_forms`
            entry across every entity), and the parsed paragraph records for the volumes to
            index.
Outputs:    `MentionAutomaton` (build once, query many) and `index_mentions`, which yields
            CONTRACTS section 2.2 mention records in corpus order.
Invariants: - One O(n) pass per paragraph regardless of how many entities exist. This is what
              lets `extract/windows.py` (Phase 3) find every passage that names a character
              without ever scanning a whole chapter.
            - Overlapping matches are resolved longest-match-wins ("Shinei Nouzen" beats
              "Nouzen") so no downstream consumer handles overlaps.
            - Matches must land on word boundaries. Without this, "Shin" would fire inside
              "Shining" — pyahocorasick has no boundary concept, so it is enforced here.
Contract:   docs/CONTRACTS.md section 2.2.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from .. import paths

try:
    import ahocorasick

    _HAS_PYAHOCORASICK = True
except ImportError:  # pragma: no cover - exercised only on machines without the C extension
    ahocorasick = None  # type: ignore[assignment]
    _HAS_PYAHOCORASICK = False


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "'" or ch == "’"


def _is_boundary_match(text: str, start: int, end: int) -> bool:
    before_ok = start == 0 or not _is_word_char(text[start - 1])
    after_ok = end >= len(text) or not _is_word_char(text[end])
    return before_ok and after_ok


def _resolve_overlaps(
    matches: list[tuple[int, int, str, str]]
) -> list[tuple[int, int, str, str]]:
    """Sweep left to right; among matches sharing or overlapping a start, keep the longest."""
    ordered = sorted(matches, key=lambda m: (m[0], -(m[1] - m[0])))
    kept: list[tuple[int, int, str, str]] = []
    last_end = -1
    for start, end, surface, entity_id in ordered:
        if start >= last_end:
            kept.append((start, end, surface, entity_id))
            last_end = end
    return kept


class MentionAutomaton:
    """Wraps pyahocorasick when available; falls back to a naive multi-substring scan
    otherwise (correct, just O(entities x text) instead of O(text) — see `wiki doctor`, which
    reports which backend is active)."""

    def __init__(self) -> None:
        # [34] surface -> [(entity_id, vols or None)]: a bare name split by volume
        # (`alias._assign_bare_forms`) has one owner per volume range; None owns every volume.
        self._owners: dict[str, list[tuple[str, frozenset[int] | None]]] = {}
        self._aho = None
        self.backend = "pyahocorasick" if _HAS_PYAHOCORASICK else "fallback"
        # [24] populated by build_automaton, not here -- a literal surface claimed by more than
        # one entity, dropped rather than added twice. See build_automaton's own docstring and
        # docs/vision/plans/0008-pre-full-scale-audit.md Sec1.5 item 3 / `wiki audit gazetteer`.
        self.dropped_ambiguous_surfaces: list[dict[str, str]] = []

    def add(self, surface: str, entity_id: str, vols: list[int] | None = None) -> None:
        if surface:
            self._owners.setdefault(surface, []).append((entity_id, frozenset(vols) if vols is not None else None))

    def build(self) -> "MentionAutomaton":
        if _HAS_PYAHOCORASICK and self._owners:
            aho = ahocorasick.Automaton()
            for surface in self._owners:
                aho.add_word(surface, surface)
            aho.make_automaton()
            self._aho = aho
        return self

    def _owner(self, surface: str, vol: int | None) -> str | None:
        owners = self._owners[surface]
        if vol is None:
            return owners[0][0]
        return next((eid for eid, vols in owners if vols is None or vol in vols), None)

    def find_all(self, text: str, vol: int | None = None) -> list[tuple[int, int, str, str]]:
        """Non-overlapping, boundary-respecting, longest-match-wins matches in `text`. `vol` picks
        the owner of a surface split by volume; a surface with no owner in `vol` does not match."""
        raw: list[tuple[int, int, str, str]] = []
        if self._aho is not None:
            hits = ((end_index - len(surface) + 1, end_index + 1, surface)
                    for end_index, surface in self._aho.iter(text))
        else:
            hits = ((idx, idx + len(surface), surface) for surface in self._owners
                    for idx in _occurrences(text, surface))
        for start, end, surface in hits:
            if _is_boundary_match(text, start, end) and (entity_id := self._owner(surface, vol)):
                raw.append((start, end, surface, entity_id))
        return _resolve_overlaps(raw)


def _occurrences(text: str, surface: str) -> Iterator[int]:
    start = 0
    while (idx := text.find(surface, start)) != -1:
        yield idx
        start = idx + 1


def surface_inventory(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic inventory of every surface claimant and its actual index ownership.

    Includes collision losers and empty forms with indexed=False. Legacy rows without source
    are explicitly unknown; their origin cannot be recovered from the final gazetteer.
    The same ownership decisions feed build_automaton, so the export cannot drift from it.
    """
    # [34] A text claimed again is indexed again only for volumes no earlier claimant owns (a bare
    # name split by volume, `vols`); otherwise the first claimant keeps it, as before.
    owners: dict[str, str] = {}
    owned_vols: dict[str, set[int] | None] = {}   # text -> volumes taken so far; None = all
    claimants: dict[str, set[str]] = {}
    for entity in entities:
        for sf in entity["surface_forms"]:
            claimants.setdefault(sf["text"], set()).add(entity["entity_id"])
    rows = []
    for entity in sorted(entities, key=lambda e: e["entity_id"]):
        for sf in sorted(entity["surface_forms"], key=lambda s: s["text"]):
            text = sf["text"]
            vols = sf.get("vols")
            if text not in owned_vols:
                indexed = bool(text)
            else:
                taken = owned_vols[text]
                indexed = bool(text) and vols is not None and taken is not None and taken.isdisjoint(vols)
            if indexed:
                owned_vols[text] = None if vols is None else (owned_vols.get(text) or set()) | set(vols)
            owner = owners.setdefault(text, entity["entity_id"])
            rows.append({
                "text": text, "entity_id": entity["entity_id"],
                "first_vol": sf.get("first_vol", entity.get("first_vol", 1)),
                "source": sf.get("source", "unknown"),
                "ambiguous": bool(sf.get("ambiguous", False) or len(claimants[text]) > 1),
                "indexed": indexed, "index_entity_id": (entity["entity_id"] if indexed else owner) if text else None,
                **({"vols": vols} if vols is not None else {}),
            })
    return rows


def write_surface_inventory(entities: list[dict[str, Any]]) -> None:
    """Write the active series/build cutoff's inventory; caller owns run provenance."""
    rows = surface_inventory(entities)
    paths.surface_forms().write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def build_automaton(entities: list[dict[str, Any]]) -> MentionAutomaton:
    """One automaton over every surface form of every entity, indexed by `entity_id`.

    A literal surface claimed by more than one entity (flagged `ambiguous` by
    `entities/alias.py`) is added for whichever entity is processed first (alphabetically by
    `entity_id` — the `sorted()` below); this is a known, documented edge case (see
    docs/handover/PHASE_2.md), not silently swallowed — the `ambiguous` flag itself is what tells
    Phase 7's wikifier to tread carefully, and every drop is now recorded on the returned
    automaton's `dropped_ambiguous_surfaces` (`wiki audit gazetteer` reports the count; [24]
    docs/vision/plans/0008-pre-full-scale-audit.md Sec1.5 item 3 — previously an unmeasured loss).
    """
    automaton = MentionAutomaton()
    for row in surface_inventory(entities):
        if row["indexed"]:
            automaton.add(row["text"], row["entity_id"], row.get("vols"))
        elif row["text"]:
            automaton.dropped_ambiguous_surfaces.append({
                "surface": row["text"], "kept_entity_id": row["index_entity_id"],
                "dropped_entity_id": row["entity_id"],
            })
    return automaton.build()


def index_mentions(automaton: MentionAutomaton, volumes: list[int]) -> Iterator[dict[str, Any]]:
    """Scan every paragraph of every volume in `volumes` (ascending) and yield CONTRACTS
    section 2.2 mention records in corpus order. Volumes with no parsed file are skipped."""
    for vol in sorted(volumes):
        path = paths.parsed_volume(vol)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                for start, end, surface, entity_id in automaton.find_all(record["text"], record["vol"]):
                    yield {
                        "para_id": record["para_id"],
                        "vol": record["vol"],
                        "entity_id": entity_id,
                        "surface": surface,
                        "start": start,
                        "end": end,
                    }
