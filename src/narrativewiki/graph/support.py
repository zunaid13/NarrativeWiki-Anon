"""Evidence support before arbitration and for interval verification.

Each claim is checked at its first evidence volume, using only cited paragraphs and their
same-chapter +/-4 context. Scoring at discovery prevents later corroboration from changing
an earlier page. All classifier calls go through LLMClient.score_support. Missing source text
is an error, not an unsupported verdict. Scores never replace extracted confidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .verify import EvidenceContext


def hypothesis(claim, entities, settings):
    subject = entities[claim["subject"]]["canonical"]
    predicate = claim["predicate"]
    cfg = settings.extraction_behaviour.get("support", {})
    template = cfg.get("templates", {}).get(predicate)
    if template is None:
        if claim["kind"] == "relation":
            raise ValueError(f"No support hypothesis template for relation {predicate}")
        label = settings.attributes.get(predicate, {}).get("display", predicate.lower())
        template = "{subject}'s " + label.lower() + " is {value}."
    text = template.format(subject=subject, value=claim.get("value") or "",
                           object=entities[claim["object"]]["canonical"] if claim.get("object") else "")
    polarity = claim.get("polarity", "asserted")
    if polarity == "denied":
        text = "It is not true that " + text
    elif polarity == "presumed":
        text = "It is believed that " + text
    elif polarity != "asserted":
        raise ValueError(f"Unknown claim polarity: {polarity}")
    return text


def evidence_documents(claim, context):
    cutoff = claim["first_vol"]
    evidence = []
    for ev in claim["evidence"]:
        match = re.fullmatch(r"v(\d+):c(\d+):p(\d+)", ev["para_id"])
        if not match:
            raise ValueError(f"Malformed support evidence paragraph: {ev['para_id']}")
        if 1 <= int(match[1]) <= cutoff:
            evidence.append(ev)
    if not evidence:
        raise ValueError(f"No cutoff-safe evidence for {claim['claim_id']}")
    contexts = context.for_facts([{"evidence": evidence}])
    documents, seen = [], set()
    for ev in sorted(evidence, key=lambda e: (e["para_id"], e["quote"])):
        paragraphs = contexts.get(ev["para_id"], [])
        cited = next((p for p in paragraphs if p["para_id"] == ev["para_id"]), None)
        if cited is None or not ev["quote"] or ev["quote"] not in cited["text"]:
            raise ValueError(f"Missing or nonverbatim support evidence: {ev['para_id']}")
        doc = "\n".join(p["text"] for p in paragraphs)
        if doc not in seen:
            seen.add(doc)
            documents.append(doc)
    return documents


def score_claims(claims, entities, settings, client):
    """Return claim_id -> auditable classifier result; fail the whole operation on any error."""
    results, contexts = {}, {}
    for claim in claims:
        cutoff = claim["first_vol"]
        if cutoff not in contexts:
            contexts[cutoff] = EvidenceContext(cutoff)
        context = contexts[cutoff]
        documents = evidence_documents(claim, context)
        text = hypothesis(claim, entities, settings)
        fingerprint = hashlib.sha256(json.dumps(
            {"documents": documents, "claim": text, "evidence_cutoff": cutoff},
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        result = client.score_support(documents, text)
        results[claim["claim_id"]] = {
            **result, "claim_id": claim["claim_id"], "hypothesis": text,
            "evidence_cutoff": cutoff, "context_sha256": fingerprint,
            "extracted_confidence": claim["confidence"],
        }
    return results


def arbitration_claims(claims, settings):
    """Relations plus single-valued attributes; score before any confidence-based pruning."""
    return [c for c in claims if c["kind"] == "relation" or
            (c["kind"] == "attribute" and settings.attributes.get(c["predicate"], {}).get("single"))]


def verify_intervals(conn, characters, entities, upto, settings, client, run_id):
    """Score each visible interval once, at discovery; return report and binding verdicts.

Only relation and single-attribute intervals are in scope. Source claims at later volumes
are excluded even if the interval has accumulated their evidence since its first appearance.
"""
    from . import temporal

    rows = {}
    for entity in characters:
        for row in [*temporal.state_at(conn, entity["entity_id"], upto),
                    *temporal.relations_at(conn, entity["entity_id"], upto)]:
            if row["object"] is not None or settings.attributes.get(row["predicate"], {}).get("single"):
                rows[row["interval_id"]] = row
    claims = []
    for iid, row in sorted(rows.items()):
        source_claims = []
        for cid in json.loads(row["claim_ids_json"]):
            c = conn.execute("SELECT * FROM claims WHERE claim_id = ? AND first_vol <= ?",
                             (cid, row["vol_start"])).fetchone()
            if c is not None:
                source_claims.append(dict(c))
        polarities = {c["polarity"] for c in source_claims}
        # Match rendering's preference for asserted evidence when an interval mixes polarities.
        polarity = "asserted" if "asserted" in polarities else "presumed" if "presumed" in polarities else "denied"
        evidence = [e for c in source_claims if c["polarity"] == polarity for e in json.loads(c["evidence_json"])]
        claims.append({
            "claim_id": iid, "subject": row["subject"], "predicate": row["predicate"],
            "object": row["object"], "value": row["value"], "first_vol": row["vol_start"],
            "confidence": row["confidence"], "kind": "relation" if row["object"] else "attribute",
            "polarity": polarity, "evidence": evidence,
        })
    scores = score_claims(claims, entities, settings, client)
    threshold = float(settings.extraction_behaviour.get("support", {}).get("threshold", 0.5))
    if not 0 <= threshold <= 1:
        raise ValueError("support threshold must be in [0, 1]")
    verdicts = [{
        "target_id": iid, "verdict": "supported" if result["score"] > threshold else "unsupported",
        "rationale": f"MiniCheck support {result['score']:.6f}; threshold > {threshold:g}",
        "model": result["model"], "run_id": run_id, "support_score": result["score"],
        "support_threshold": threshold, "context_sha256": result["context_sha256"],
        "evidence_cutoff": result["evidence_cutoff"], "model_revision": result["revision"],
    } for iid, result in scores.items()]
    flagged = {v["target_id"]: v for v in verdicts if v["verdict"] == "unsupported"}
    results = []
    checked_characters = set()
    for entity in characters:
        ids = {iid for iid, r in rows.items() if entity["entity_id"] in (r["subject"], r["object"])}
        if ids:
            checked_characters.add(entity["entity_id"])
        if ids & flagged.keys():
            results.append({"entity_id": entity["entity_id"], "canonical": entity["canonical"],
                            "upto_vol": upto, "flagged": [
                                {"interval_id": iid, "predicate": rows[iid]["predicate"],
                                 "value": rows[iid]["value"] or rows[iid]["object"],
                                 "rationale": flagged[iid]["rationale"]} for iid in sorted(ids & flagged.keys())]})
    return {"upto_vol": upto, "method": "minicheck", "scores": scores, "results": results,
            "summary": {"characters_checked": len(checked_characters), "characters_flagged": len(results),
                        "facts_checked": len(scores), "facts_flagged": len(flagged)}}, verdicts
