"""[Phase 23 A2] Evidence-adequacy gate, shared by `extract/claims.py` and `extract/scenes.py`.

The pre-existing verbatim-quote check (`quote not in paragraph["text"]`) proves a quote EXISTS in
the cited paragraph. It proves nothing about whether that quote actually SUPPORTS the fact it is
attached to — this is what let a false `SPOUSE_OF` between Marheit and Kraft Lawrence through on a
quote that is genuinely verbatim, genuinely in-volume, and cites a paragraph that names neither
"partner" as a person nor the object entity at all.

Inputs:     One fact's already-resolved subject entity dict, its object entity dict (relations
            only, else `None`), the paragraph text the evidence quote was drawn from, the quote
            itself, the predicate's `kind` and its `config/extraction.yaml` spec dict, and the
            caller's `extraction_behaviour` dict (read for the `evidence_adequacy` block).
Outputs:    `passes_evidence_adequacy(...) -> bool`. `False` means the caller must drop the fact
            (never render it hedged/"unconfirmed" — CLAUDE.md's zero-evidence rule: null, not an
            invented or softened claim) and count the specific reason in its own `drops` counter,
            exactly like every other extraction-time rejection.
Invariants: - Every check reads only the CITED paragraph's own text, never the wider window/span
              — that distinction is the whole point (the wider window almost always DOES contain
              both names; that is why `resolve_surface` alone could not catch this class of bug).
            - A subject/object is "named" in a paragraph by its canonical name or any known alias
              appearing case-insensitively as a substring — the same low-stakes check already
              implicit in how the corpus's own prose refers to a character (CLAUDE.md's corpus has
              no formal name-normalization needs beyond this at paragraph scope).
Contract:   docs/CONTRACTS.md §3.3; config/extraction.yaml's `extraction.evidence_adequacy` block
            and each relation's `requires_both_endpoints` flag.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _entity_named_in(entity: dict[str, Any] | None, text: str) -> bool:
    if entity is None:
        return False
    text_lower = text.lower()
    names = [entity.get("canonical", "")] + list(entity.get("aliases", []) or [])
    return any(name and name.strip().lower() in text_lower for name in names)


def neighbourhood_text(
    para_id: str,
    ordered_para_ids: list[str] | tuple[str, ...],
    records_by_id: dict[str, dict[str, Any]],
    radius: int,
) -> str:
    """The cited paragraph plus `radius` paragraphs either side of it, within the window/span the
    model was actually shown. Used only by the `subject_must_be_named` check -- see its config
    note on why a pronoun paragraph with the name one paragraph above it is real evidence."""
    if radius <= 0:
        return records_by_id.get(para_id, {}).get("text", "")
    try:
        i = list(ordered_para_ids).index(para_id)
    except ValueError:
        return records_by_id.get(para_id, {}).get("text", "")
    ids = list(ordered_para_ids)[max(0, i - radius): i + radius + 1]
    return "\n".join(records_by_id.get(pid, {}).get("text", "") for pid in ids)


def passes_evidence_adequacy(
    *,
    subject_entity: dict[str, Any] | None,
    object_entity: dict[str, Any] | None,
    true_kind: str,
    predicate_spec: dict[str, Any],
    paragraph_text: str,
    quote: str,
    behaviour: dict[str, Any],
    drops: Counter[str],
    qualifier: str | None = None,
    subject_context_text: str | None = None,
) -> bool:
    """Returns True iff `quote`/`paragraph_text` clears every configured adequacy check for this
    fact. Callers must have already resolved `subject_entity`/`object_entity` and validated the
    quote is verbatim in `paragraph_text` — this function assumes both are already true and only
    asks whether the evidence is ADEQUATE, not merely present."""
    cfg = behaviour.get("evidence_adequacy", {})

    min_quote_words = int(cfg.get("min_quote_words", 0) or 0)
    if min_quote_words and len(quote.split()) < min_quote_words:
        drops["quote_too_short"] += 1
        return False

    # The name may appear in the cited paragraph OR, when the caller supplies it, in the
    # immediately surrounding paragraphs of the same window (`subject_named_within_paragraphs`).
    # Prose carries a subject across paragraph breaks with pronouns constantly, so the strict
    # single-paragraph form dropped 325 of 670 otherwise-valid facts on a real v1-2 run -- more
    # than it kept -- while the misattribution it exists to catch (a fact pinned on whichever
    # character the call was about, from a paragraph that is not about them at all) is still
    # caught: a paragraph neighbourhood that never names them anywhere is not about them.
    if cfg.get("subject_must_be_named", False) and not _entity_named_in(
        subject_entity, subject_context_text or paragraph_text
    ):
        drops["subject_not_named_in_paragraph"] += 1
        return False

    if (
        true_kind == "relation"
        and predicate_spec.get("requires_both_endpoints")
        and not _entity_named_in(object_entity, paragraph_text)
    ):
        drops["endpoint_not_named_in_paragraph"] += 1
        return False

    # Phase 26 part B: a symmetric social predicate with no qualifier is a label, not a fact.
    # 24 of 89 relations in the live v1-2 graph rendered as a bare "Friend of" / "Enemy of".
    if true_kind == "relation" and predicate_spec.get("requires_qualifier") and not (qualifier or "").strip():
        drops["relation_without_qualifier"] += 1
        return False

    # Phase 26 part B: the qualifier itself says the predicate does not hold. All three of the
    # live graph's `wiki audit eval` negative-fact violations were RELATIVE_OF claims whose own
    # qualifier gave the game away -- "pet" and "shepherd's sheepdog" for a shepherdess and her
    # dog, "pretended debt as excuse to remain together" for two travelling companions. See
    # config/extraction.yaml's `qualifier_disqualifiers` note on why this is a per-predicate
    # substring list rather than a global rule.
    if true_kind == "relation" and qualifier:
        lowered = qualifier.lower()
        if any(term.lower() in lowered for term in predicate_spec.get("qualifier_disqualifiers") or []):
            drops["qualifier_disqualifies_predicate"] += 1
            return False

    # Phase 26 part C: the cited sentence describes something that did NOT happen. The part-B
    # gates all check WHO is named, never whether the event is real, so the live v1-2 graph kept
    # `holo KILLED yarei` (conf 0.99) from "The image of Yarei's torso in Holo's fangs came
    # unbidden to Lawrence's mind" and `kraft-lawrence KILLED liebert` from "If it had been up to
    # Lawrence, he would have killed the man". Prompt guidance did not hold twice already
    # (RELATIVE_OF), so this is a deterministic marker list, opt-in per predicate
    # (`requires_realis`) because a counterfactual is only fatal where the EVENT is the fact.
    if predicate_spec.get("requires_realis"):
        lowered_quote = quote.lower()
        if any(m.lower() in lowered_quote for m in cfg.get("irrealis_markers") or []):
            drops["irrealis_evidence"] += 1
            return False

    # 2026-09-21: the same lesson one layer along. A TRAIT asserts what someone is usually like,
    # so a sentence that explicitly frames the behaviour as a departure ("For the first time, a
    # flicker of empathy appeared there" -> jakob PERSONALITY "capable of empathy", conf 0.95)
    # argues against the trait rather than for it. The PERSONALITY prompt note asks for
    # characteristic behaviour and was ignored on its first real test, so this is deterministic
    # and opt-in per predicate, exactly like `requires_realis` above.
    if predicate_spec.get("requires_typicality"):
        lowered_quote = quote.lower()
        if any(m.lower() in lowered_quote for m in cfg.get("atypical_markers") or []):
            drops["atypical_evidence"] += 1
            return False

    return True


def prompt_rules(settings) -> str:
    """The adequacy gates above, written out as prompt rules, generated from the same config the
    gates read (2026-09-23).

    A gate the model was never told about silently deletes work it would otherwise have done
    correctly. Measured on a real v1-2 re-run: of ~750 facts extracted, 106 were dropped for a
    quote under `min_quote_words`, 163 for a relation with no qualifier, and the prompt actively
    invited the third failure -- it said a fact carried by a pronoun "still counts ... do not
    require the character's own name to appear", while `subject_must_be_named` threw exactly
    those away (342 of them). Two thirds of the gap between the free-tier model's claim count and
    this one's was the model obeying the prompt and being punished by config.

    Generated rather than hand-written so a config edit cannot leave the prompt lying.
    """
    # getattr, not attribute access: the prompt builders are called with stub settings objects in
    # tests that predate this block, and a missing gate config means "no rule to state", not a
    # crash. A real Settings always has both.
    behaviour = getattr(settings, "extraction_behaviour", {}) or {}
    relations = getattr(settings, "relations", {}) or {}
    cfg = behaviour.get("evidence_adequacy", {}) or {}
    lines: list[str] = []

    if min_words := int(cfg.get("min_quote_words", 0) or 0):
        lines.append(
            f'- "quote" must be at least {min_words} words long. A shorter quote cannot support a '
            f'fact on its own and is thrown away — quote the whole clause, not two words of it.'
        )
    if cfg.get("subject_must_be_named", False):
        radius = int(cfg.get("subject_named_within_paragraphs", 0) or 0)
        if radius > 0:
            lines.append(
                f'- The character\'s own name or a known alias MUST occur in the cited paragraph '
                f'or within {radius} paragraph(s) before or after it in the supplied passage. '
                'Cite and quote the paragraph that actually states the fact, even when it uses '
                'a pronoun; do not substitute a nearby paragraph merely because it names them. '
                'The antecedent must be unambiguous; proximity alone does not establish identity. '
                'If this context does not identify the character, omit the fact.'
            )
        else:
            lines.append(
                '- The paragraph you cite MUST contain the character\'s own name or one of their '
                'known aliases AND state the fact. If no such paragraph exists, leave the fact out. '
                'Do not substitute a paragraph that names them but does not support the fact.'
            )

    needs_qualifier = [n for n, meta in relations.items() if meta.get("requires_qualifier")]
    if needs_qualifier:
        lines.append(
            f'- These relations REQUIRE a non-empty "qualifier" saying what the bond consists of '
            f'({", ".join(sorted(needs_qualifier))}) — e.g. "travelling companions who share '
            f'profits", not just the predicate name. Without one the fact is thrown away, because '
            f'a bare "Friend of" is a label, not a fact.'
        )
    both_endpoints = [n for n, meta in relations.items() if meta.get("requires_both_endpoints")]
    if both_endpoints:
        lines.append(
            f'- For these relations the cited paragraph must name BOTH people '
            f'({", ".join(sorted(both_endpoints))}), not just the subject.'
        )
    return "\n".join(lines)
