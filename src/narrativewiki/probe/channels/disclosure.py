"""[24] Surface disclosure: does a visible entity's vocabulary occur in cutoff text?

Inputs:     The active gazetteer and every parsed volume in 1..upto_vol.
Outputs:    A first_occurrence measurement, preserving the archived analysis's exact and
            article-stripped, case-insensitive substring checks as separate metrics.
Invariants: No LLM calls, no writes, no future-text reads. Entity visibility defines the
            denominator; surface first_vol is deliberately not filtered, since this probe
            measures the vocabulary held by the index for already-visible entities.
            String occurrence is not entity identity, fact entailment, or semantic disclosure.
Contract:   docs/CONTRACTS.md §9. Promoted from scripts/probe/reanalysis_first_occurrence.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ... import paths


def measure_surface_disclosure(series_id: str, upto_vol: int) -> dict[str, Any]:
    """Locate the first exact and normalized occurrence within the supplied reading prefix."""
    gazetteer = json.loads(paths.gazetteer().read_text(encoding="utf-8"))
    blobs: dict[int, str] = {}
    for vol in range(1, upto_vol + 1):
        path = paths.parsed_volume(vol)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        blobs[vol] = "\n".join(record["text"] for record in records)

    surfaces = sorted({
        sf["text"] for entity in gazetteer["entities"]
        if int(entity.get("first_vol", 1)) <= upto_vol
        for sf in entity.get("surface_forms", [])
    })

    def first(pattern: re.Pattern[str]) -> dict[str, int] | None:
        for vol, blob in blobs.items():
            match = pattern.search(blob)
            if match is not None:
                return {"vol": vol, "start": match.start(), "end": match.end()}
        return None

    occurrences = []
    for surface in surfaces:
        normalized = re.sub(r"^(the|a|an)\s+", "", surface.strip(), flags=re.I)
        occurrences.append({
            "surface": surface,
            "normalized_surface": normalized,
            "first_exact": first(re.compile(re.escape(surface))),
            "first_normalised": first(re.compile(re.escape(normalized), re.I)),
        })
    unseen_exact = [r["surface"] for r in occurrences if r["first_exact"] is None]
    unseen_normalised = [r["surface"] for r in occurrences if r["first_normalised"] is None]
    total = len(surfaces)
    return {
        "channel": "L_build", "probe": "first_occurrence", "series": series_id,
        "upto_vol": upto_vol, "cutoff_visible_surface_forms": total,
        "unseeable_exact": unseen_exact,
        "unseeable_exact_rate": len(unseen_exact) / total if total else 0.0,
        "unseeable_normalised": unseen_normalised,
        "unseeable_normalised_rate": len(unseen_normalised) / total if total else 0.0,
        "occurrences": occurrences,
    }
