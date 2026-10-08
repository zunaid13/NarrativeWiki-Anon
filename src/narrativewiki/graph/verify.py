"""[22 C3] Verification pass: one cheap LLM call per FACT, checking whether the facts already
assembled for a page are actually supported by their own cited evidence -- the backstop for errors
that survive extraction, not a bigger extraction pass (docs/vision/PHASE_22.md TODO).

It was one call per character until 2026-09-24, with the facts sent as a numbered list and the
model answering with `fact_index` values. Two defects came out of that batching, both measured
(MEASUREMENTS section 28). Judgements bled between facts in one call: `norah-arendt COMMANDS enek`
was withheld with the rationale "The evidence describes Enek following orders, not Norah commanding
Liebert" -- a blend of two adjacent COMMANDS facts, withholding a true fact (a shepherdess does
command her sheepdog) on an argument about a different pair. And an in-range but wrong `fact_index`
withheld the wrong fact with nothing able to detect it. One fact per call removes the index
entirely, so there is no longer an attribution to get wrong, and it makes the re-verification unit
match the caching unit: `fact_context_sha256` fingerprints one fact, so only facts whose own
evidence moved are re-asked. A relation checked from both endpoints now produces the identical
prompt from each side, so it answers once and the second side is a cache hit. Named specifically to catch three failure patterns prior audits found (and B2/A3 already
partly mitigate upstream, but cannot guarantee eliminated): a fact hallucinated about the wrong
entity ("phantom Kraft" -- two aliases of one person read as two people), a relationship claim the
evidence doesn't actually support ("Medio membership" -- an ENEMY_OF/captured-by passage misread
as membership), and (Phase 23 Part F, 2026-09-11, live against the re-run's own gold-eval FAIL) a
rhetorical self-introduction or boastful name invocation misread as a literal family claim -- e.g.
"I'm Kraft Lawrence, son of the great Jakob Tarantino" produced a real CHILD_OF edge even though
PARENT_OF/CHILD_OF's own extraction-time guardrail note already names this exact anti-pattern
("son of so-and-so" as a claimed/false identity, not evidence of real parentage) -- the note alone
did not stop it, so verify now names it too as a second line of defense.

Inputs:     One character's `synth/assemble.py::fact_set_for_verification()` output (predicate/
            value/qualifier + up to N evidence quotes per fact), an LLMClient (the `verify` role),
            and optional cutoff-safe nearby paragraphs from EvidenceContext.
Outputs:    `verify_facts()` -> the flagged subset of the facts given, in input order (empty
            when nothing looked wrong, or there was nothing to check). `wiki verify` (cli.py)
            collects these per character into `data/04_graph/verification.json` (CONTRACTS §4.3).
Invariants: - Never mutates a claim, interval, or page. A flagged fact stays exactly where it was
            -- same "never silently drop either side" discipline `contradictions.json`'s `flag`
            resolution already established (CLAUDE.md §2). The CLI persists binding verdicts
            separately; unsupported intervals are withheld by subsequent rendering.
            - No facts, or no reachable model -> no call, no verdict. Verification that cannot run
            degrades to "skipped", never to a fabricated pass/fail.
Contract:   docs/CONTRACTS.md §4.3; docs/PROMPTS.md `verify_facts`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from .. import paths

STAGE = "verify"

# Bump when the verification METHOD changes -- the prompt, the schema, or what gets sent with a
# fact. `fact_context_sha256` fingerprints the fact, which is not enough on its own: the facts were
# untouched when this pass went from one batched call per character to one call per fact, so every
# stored verdict would have been carried forward and the judgements of the method being replaced
# would have survived it. `wiki verify` reuses a verdict only when this matches too, so changing
# the method re-verifies exactly once and future changes cost one line here.
# [32] v4: each fact carries its predicate's definition, and the whole line (label and
# description) must be supported. Hand-read precision of the v1-2 wiki was 33/40, every miss a
# relation the verifier judged in the everyday sense ("Friend of" for a business contact).
METHOD_REVISION = "per-fact-v5-definitions"  # v5: MOTIVATION/SKILL/FEAR defined

_SYSTEM_PROMPT = """You are fact-checking one entry on a character's page on a wiki generated from a \
novel series. You will be shown a single fact extracted about this character, together with \
the exact quoted evidence it was extracted from. Three known failure patterns to watch for: (1) a \
fact that actually belongs to a DIFFERENT character whose name is similar or who appears in the \
same passage (two people conflated as one); (2) a relationship or membership claim the quoted \
evidence does not actually support (for example, evidence describing an enemy or a captor does not \
support "member of" or "friend of"); (3) a family relationship (parent/child/sibling/spouse/relative) \
whose "evidence" is really a boast, a joke, a false identity given to a stranger, or someone \
invoking a respected name for effect (e.g. "I'm X, son of the great Y" as bravado or name-dropping, \
not a literal claim of parentage) -- treat this the same as pattern (2): the quoted line does not \
actually support the relationship. Judge the fact ONLY against its OWN quoted evidence: never \
reject it because it is surprising, or because you know the story from outside \
sources. Judge only the fact shown. The character may appear under any of the names listed after \
"also called": evidence about any of those names is evidence about this character, not about \
someone else. For a GENDER fact, a gendered pronoun or noun that clearly refers to the character \
("she", "his", "the girl") is sufficient evidence. Nearby paragraphs, when supplied, are context \
for the quote, not \
additional cited evidence. Use them to distinguish literal statements from banter, figurative \
kinship, and false identities. Base your judgement solely on the supplied quote and context. \
When the fact comes with a definition of its label, judge it by that definition: the evidence must \
show that specific relationship or quality (a friendship, a mentorship, a membership), not merely a \
meeting, a business dealing, a single helpful act, being in the same scene, or being paid by someone. \
The description in parentheses must be supported too; if the label holds but the description does \
not, the fact is not supported."""


class EvidenceContext:
    """Lazy, per-run parsed-volume cache for bounded context around cited evidence.

    Reads only volumes <= upto_vol and never crosses chapter boundaries. Missing legacy parsed
    files/paragraphs leave the quote-only prompt intact. The radius defaults to four paragraphs
    on either side, matching extraction's existing context window; no model-generated fields.
    """

    def __init__(self, upto_vol: int, radius: int = 4) -> None:
        if upto_vol < 1 or radius < 0:
            raise ValueError("Verification cutoff must be positive and context radius nonnegative")
        self.upto_vol = upto_vol
        self.radius = radius
        self._volumes: dict[int, tuple[list[dict], dict[str, int]]] = {}

    def for_facts(self, facts: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        contexts = {}
        for fact in facts:
            for evidence in fact.get("evidence", []):
                para_id = evidence.get("para_id", "")
                match = re.fullmatch(r"v(\d+):c(\d+):p(\d+)", para_id)
                if not match or para_id in contexts:
                    continue
                vol = int(match[1])
                if not 1 <= vol <= self.upto_vol:
                    continue
                if vol not in self._volumes:
                    path = paths.parsed_volume(vol)
                    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                            if line.strip()] if path.is_file() else []
                    # Defend against a misplaced future-volume row inside an older volume file.
                    rows = [r for r in rows if r.get("vol") == vol]
                    self._volumes[vol] = (rows, {r["para_id"]: i for i, r in enumerate(rows)})
                rows, indices = self._volumes[vol]
                if para_id not in indices:
                    continue
                i = indices[para_id]
                chapter_prefix = para_id.rsplit(":", 1)[0] + ":"
                contexts[para_id] = [
                    {"para_id": row["para_id"], "text": row["text"]}
                    for row in rows[max(0, i - self.radius):i + self.radius + 1]
                    if row["para_id"].startswith(chapter_prefix)
                ]
        return contexts


class FactVerdict(BaseModel):
    """One fact's judgement. No index: the call is about a single fact, so there is nothing to
    mis-address. That is the whole point of the 2026-09-24 change (see module docstring)."""

    supported: bool
    rationale: str = Field("", max_length=300)

    @model_validator(mode="before")
    @classmethod
    def _accept_what_models_actually_return(cls, data: Any) -> Any:
        """Coerce the shapes real models answer a yes/no question with.

        The lesson kept from the per-character schema this replaces: that one defaulted `flags` to
        empty, so a model answering in the wrong shape reported "nothing flagged" and 49 real flags
        vanished with no error raised anywhere. A verification pass that fails OPEN is worse than
        one that crashes. So `supported` has NO default -- an answer this cannot read fails
        validation, gets the client's repair-retry, and ultimately raises, rather than quietly
        passing every fact.
        """
        if isinstance(data, bool):
            return {"supported": data}
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for alias in ("verdict", "is_supported", "supported_by_evidence", "answer"):
            if "supported" not in out and alias in out:
                out["supported"] = out.pop(alias)
        raw = out.get("supported")
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in ("supported", "yes", "true", "y"):
                out["supported"] = True
            elif token in ("unsupported", "no", "false", "n", "not supported"):
                out["supported"] = False
        for alias in ("reason", "explanation", "justification"):
            if not out.get("rationale") and out.get(alias):
                out["rationale"] = out[alias]
        if isinstance(out.get("rationale"), str) and len(out["rationale"]) > 300:
            out["rationale"] = out["rationale"][:300]   # a long answer is not a failed one
        return out


class JSONClient(Protocol):
    def complete_json(
        self, stage: str, prompt: str, schema: type[BaseModel], system: str | None = None
    ) -> BaseModel: ...


def _format_claim(fact: dict[str, Any]) -> str:
    qualifier = f" ({fact['qualifier']})" if fact.get("qualifier") else ""
    return f'{fact["predicate"]}{qualifier} = "{fact["value"]}"'


def attach_pair_siblings(facts: list[dict[str, Any]]) -> None:
    """Give each relation fact the OTHER relations recorded between the same two entities, as bare
    claims (no quotes), under `pair_siblings`.

    MEASUREMENTS section 30: going per-fact released 16 relations, all of them relations, because a
    relation judged alone no longer saw what else is asserted about the pair -- the batched call had
    supplied that by accident. This restores exactly that context and nothing wider: the pair's own
    relations, not the character's whole fact list. `relations_at` returns the same pair set from
    either endpoint, and the list is sorted, so the prompt stays identical from both sides."""
    by_pair: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("pair"):
            by_pair.setdefault(tuple(fact["pair"]), []).append(fact)
    for group in by_pair.values():
        for fact in group:
            fact["pair_siblings"] = sorted(_format_claim(f) for f in group if f is not fact)


def _format_fact(fact: dict[str, Any]) -> str:
    quotes = "; ".join(f'[{ev["para_id"]}] "{ev["quote"]}"' for ev in fact["evidence"]) or "(no quoted evidence)"
    definition = f"\nDefinition of {fact['predicate']}: {fact['definition']}" if fact.get("definition") else ""
    return f"{_format_claim(fact)} -- evidence: {quotes}{definition}"


def attach_definitions(facts: list[dict[str, Any]], settings: Any) -> None:
    """[32] Give each fact the `note` its predicate has in config/extraction.yaml, the same
    definition the extraction prompt used, so the verifier judges the label the extractor was
    asked for rather than its everyday sense."""
    notes = {p: cfg.get("note") for group in (settings.attributes, settings.relations, settings.traits)
             for p, cfg in group.items() if cfg.get("note")}
    for fact in facts:
        if fact["predicate"] in notes:
            fact["definition"] = notes[fact["predicate"]]


def fact_context_sha256(fact: dict[str, Any]) -> str:
    """Fingerprint exactly what this fact's verdict was judged on: its identity and its cited
    evidence, nothing else.

    A verdict is only reusable across a rebuild if the thing it judged has not moved, and
    `interval_id` alone does not establish that — it is stable while a fact's evidence set grows or
    shrinks underneath it. Evidence is sorted, so a reordering of equally-cited quotes does not
    look like a change; `_format_fact` renders predicate, qualifier, value and quotes, so those are
    what is hashed. Anything not in this hash must not change the verdict, and anything that can
    change the verdict must be in it — the nearby-paragraph context is deliberately excluded, as it
    is derived from the same para_ids and adds no independent state.
    """
    payload = json.dumps(
        {
            "kind": fact.get("kind"),
            "predicate": fact.get("predicate"),
            "value": fact.get("value"),
            "qualifier": fact.get("qualifier"),
            "evidence": sorted(
                (e["para_id"], e["quote"]) for e in (fact.get("evidence") or [])
            ),
            # Siblings are in the prompt, so they can move the verdict (attach_pair_siblings).
            "pair_siblings": fact.get("pair_siblings") or [],
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _user_prompt(
    canonical: str, fact: dict[str, Any],
    context: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    """One fact, its own evidence, and only the nearby paragraphs belonging to that evidence.

    Scoping the context to this fact is what makes the prompt identical from either endpoint of a
    relation, so a relation checked from both sides answers once and the second side is a cache hit
    instead of an independent second judgement that then had to be reconciled."""
    context_lines = []
    seen_context = set()
    for ev in fact.get("evidence") or []:
        for para in (context or {}).get(ev["para_id"], []):
            if para["para_id"] not in seen_context:
                context_lines.append(f"[{para['para_id']}] {para['text']}")
                seen_context.add(para["para_id"])
    if context_lines:
        context_lines.insert(0, "Nearby paragraph context (interpretation only, not additional citations):")
    context_text = "\n\n" + "\n".join(context_lines) if context_lines else ""
    siblings = fact.get("pair_siblings") or []
    sibling_text = (
        "\n\nOther relations recorded between the same two characters (context only, not being "
        "judged here):\n" + "\n".join(f"- {s}" for s in siblings)
    ) if siblings else ""
    return (
        f"Character: {canonical}\n\nFact: {_format_fact(fact)}{sibling_text}{context_text}\n\n"
        "Does the quoted evidence actually support this fact? If it does not, give a one-sentence "
        "rationale citing what the evidence actually shows.\n\n"
        "Respond with JSON only, shaped exactly like this:\n"
        '{"supported": false, "rationale": "The quote shows Shin hiring the man, not being '
        'related to him."}\n\n'
        'Return {"supported": true} if the evidence supports the fact.'
    )


def subject_label(entity: dict[str, Any], upto_vol: int, limit: int = 8) -> str:
    """The character's name as the verifier sees it: canonical plus the aliases visible at this
    cutoff. [30] With the canonical name alone the verifier withheld true facts about an alias --
    Ainz's armour "describes the adventurer 'Momon', a persona Ainz uses, rather than Ainz" --
    which matters for any series whose people carry more than one name."""
    aliases = list(dict.fromkeys(
        sf["text"] for sf in entity.get("surface_forms") or []
        if sf["text"] != entity["canonical"] and (sf.get("first_vol") or 1) <= upto_vol
    ))[:limit]
    return entity["canonical"] + (f" (also called: {', '.join(aliases)})" if aliases else "")


def verify_fact(
    canonical: str, fact: dict[str, Any], client: JSONClient | None,
    *, context: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any] | None:
    """One `verify` call for one fact. Returns the fact plus its `rationale` when the evidence does
    not support it, or `None` when it does (or when `client` is None -- no reachable model degrades
    to skipped, never to a fabricated pass/fail)."""
    if client is None:
        return None
    verdict = client.complete_json(
        STAGE, _user_prompt(canonical, fact, context), FactVerdict, system=_SYSTEM_PROMPT
    )
    if verdict.supported:
        return None
    return {**fact, "rationale": verdict.rationale or "(no rationale given)"}


def verify_facts(
    canonical: str, facts: list[dict[str, Any]], client: JSONClient | None,
    *, context: dict[str, list[dict[str, str]]] | None = None,
    workers: int = 1,
    on_result: Callable[[int, dict[str, Any], dict[str, Any] | None], None] | None = None,
) -> list[dict[str, Any]]:
    """One call per fact, up to `workers` in flight, returning the flagged subset in input order.

    Replaces the former `verify_character`, which sent a numbered list in a single call and had the
    model answer with `fact_index` values; the module docstring records what that cost.
    `on_result(index, fact, flagged_or_none)` mirrors `llm/parallel.py::map_calls`, so a caller can
    report progress while calls overlap."""
    if not facts or client is None:
        return []
    from ..llm.parallel import map_calls

    results = map_calls(
        lambda fact: verify_fact(canonical, fact, client, context=context),
        facts, workers, on_result=on_result,
    )
    return [r for r in results if r is not None]
