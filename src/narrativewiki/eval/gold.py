"""[21d] Hand-transcribed gold-standard data + the recall/entailment metrics computed against it.

Inputs:     `docs/eval/gold/<series_id>/*.yaml` — hand-transcribed from a real reference wiki page
            (see each file's own `source_url`/`transcribed`/`eval_vol` fields), never written by
            the pipeline itself. `data/05_pages/<entity_id>/v{NN}.json` (CONTRACTS §5, pre-
            wikification — plain text, nothing to strip) for the page actually generated at or
            below each gold file's own `eval_vol`. `data/01_parsed/v{NN}.jsonl` for citation
            entailment's paragraph lookup.
Outputs:    `load_gold(series_id)` -> one `GoldEntry` per YAML file (now also carrying
            `negative_facts`, Phase 23 C3). `score_heading_recall`, `score_fact_recall`,
            `score_fact_precision` (Phase 23 C1), `score_citation_entailment` -> per-metric
            result dicts consumed by `audit/reports.py`'s `wiki audit eval` report (the only
            caller). `load_baseline(series_id)` (Phase 22 C4) -> `{entity_id: fact_recall}` from
            the sibling, also hand-maintained `baseline.json` -- the recorded floor `_eval_report`
            gates a regression against (Phase 23 C4 adds a sibling `precision` map to the same
            file).
Invariants: - `in_scope: false` facts are EXCLUDED from `score_fact_recall`'s denominator, not
            just down-weighted. A real reference wiki describes the WHOLE series; this project's
            #1 invariant (CLAUDE.md) is that a page built at cutoff N must never reveal anything
            past volume N — scoring recall against an out-of-scope fact would penalize CORRECT
            spoiler suppression, exactly backwards from what an eval harness should reward.
            - Both `score_heading_recall`/`score_fact_recall` are LEXICAL keyword-overlap
            heuristics, not a semantic judge — deliberately: this project spends LLM calls only on
            the stages CLAUDE.md §2 names (bulk extraction, final prose, arbitration); evaluation
            is not one of them. They are a recall SIGNAL for a human reviewer to skim, not a
            certified score — the report always prints the per-fact/per-heading verdict so a
            reviewer can see exactly what the heuristic decided and why, and correct it by eye.
            - `score_citation_entailment` needs no gold data at all: it re-derives each `kind:
            prose` section's cited paragraphs from `data/01_parsed` (`prose.<section>.evidence`,
            already a citation list per CONTRACTS §5) and checks vocabulary overlap between the
            generated text and the UNION of its own cited paragraphs — a self-grounding check on
            OUR OWN output, answering "does this citation actually support this sentence" the same
            heuristic way, not "is this fact true against an external source."
            - For a `source: "events"` section (chronology), `evidence` is deliberately narrow —
            `synth/prose.py::generate_chronology` only stores `event_claims.para_id`, not each
            event's much larger `core_para_ids` (`graph/events.py`; the paragraphs `beat_summary`
            was actually paraphrased from — see that module's docstring for why the live page
            never carries the full set). This function's own overlap check would then under-count
            a well-grounded chronology whose events had no state_change/quote. When the caller
            passes `events_conn`/`entity_id`/`vol`/`events_source_keys`, this function ALSO checks
            overlap against that broader `core_para_ids` union for just those sections — an
            eval-only, on-demand widening, never written back to `data/05_pages/`.
Contract:   docs/CONTRACTS.md §8.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import paths

# Fandom-style heading (lowercased) -> the `page_outline` section key(s) that would satisfy it.
# An empty set is a DELIBERATE, expected gap (e.g. "Trivia"/"Gallery" have no equivalent in a
# page built entirely from deterministic graph rows plus capped, evidence-gated prose — CLAUDE.md
# §2's "structured fields are never LLM-generated" rule leaves no room for trivia) -- not a bug,
# but still correctly scored as a recall miss so the report shows the true structural gap.
#
# Phase 23 E1: the base taxonomy's own section keys changed (chronology+background merged into
# `history`; a new `appearance` prose section replaces the old infobox-only `overview` bullet
# list for physical description) -- both the OLD keys (background/chronology/overview) and the
# NEW ones (history/appearance) are kept here so this table matches EITHER a page built before or
# after Phase 23 E1 (a per-series overlay could also still declare the old shape), rather than
# assuming every bundle on disk was built by the current config.
_HEADING_ALIASES: dict[str, set[str]] = {
    "appearance": {"appearance", "overview"},
    "personality": {"personality"},
    "history": {"history", "background", "chronology"},
    "background": {"history", "background", "chronology"},
    "plot": {"history", "chronology"},
    "biography": {"history", "background", "chronology"},
    "relationships": {"relationships", "affiliations"},
    "quotes": {"quotes"},
    "trivia": set(),
    "gallery": set(),
    "references": set(),
    "navigation": set(),
}

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto", "than", "then",
    "them", "they", "their", "there", "she", "her", "hers", "his", "him", "who", "when",
    "what", "which", "while", "were", "was", "been", "being", "have", "has", "had", "does",
    "did", "not", "but", "also", "some", "most", "more", "over", "such", "each", "very",
    "about", "would", "could", "should", "will", "shall", "these", "those", "eventually",
}


def _keywords(text: str) -> set[str]:
    """Lowercased content words, length >= 4, stopwords dropped — the unit both keyword-overlap
    heuristics compare on. Short/common words are excluded because they inflate overlap without
    indicating real recall (e.g. "with" appears in almost every sentence of any language)."""
    # [30] A trailing possessive is the same name: gold "Lutz's mother" never matched "Lutz".
    words = [re.sub(r"'s$", "", w) for w in re.findall(r"[a-zA-Z']+", text.lower())]
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


@dataclass
class GoldFact:
    text: str
    in_scope: bool


@dataclass
class GoldEntry:
    entity_id: str
    canonical: str
    source_url: str
    eval_vol: int
    outline: list[str]
    facts: list[GoldFact]
    negative_facts: list[str]  # Phase 23 C3 -- assertions the wiki must NOT make about this entity


def load_gold(series_id: str) -> list[GoldEntry]:
    """Every `docs/eval/gold/<series_id>/*.yaml` file, sorted by `entity_id`. `[]` (not an error)
    when the directory doesn't exist — a series with no gold data yet is not itself a failure."""
    gold_dir = paths.eval_gold_dir(series_id)
    if not gold_dir.is_dir():
        return []
    entries = []
    for path in sorted(gold_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries.append(
            GoldEntry(
                entity_id=doc["entity_id"],
                canonical=doc["canonical"],
                source_url=doc["source_url"],
                eval_vol=int(doc["eval_vol"]),
                outline=list(doc.get("outline", [])),
                facts=[GoldFact(text=f["text"], in_scope=bool(f.get("in_scope", True))) for f in doc.get("facts", [])],
                negative_facts=list(doc.get("negative_facts", [])),
            )
        )
    return sorted(entries, key=lambda e: e.entity_id)


def load_baseline(series_id: str) -> dict[str, float]:
    """Phase 22 C4: `{entity_id: fact_recall}` from `docs/eval/gold/<series_id>/baseline.json` —
    `{}` (not an error) when the file doesn't exist yet, the same "absence is not a failure"
    convention `load_gold` uses for a series with no gold data at all. Hand-maintained, never
    written by the pipeline: a human raises the recorded floor deliberately (after confirming a
    genuine improvement), the same way a gold YAML fact is added or corrected by hand."""
    path = paths.eval_baseline(series_id)
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in doc.get("fact_recall", {}).items()}


def load_precision_baseline(series_id: str) -> dict[str, float]:
    """Phase 23 C4: `{entity_id: fact_precision}` from the SAME `baseline.json`
    `load_baseline` reads, sibling key `precision` alongside `fact_recall` — same hand-maintained,
    "raise it deliberately after confirming a genuine improvement" discipline. `{}` when the file
    doesn't exist, or exists but predates this key (a pre-Phase-23 baseline.json has no
    `precision` map at all)."""
    path = paths.eval_baseline(series_id)
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in doc.get("precision", {}).items()}


def nearest_page_at_or_below(entity_id: str, vol: int) -> dict[str, Any] | None:
    """The page JSON (CONTRACTS §5) for `entity_id` at the nearest cutoff <= `vol` — the same
    sparse-cutoff walk every other reader of `data/05_pages/` uses (`synth/cache.py::
    previous_page`'s own downward walk, inclusive of `vol` itself here since the caller already
    knows exactly which volume it wants to evaluate at, not "whatever came before it")."""
    for v in range(vol, 0, -1):
        candidate = paths.page_json(entity_id, v)
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


_PAGE_TEXT_SKIP_KEYS = {
    # Phase 23 C2: this function's own docstring always claimed to exclude these, but the
    # original `add()` never actually filtered by key -- it recursed into every value inside
    # `fields`, INCLUDING nested relationship/affiliation dicts (fields.relationships,
    # fields.affiliations), so `entity_id: "marheit"` and `predicate: "SPOUSE_OF"` landed in the
    # keyword bag exactly like real prose. A FALSE relation's own raw identifiers could then only
    # ever RAISE fact_recall (a keyword collision is pure upside for the metric), the opposite of
    # what a gold-fact "the wiki does not know this" check should reward. Structural/identifier
    # keys are skipped; genuinely human-readable value keys (value, note, blurb, label, role,
    # quote, canonical, text) still count.
    "entity_id", "predicate", "claim_set_hash", "generated_by", "para_id", "interval_id",
    "claim_id", "claim_ids", "since_vol", "vol_end", "vols", "vol", "show_vol", "current", "polarity", "inferred", "history",
}


def _page_text(page: dict[str, Any]) -> str:
    """Every string worth matching keywords against: the page's own `canonical` name, structured
    `fields` (attribute values, role/label text — nicknames included, so an alias needs no separate
    field), and `kind: prose` section text/quote text — NOT `entity_id`/`claim_set_hash`/
    `generated_by`/`para_id`/predicate-name/other identifier keys (`_PAGE_TEXT_SKIP_KEYS`), which
    would only add noise -- or worse, let a false relation's own predicate constant raise recall
    for an unrelated gold fact that happens to share a word with it. `canonical` is included
    (Phase 23 Part F, 2026-09-11) because `score_fact_precision`'s negative-fact check requires
    FULL keyword coverage, including the subject's own name -- every real negative_fact in this
    project reads "<this character> is not a RELATIONSHIP of <other>", so leaving the subject's own
    name out of its own page's text would make that name-token unmatchable on every page, never
    just the false ones."""
    parts: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            if "label" in value and isinstance(value.get("entity_id"), str):
                # [30] A rendered relationship/affiliation entry shows its counterpart's NAME
                # ("Karla — Child of — mother"); the id is skipped below, and with it the only
                # word tying the entry to "Lutz's mother is Karla". Entries here already passed
                # verification (withheld ones never reach `fields`), so this is visible text.
                parts.append(value["entity_id"].replace("-", " "))
            for key, v in value.items():
                if key in _PAGE_TEXT_SKIP_KEYS:
                    continue
                add(v)
        elif isinstance(value, list):
            for v in value:
                add(v)

    add(page.get("canonical"))
    add(page.get("fields", {}))
    add(page.get("traits", {}))
    for section in (page.get("prose") or {}).values():
        if section:
            add(section.get("text"))
    for q in page.get("quotes") or []:
        add(q.get("quote"))
    return " ".join(parts)


def score_heading_recall(outline: list[str], section_keys: list[str]) -> dict[str, Any]:
    """Fraction of the gold `outline` headings that have an equivalent among this project's own
    (fixed, config-driven) `page_outline` section KEYS (`settings.page_outline`'s `key`, e.g.
    "background"/"chronology" — not the display `title`, which is not guaranteed to match
    `_HEADING_ALIASES`'s vocabulary even though it happens to in the base config) — see
    `_HEADING_ALIASES`'s module-level docstring for why some headings are EXPECTED to have no
    equivalent."""
    section_keys_norm = {k.strip().lower() for k in section_keys}
    headings = []
    for heading in outline:
        norm = heading.strip().lower()
        aliases = _HEADING_ALIASES.get(norm, set())
        headings.append({"heading": heading, "matched": bool(aliases & section_keys_norm)})
    recall = sum(h["matched"] for h in headings) / len(headings) if headings else None
    return {"recall": recall, "headings": headings}


def score_fact_recall(facts: list[GoldFact], page: dict[str, Any]) -> dict[str, Any]:
    """Fraction of `in_scope` facts whose keywords are majority-covered by the generated page's
    own text (`_page_text`) — a lexical recall SIGNAL, not a semantic judge (module docstring)."""
    page_kw = _keywords(_page_text(page))
    scored = []
    for fact in facts:
        if not fact.in_scope:
            continue
        fact_kw = _keywords(fact.text)
        if not fact_kw:
            continue
        matched = sorted(fact_kw & page_kw)
        found = _fact_matches(fact_kw, page_kw)
        scored.append({"text": fact.text, "found": found, "matched_keywords": matched})
    recall = sum(f["found"] for f in scored) / len(scored) if scored else None
    return {"recall": recall, "facts": scored}


def _fact_matches(fact_kw: set[str], candidate_kw: set[str]) -> bool:
    """The one overlap rule both `score_fact_recall` and `annotate_missed_facts` apply, so the
    two are comparable by construction: >= half the fact's keywords, and never fewer than 2."""
    return len(fact_kw & candidate_kw) >= max(2, round(len(fact_kw) * 0.5))


def claim_keywords(conn, entity_id: str, upto: int) -> set[str]:
    """Every keyword this entity's claims carry at or below `upto` -- value, object, the
    de-underscored predicate, and each evidence quote -- pooled into ONE bag.

    Pooled deliberately, to mirror `_page_text`: the recall metric asks "do this fact's keywords
    appear anywhere on the page", so the only honest counterpart asks "do they appear anywhere in
    the graph". Matching per-claim instead would compare a pooled bag against unpooled ones and
    report an extraction gap for facts the graph demonstrably holds -- a gold sentence carries far
    more keywords than any single claim does, so it can clear the threshold against the page and
    fail it against every individual claim.

    `first_vol <= upto` is the spoiler filter and is not optional even here: this feeds a printed
    report, and a diagnosis drawn from a volume the reader has not reached leaks exactly what
    CLAUDE.md's #1 invariant forbids.
    """
    rows = conn.execute(
        "SELECT predicate, object, value, evidence_json FROM claims "
        "WHERE subject = ? AND first_vol <= ?", (entity_id, upto),
    ).fetchall()
    parts: list[str] = []
    for predicate, obj, value, evidence_json in rows:
        parts += [str(predicate or "").replace("_", " "), str(obj or "").replace("-", " "), str(value or "")]
        try:
            for ev in json.loads(evidence_json or "[]"):
                parts.append(str(ev.get("quote") or ""))
        except (ValueError, AttributeError):
            pass
    return _keywords(" ".join(parts))


# Phase 27: the semantic half of fact recall. MiniCheck-FT5 is LOCAL and unmetered, so this
# spends no OpenRouter quota -- the budget CLAUDE.md §2 actually protects. 0.5 is MiniCheck's own
# documented operating point and is not tuned per corpus here; the two facts it recovered on the
# v1-2 gold set scored 0.984 and 0.986, nowhere near the boundary, so the exact value is not
# load-bearing (measured 2026-09-21, docs/vision/PHASE_27.md).
SEMANTIC_RECALL_THRESHOLD = 0.5


def page_chunks(page: dict[str, Any]) -> list[str]:
    """The page as MiniCheck-sized documents: structured fields, each prose section, and the
    whole page.

    Three shapes rather than one because chunking changes the answer and neither extreme is
    right. MiniCheck-FT5 truncates at 512 tokens, so ONE whole-page document silently drops the
    tail; but the score is a MAX over documents, so chopping finely makes a fact that needs two
    sections entailed by neither -- measured, Liebert's "tense and nervous during the smuggling
    job" scored 0.797 whole-page and 0.059 sentence-chunked. Sections are the natural unit (a
    personality fact should be entailed by the Personality section), and the truncated whole-page
    chunk is kept so a cross-section fact still has one document that spans it.

    Each prose section is prefixed with the character's name because the sections use pronouns
    throughout, and an entailment check cannot resolve "he" against a claim naming the character.
    """
    out: list[str] = []
    fields_only = _page_text({
        "fields": page.get("fields", {}),
        "traits": page.get("traits", {}),
        "prose": {},
        "quotes": [],
        "canonical": page.get("canonical"),
    })
    if fields_only.strip():
        out.append(fields_only.strip())
    name = page.get("canonical") or ""
    for section in (page.get("prose") or {}).values():
        text = section.get("text") if isinstance(section, dict) else None
        if text and text.strip():
            out.append(f"{name}. {text.strip()}" if name else text.strip())
    whole = _page_text(page).strip()
    if whole:
        out.append(whole[:1800])
    return out


def augment_recall_semantically(fact_result, page, score_support, threshold=SEMANTIC_RECALL_THRESHOLD):
    """UNION a semantic entailment check into an already-computed lexical recall result.

    A union, never a replacement: MiniCheck is a stricter judge than keyword overlap, not a
    better one. It reads paraphrase that keywords cannot -- gold "dreams of becoming a tailor"
    against a page saying "dressmaker" scores 0.984 -- but it also refuses compound gold
    sentences that keyword overlap happily passes, because entailment demands the WHOLE
    proposition. Scoring by entailment alone measured WORSE than lexical on 3 of 5 gold
    characters. Taking the union keeps every lexical hit and can only raise recall, so this can
    never regress the signal the baseline was recorded against.

    `score_support(documents, claim) -> {"score": float}` is injected rather than imported so
    this module stays free of `llm/`, and so the tests do not need a model. Each upgraded fact is
    marked `semantic=True` with its score, so a reader can always see which hits were lexical.
    Mutates and returns `fact_result`.
    """
    chunks = page_chunks(page)
    if not chunks:
        return fact_result
    upgraded = 0
    for fact in fact_result["facts"]:
        if fact["found"]:
            continue
        score = float(score_support(chunks, fact["text"])["score"])
        fact["semantic_score"] = score
        if score >= threshold:
            fact["found"] = True
            fact["semantic"] = True
            upgraded += 1
    scored = fact_result["facts"]
    if scored:
        fact_result["recall"] = sum(f["found"] for f in scored) / len(scored)
    fact_result["semantic_upgrades"] = upgraded
    return fact_result


def annotate_missed_facts(fact_result: dict[str, Any], claim_kw: set[str]) -> None:
    """Mark each NOT-recalled fact with `in_graph`: do this entity's claims already cover it?

    The one bit that turns fact recall from a score into a diagnosis -- it separates "the page did
    not print it" from "we never extracted it", which the recall float alone cannot distinguish.

    **The two directions do not carry equal confidence, and the report must not present them as
    if they did.** `in_graph: True` is reliable: the keywords really are in the graph, so the
    omission is the page's -- normally the deliberate 2-4 sentence prose cap (CLAUDE.md §2), i.e.
    nothing to fix. `in_graph: False` is only "no LEXICAL match" and over-reports, because it
    inherits both weaknesses of the surrounding heuristic. Measured on the real v1-2 graph
    (2026-09-21): gold "Norah dreams of becoming a tailor" scores False while the graph holds
    `tailors` and `dressmaker` (a morphology/synonym miss), and the 14-keyword "Orphaned ... an
    almshouse" sentence lands 5 against a threshold of 6 while the graph holds three separate
    almshouse claims -- long gold sentences simply carry more keywords than the claims phrasing
    the same fact. Treat False as "worth a look", never as proof of an extraction gap.

    Recall itself is NOT changed by this; the gate keeps asserting exactly what it asserted
    before. Deliberately NOT fixed by lowering the threshold: that would inflate the recall score
    this same rule computes, which is tuning the metric to the answer (PHASE_26.md, 2026-09-21).
    """
    for fact in fact_result["facts"]:
        if fact["found"]:
            continue
        fact_kw = _keywords(fact["text"])
        fact["in_graph"] = _fact_matches(fact_kw, claim_kw)


_AFFILIATION_SYNONYM_KEYWORDS = {"affiliated", "affiliation", "member", "membership"}
# `fields.affiliations` entries (assemble.py::_relationship_entries) carry no predicate string at
# all -- every entry means the one fixed AFFILIATED_WITH relation, whose own config `display` is
# "Affiliation" (a different word-form than "affiliated"/"member", the phrasings this project's
# gold negative_facts actually use) -- added unconditionally per affiliation entry, not derived
# from page text, so a real membership is still catchable under these phrasings.


def _relationship_entry_keywords(page: dict[str, Any]) -> list[set[str]]:
    """One keyword set per rendered relationship/affiliation entry (Phase 23 Part F,
    2026-09-11) -- entity_id (de-hyphenated), predicate (de-underscored -- PARENT_OF's own
    constant reads as "parent of"), and label/note/blurb, all from that SAME entry, never pooled
    across the whole page. This is what `score_fact_precision`'s negative-fact check matches
    against instead of `_page_text`'s pooled bag: a negative fact must find ONE entry that
    supplies BOTH the specific other party AND the specific relationship word together, not just
    either signal floating anywhere on the page. See that function's own docstring for why pooling
    (this project's first attempt) does not work."""
    fields = page.get("fields", {}) or {}
    out: list[set[str]] = []
    for entry in fields.get("relationships") or []:
        parts = [
            str(entry.get("entity_id", "")).replace("-", " "),
            str(entry.get("predicate", "")).replace("_", " "),
            str(entry.get("label") or ""),
            str(entry.get("note") or ""),
            str(entry.get("blurb") or ""),
        ]
        out.append(_keywords(" ".join(parts)))
    for entry in fields.get("affiliations") or []:
        parts = [str(entry.get("entity_id", "")).replace("-", " "), str(entry.get("role") or "")]
        out.append(_keywords(" ".join(parts)) | _AFFILIATION_SYNONYM_KEYWORDS)
    return out


def score_fact_precision(
    facts: list[GoldFact], negative_facts: list[str], page: dict[str, Any]
) -> dict[str, Any]:
    """Phase 23 C1. `score_fact_recall` answers "how much of what gold says is on the page";
    this answers the question whose absence let the false "Kraft Lawrence, Spouse of Marheit"
    relationship unnoticed — "does the page assert something gold says it must NOT".

    `supported` reuses exactly the in-scope positive facts `score_fact_recall` already counts as
    `found` (still the original pooled, majority-overlap `_page_text` heuristic -- appropriate
    there, a real fact reworded differently should still count as recalled) -- this is not
    circular, it is the intended contrast: `precision = supported / (supported + contradicted)`
    is undefined (`None`) when gold has no opinion either way, so a sparse or under-covered gold
    file never manufactures a false precision score.

    Negative facts are checked differently (Phase 23 Part F, 2026-09-11, rewritten twice live
    against the V1-2 re-run's own gold-eval output before landing here): every one of this
    project's negative_facts is shaped "X is not a RELATIONSHIP-WORD of Y". Pooled full-page
    keyword coverage was tried first and both under- and over-fired: requiring the object Y's own
    name kept failing (a real relationship entry's `label`/`blurb` never repeats the OTHER
    party's name -- only its own `entity_id`, per `assemble.py::_relationship_entries` -- so Y's
    name only ever showed up by prose-mention luck, missing it entirely for 2 confirmed real
    violations), while dropping the name requirement let an unrelated word coincidentally
    appearing elsewhere in prose (Lawrence's own Background says "apprenticing under a relative",
    about someone else entirely) falsely trip every "is not a relative of ANYONE Lawrence has any
    real relationship with" check. `_relationship_entry_keywords` fixes both: check each rendered
    relationship/affiliation entry ON ITS OWN (entity_id + predicate + label/note/blurb, never
    pooled with unrelated prose or unrelated entries) for full coverage of the fact's keywords
    minus the page's own subject name (tautological -- of course a page is "about" its own
    subject). A negative fact is `contradicted` only when ONE SPECIFIC entry supplies both the
    right other party AND the right relationship word together -- exactly the shape a real
    violation has, and exactly what a same-page-different-relationship or an unrelated prose
    word cannot fake. Verified against a real run: correctly flags both genuine violations that
    run had and clears all 15 same-page/coincidental-word false positives it also had."""
    page_kw = _keywords(_page_text(page))

    positive_hits = 0
    for fact in facts:
        if not fact.in_scope:
            continue
        fact_kw = _keywords(fact.text)
        if fact_kw and len(fact_kw & page_kw) >= max(2, round(len(fact_kw) * 0.5)):
            positive_hits += 1

    subject_kw = _keywords(page.get("canonical") or "")
    entry_kws = _relationship_entry_keywords(page)
    contradictions: list[dict[str, Any]] = []
    for text in negative_facts:
        fact_kw = _keywords(text)
        if not fact_kw:
            continue
        required = fact_kw - subject_kw or fact_kw
        contradicted = any(required <= entry_kw for entry_kw in entry_kws)
        matched = sorted(fact_kw & page_kw)
        contradictions.append({"text": text, "contradicted": contradicted, "matched_keywords": matched})

    contradicted_count = sum(c["contradicted"] for c in contradictions)
    denominator = positive_hits + contradicted_count
    precision = positive_hits / denominator if denominator else None
    return {
        "precision": precision,
        "supported": positive_hits,
        "contradicted": contradicted_count,
        "negative_facts": contradictions,
    }


def _load_paragraph_texts(para_ids: set[str]) -> dict[str, str]:
    """`{para_id: text}` for exactly the paragraphs named — opens only the volume files those
    para_ids actually reference (`v{NN}:...` prefix), never the whole corpus."""
    vols_needed = {int(pid.split(":", 1)[0][1:]) for pid in para_ids}
    by_id: dict[str, str] = {}
    for vol in vols_needed:
        path = paths.parsed_volume(vol)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["para_id"] in para_ids:
                by_id[record["para_id"]] = record["text"]
    return by_id


def _core_para_ids_for(events_conn: sqlite3.Connection, entity_id: str, vol: int) -> set[str]:
    """Union of `core_para_ids` (`graph/events.py`) across every event `entity_id` participates in
    through cutoff `vol` — the real, chapter-scale grounding for that character's `beat_summary`
    lines, deliberately never written into the live page's own `evidence` field (see
    `synth/prose.py::generate_chronology`'s docstring)."""
    from ..graph.events import character_events_at

    out: set[str] = set()
    for row in character_events_at(events_conn, entity_id, vol):
        out.update(json.loads(row["core_para_ids_json"] or "[]"))
    return out


def score_citation_entailment(
    page: dict[str, Any],
    *,
    events_conn: sqlite3.Connection | None = None,
    entity_id: str | None = None,
    vol: int | None = None,
    events_source_keys: set[str] | None = None,
) -> dict[str, Any]:
    """For every `kind: prose` section with text, checks its cited paragraphs (`evidence`, a
    para_id list — CONTRACTS §5) actually exist on disk (a hard, exact check: a dangling citation
    is a real bug, not a heuristic near-miss) and that the section's text shares vocabulary with
    the UNION of those paragraphs' own text (a soft heuristic — module docstring). Needs no gold
    data: this is a self-grounding check on the page's own citations, not fact-checking against an
    external source.

    When `events_conn`/`entity_id`/`vol` are given, a section whose key is in
    `events_source_keys` (the `page_outline` entries with `source: "events"`, i.e. chronology)
    ALSO gets checked against `_core_para_ids_for`'s broader paragraph set — module docstring's
    "For a `source: 'events'` section" note. This can only turn a `weak overlap` verdict into
    `grounded`; it never affects `dangling_citations` (still computed from the page's own stored
    `evidence` only — a citation that doesn't exist on disk is a real bug regardless)."""
    sections = []
    all_para_ids: set[str] = set()
    for key, section in (page.get("prose") or {}).items():
        if not section or not section.get("text"):
            continue
        all_para_ids.update(section.get("evidence", []))
    para_text_by_id = _load_paragraph_texts(all_para_ids)

    core_para_ids: set[str] = set()
    if events_conn is not None and entity_id is not None and vol is not None:
        core_para_ids = _core_para_ids_for(events_conn, entity_id, vol)
    core_para_text_by_id = _load_paragraph_texts(core_para_ids) if core_para_ids else {}

    for key, section in (page.get("prose") or {}).items():
        if not section or not section.get("text"):
            continue
        evidence = section.get("evidence", [])
        missing = [pid for pid in evidence if pid not in para_text_by_id]
        cited_text = " ".join(para_text_by_id[pid] for pid in evidence if pid in para_text_by_id)
        text_kw = _keywords(section["text"])
        cited_kw = _keywords(cited_text)
        overlap = sorted(text_kw & cited_kw)
        grounded = bool(text_kw) and len(overlap) >= max(1, round(len(text_kw) * 0.3))

        beat_summary_checked = bool(core_para_text_by_id) and key in (events_source_keys or set())
        beat_summary_grounded = False
        if beat_summary_checked:
            beat_text = " ".join(core_para_text_by_id.values())
            beat_overlap = text_kw & _keywords(beat_text)
            beat_summary_grounded = bool(text_kw) and len(beat_overlap) >= max(1, round(len(text_kw) * 0.3))

        sections.append(
            {
                "section": key,
                "evidence_count": len(evidence),
                "dangling_citations": missing,
                "grounded": (grounded or beat_summary_grounded) if not missing else False,
                "overlap_keywords": overlap,
                "beat_summary_checked": beat_summary_checked,
                "beat_summary_para_count": len(core_para_text_by_id) if beat_summary_checked else 0,
                "beat_summary_grounded": beat_summary_grounded,
            }
        )
    return {"sections": sections}
