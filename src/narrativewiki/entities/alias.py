"""[2] Alias clustering: which surface forms name the same entity? CONTRACTS section 2.1.

Inputs:     Classified candidates from `entities/classify.py` (each has `type`), a
            para_id -> text lookup for evidence, and an LLMClient.
Outputs:    A list of entity dicts ready for `entities/gazetteer.py::build_gazetteer` — every
            field of CONTRACTS section 2.1 except `importance` (computed once corpus-wide,
            after clustering, by `gazetteer.compute_importance`).
Invariants: - Merging is tiered from cheap-and-certain to expensive-and-uncertain (PROMPTS.md
              "alias_same_person" notes): exact match, then fuzzy match, then whole-word subset,
              then — only for what remains ambiguous — one LLM call per pair.
            - The LLM is told that leaving two forms separate is the safer error. A wrong merge
              silently mixes two people's facts on one page; a wrong split leaves two pages.
            - Nothing here is per series (Phase 32, req. 8). The hand `entity_overrides` that
              used to patch the result are gold now (`docs/eval/roster/`), scored by
              `scripts/eval/roster_gold.py`.
Contract:   docs/CONTRACTS.md section 2.1; docs/PROMPTS.md `alias_same_person` notes.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from unidecode import unidecode

from ..llm.parallel import map_calls, workers_for
from .classify import JSONClient  # re-exported protocol; same shape needed here
from .candidates import DEFAULT_HONORIFICS
from .gazetteer import slugify

STAGE = "entity_alias"

# Tier thresholds. Tiers a-c are free and deterministic; tier d spends one LLM call per pair,
# capped below, since an O(n^2) sweep over hundreds of candidates would otherwise be unbounded.
FUZZY_MERGE_THRESHOLD = 90       # tier b: auto-merge above this (typo/transliteration variants)
FUZZY_MAYBE_LOW = 55              # tier d: below this, never even worth an LLM call
LLM_MERGE_CONFIDENCE = 0.7        # tier d: only merge when the model is this sure
MAX_LLM_PAIRS = 5000              # hard cap on alias_same_person calls in one gazetteer build
# [32] 500 -> 1000: Middlemarch (556 pairs) cut 56, and the cut tail is exactly the low-fuzzy,
# non-co-occurring nickname pairs ("Rosy"/"Rosamond"). A call costs ~$0.0001.
# [34] 1000 -> 5000: Anne's v1-5 build had 2,116 pairs and never asked 1,116 of them, among them
# married names ("Diana Barry"/"Diana Wright"); asking all costs about $0.12.

# A capitalised run's regex (candidates.py) has no notion of a sentence boundary, so a sentence-
# initial function word directly before a name ("As Lena walked...", "And Kiriya said...") mines
# as its own 2-word candidate, "As Lena" / "And Kiriya", alongside the bare "Lena" / "Kiriya" that
# also gets mined everywhere else that name appears mid-sentence. Both land in the same cluster
# (they share every non-leading token), but `canonical_key`'s "most tokens wins" rule would then
# hand the ugly, artifact-bearing form the canonical name purely for being longer — real evidence
# from a v01-04 run: "As Lena", "And Kiriya", "And Anju" all outranked the correct bare name this
# way. Stripped for SCORING only (never removed from `surfaces`, so it stays a harmless indexed
# alias) so a real longer name ("Shinei Nouzen" over "Shin") is untouched: neither token is in
# this set, so its length is never discounted.
_CANONICAL_LEADING_STOPWORDS = {
    "a", "an", "the", "and", "but", "or", "nor", "so", "yet", "as", "if", "when", "while",
    "because", "since", "after", "before", "though", "although", "than", "that", "then",
    # [32] Adjectives a narrator puts before a name: Middlemarch's page was titled "Poor Dorothea".
    # Not "great"/"big": those begin real names ("Great Tomb of Nazarick").
    "poor", "old", "young", "little", "dear",
    # [33] A question or exclamation opens with a verb: Overlord's "Is Albedo" (3) outranked
    # "Albedo" (421) by being two words long. Not "will"/"may": those begin names (Will Ladislaw).
    "is", "was", "are", "were", "has", "had", "have", "does", "did", "do", "can", "could", "would",
    "shall", "should", "might", "must", "where", "what", "who", "why", "how", "oh", "ah", "yes", "no",
    "well",
}


_KINSHIP_WORDS = {
    "mom", "mum", "mother", "mama", "ma", "dad", "father", "papa", "pa", "grandma", "grandpa",
    "grandmother", "grandfather", "granny", "uncle", "aunt", "auntie", "brother", "sister",
}


def _canonical_rank(stripped: str) -> int:
    """Token count for canonical-name ranking, discounting one leading sentence-initial
    function word so it can never make an artifact-bearing candidate outrank the clean one."""
    tokens = stripped.split()
    if len(tokens) > 1 and tokens[0].casefold() in _CANONICAL_LEADING_STOPWORDS:
        return len(tokens) - 1
    return len(tokens)

_SYSTEM_PROMPT = """You are deciding whether two surface forms mined from a novel refer to the SAME entity (e.g. a full name and a nickname, a call sign and a real \
name, a title and a bare surname) or to two DIFFERENT entities that merely look similar.

Leaving two different surface forms unmerged is the SAFE error — a human reviewer can merge them \
in five seconds. Merging two different people is the COSTLY error — it silently mixes their \
facts on one page and is hard to catch later. When genuinely unsure, say they are different.

Decide from the quoted passages alone. Anything you may know about this book or its series from \
elsewhere is not evidence: the reader of this page knows only the text so far, and a merge the \
passages do not show can reveal a later twist or join two different people.

Respond with JSON only: {"same_entity": true/false, "confidence": 0.0-1.0, "notes": "short reason"}"""


class AliasDecision(BaseModel):
    same_entity: bool
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


def _strip_honorific(surface: str, honorifics: list[str]) -> str:
    for h in sorted(honorifics, key=len, reverse=True):
        prefix = h + " "
        if surface.startswith(prefix):
            return surface[len(prefix) :].strip()
    return surface


# [32] The person an honorific implies. Only a different class means a different person:
# Mr./Miss Brooke are uncle and niece, Mrs./Miss Vincy mother and daughter, but Overlord's "Sir
# Momon" and "Lord Momon" are one man, and splitting on them cut the protagonist into five pages.
# Ranks and offices (Dr., Captain, King, Sister) imply no one and are never a conflict.
_HONORIFIC_CLASS = {"mr": "man", "sir": "man", "lord": "man", "master": "man",
                    "mrs": "wife", "madam": "wife", "mistress": "wife", "lady": "wife", "miss": "miss"}


def _honorific_of(surface: str, honorifics: list[str]) -> str | None:
    """The person class of the honorific a surface opens with ("Mrs." -> "wife"), or None."""
    for h in sorted(honorifics, key=len, reverse=True):
        if surface.startswith(h + " "):
            return _HONORIFIC_CLASS.get(h.lower().rstrip("."))
    return None


def _normalize(surface: str, honorifics: list[str]) -> str:
    return unidecode(_strip_honorific(surface, honorifics)).casefold().strip()


def _tokens(normalized: str) -> set[str]:
    return set(normalized.split())


def _user_prompt(a: dict, b: dict, evidence: dict[str, list[str]]) -> str:
    def around(text: str, surface: str) -> str:
        # [33] The passage around the name, not the paragraph's first 200 characters: a long
        # Middlemarch paragraph introduces "Miss Noble, her sister" and "Miss Winifred Farebrother"
        # at offset ~1,100, so the model never saw them and merged the two from memory.
        i = max(text.find(surface), 0)
        lo = max(0, i - 120)
        return text[lo:lo + 300].strip()

    def block(c: dict) -> str:
        paras = list(dict.fromkeys(evidence.get(c["surface"], [])))[:2]
        quotes = "\n".join(f'  - "{around(t, c["surface"])}"' for t in paras)
        return f'"{c["surface"]}" ({c["type"]}, seen {c["count"]} time(s), first vol {c["first_vol"]}):\n{quotes}'

    return f"Form A: {block(a)}\n\nForm B: {block(b)}\n\nAre these the same entity?"


class _UnionFind:
    """[32] `tags` (one honorific or None per member) make a cluster hold at most one distinct
    honorific: "Mr. Brooke" (the uncle) and "Miss Brooke" (Dorothea) strip to the same "Brooke"
    and were fused, and so would Mrs./Miss Vincy or Lady/Sir Chettam. Two explicit, different
    honorifics are two people; every tier, and the LLM step, unions through this check."""

    def __init__(self, n: int, tags: list[str | None] | None = None) -> None:
        self.parent = list(range(n))
        self.tags = [{t} if t else set() for t in (tags or [None] * n)]
        self.bridges: dict[int, list[int]] = {}   # [34] short form -> the incompatible longer forms

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if len(self.tags[ra] | self.tags[rb]) > 1:
            return False
        self.parent[rb] = ra
        self.tags[ra] |= self.tags[rb]
        return True


# [31] Words that address or describe a person without identifying them. Two forms that differ
# only in these ("Aunt Marilla" / "Marilla", "Mistress Shalltear" / "Shalltear") are one person.
_TITLE_WORDS = {
    "mr", "mrs", "ms", "miss", "dr", "doctor", "rev", "reverend", "sir", "lady", "lord", "madam",
    "mistress", "master", "aunt", "uncle", "cousin", "grandma", "grandpa", "granny", "mother",
    "father", "captain", "commander", "major", "colonel", "general", "professor", "president",
    "saint", "king", "queen", "prince", "princess", "old", "young", "little", "small", "big",
}


def _core_tokens(surface: str, honorifics: list[str]) -> list[str] | None:
    """`_name_core` in reading order ("Small Anne Cordelia" -> ["anne", "cordelia"]). [34] In a
    title "X of (the) Y" the words after "of" identify: "Wicked Witch of the West" and "...of the
    East" are two people, so their cores are {wicked, witch, west} and {wicked, witch, east}."""
    words = surface.replace(".", " ").split()
    if "of" in words[1:-1]:
        at = words.index("of")
        words = words[:at] + words[at + 1:][1 if words[at + 1:][:1] == ["the"] else 0:]
    # [34] An article names no one: "The Princess" had the core ["the"] and was handed, as a
    # "bare first name", to "The Wonderful Wizard" for Oz books 4-5.
    while len(words) > 1 and words[0].casefold() in ("the", "a", "an"):
        words = words[1:]
    if any(w[:1].islower() for w in words):
        return None
    titles = _TITLE_WORDS | {h.lower().rstrip(".") for h in honorifics}
    return [unidecode(w).casefold() for w in words if len(w) > 1 and w.lower() not in titles]


def _name_core(surface: str, honorifics: list[str]) -> frozenset[str] | None:
    """The identifying tokens of a name: titles and initials removed. None for an epithet (any
    lowercase word, e.g. "Norah the Nymph", "Great Tomb of Nazarick"): epithets are descriptive
    and never evidence that two names belong to different people."""
    core = _core_tokens(surface, honorifics)
    return None if core is None else frozenset(core)


def _names_with_several_honorifics(paragraphs_by_id: dict[str, str], honorifics: list[str]) -> set[str]:
    """[32] Casefolded names the text uses with two or more different honorifics ("Mr. Brooke" and
    "Miss Brooke"; by `_HONORIFIC_CLASS`, so "Sir"/"Lord" count once). A bare "Brooke" is then
    ambiguous: mined as its own candidate (the "Mr." often splits off in mining), it absorbed "Miss Brooke" -- Dorothea -- by exact match after stripping.
    One regex pass over the corpus."""
    alternation = "|".join(re.escape(h) for h in sorted(honorifics, key=len, reverse=True))
    pat = re.compile(rf"\b({alternation}) ([A-Z][\w'’-]+)")
    seen: dict[str, Counter[str]] = {}
    for text in paragraphs_by_id.values():
        for m in pat.finditer(text):
            cls = _HONORIFIC_CLASS.get(m.group(1).lower().rstrip("."))
            if cls:
                seen.setdefault(unidecode(m.group(2)).casefold(), Counter())[cls] += 1
    # [33] A class used once is a slip, not a second person: Overlord says "Mistress Albedo" 7
    # times and "Miss Albedo" once, and the one "Miss" split her page in two.
    return {name for name, hs in seen.items() if sum(n >= 2 for n in hs.values()) > 1}


def _words_pair_up(a: set[str], b: set[str]) -> bool:
    """[34] A spelling variant changes letters inside a word ("Lawrance"/"Lawrence", ratio 88),
    never one whole word for another: "Wicked Witch of the West"/"...of the East" scored 92 as
    strings and fused two witches ("west"/"east": 75)."""
    only_a, only_b = a - b, b - a
    return (all(any(fuzz.ratio(x, y) >= 80 for y in only_b) for x in only_a)
            and all(any(fuzz.ratio(x, y) >= 80 for x in only_a) for y in only_b))


def _auto_cluster(
    classified: list[dict[str, Any]], honorifics: list[str], ambiguous_bare: set[str] | None = None,
) -> tuple[_UnionFind, list[str], list[tuple[int, int, bool]]]:
    """Tiers a-c (exact normalized match, high fuzzy ratio, whole-word subset — all free), plus
    discovery of the tier-d candidate pairs those tiers left unresolved.

    A pair becomes tier-d-eligible for one of two independent reasons:
    - moderate fuzzy similarity (a spelling variant tiers a-b were not confident enough about), or
    - co-occurrence: the two surfaces were both seen in at least one of the same paragraphs.
      This is the only signal that can catch a true nickname/call-sign pair ("Shin" /
      "Undertaker") — those are never textually similar, so fuzzy matching alone would never
      even propose them as a pair. Evidence overlap only sees the (capped) sample of paragraphs
      kept per candidate, so this is best-effort, not exhaustive; a miss here leaves two pages for one person.
    """
    n = len(classified)
    normalized = [_normalize(c["surface"], honorifics) for c in classified]
    # A bare form of a name the text gives several honorifics carries its own tag, so it can join
    # other bare forms but no honorific form (see _names_with_several_honorifics).
    tags = [_honorific_of(c["surface"], honorifics)
            or ("(bare)" if normalized[i] in (ambiguous_bare or set()) else None)
            for i, c in enumerate(classified)]
    uf = _UnionFind(n, tags)
    token_sets = [_tokens(t) for t in normalized]
    evidence_sets = [set(c.get("evidence_para_ids", [])) for c in classified]

    by_type: dict[str, list[int]] = {}
    for i, c in enumerate(classified):
        by_type.setdefault(c["type"], []).append(i)

    maybe_pairs: list[tuple[int, int, bool]] = []
    subset_pairs: list[tuple[int, int]] = []   # (shorter, longer)

    for indices in by_type.values():
        for a_pos, i in enumerate(indices):
            for j in indices[a_pos + 1 :]:
                if uf.find(i) == uf.find(j):
                    continue
                ni, nj = normalized[i], normalized[j]
                if ni == nj:
                    uf.union(i, j)
                    continue
                ratio = fuzz.ratio(ni, nj)
                ti, tj = token_sets[i], token_sets[j]
                if ratio >= FUZZY_MERGE_THRESHOLD and _words_pair_up(ti, tj):
                    uf.union(i, j)
                    continue
                if ti and tj and (ti <= tj or tj <= ti):
                    subset_pairs.append((i, j) if len(ti) <= len(tj) else (j, i))
                    continue
                co_occurs = bool(evidence_sets[i] & evidence_sets[j])
                # [32] A pet name shares the name's opening letters ("Rosy"/"Rosamond") but not
                # enough of it for the fuzzy ratio; propose it, the model decides.
                nickname_shape = " " not in ni and " " not in nj and min(len(ni), len(nj)) >= 3 and ni[:3] == nj[:3]
                if ratio >= FUZZY_MAYBE_LOW or co_occurs or nickname_shape:
                    maybe_pairs.append((i, j, co_occurs))

    # Tier c, guarded [31]. A shorter form joins the longer forms that contain it only when those
    # longer forms are compatible with each other (one contains the other). A bare "Cuthbert" is a
    # subset of both "Matthew Cuthbert" and "Marilla Cuthbert"; merging it into each fused the two
    # people through union-find (so did "Barry", "Lynde", "Moore", "Ford", "Jim" in a family
    # saga). An ambiguous bridge stays its own entity, and it is not sent to the LLM either: the
    # text uses it for both people, so no answer is right.
    # [34] Nor does it join a full name the text first uses in a LATER volume than the short form:
    # Anne's father "Walter" (v1) joined "Walter Blythe" (v5), Mr. "Marshall" (v1) "Marshall
    # Elliott" (v4), and each early page then described the later man. Such a pair goes to the
    # model, whose evidence is the short form's earliest passages; `_assign_bare_forms` then gives
    # the later volumes' bare mentions to the full name if the model keeps the two apart.
    # The bridge check sees every longer form, later ones included: with only the earlier ones,
    # Anne's "Blythe" (v4) saw just "Gilbert Blythe" and joined him, past Walter/Di/Jem Blythe (v5).
    supersets: dict[int, list[int]] = {}
    for short, long_ in subset_pairs:
        supersets.setdefault(short, []).append(long_)
    cores = [_name_core(c["surface"], honorifics) for c in classified]
    for short, longs in supersets.items():
        compatible = all(cores[a] is None or cores[b] is None or cores[a] <= cores[b] or cores[b] <= cores[a]
                         for x, a in enumerate(longs) for b in longs[x + 1:])
        if compatible:
            for long_ in longs:
                if classified[long_].get("first_vol", 1) > classified[short].get("first_vol", 1):
                    maybe_pairs.append((short, long_, bool(evidence_sets[short] & evidence_sets[long_])))
                else:
                    uf.union(short, long_)
        else:
            uf.bridges[short] = longs   # [34] `_join_resolved_bridges` revisits it after the model

    return uf, normalized, maybe_pairs


def _join_resolved_bridges(classified: list[dict[str, Any]], uf: "_UnionFind") -> None:
    """[34] A bridge was held back because its longer forms looked like different people. When the
    model has since put every one of them in one cluster, they are one person and the reason is
    gone: Oz's bare "Wizard" sat between "Wonderful Wizard" and "Wizard of Oz", both the Wizard, and
    book 1 had two Wizard pages. Never into a cluster whose longer forms all come later."""
    for short, longs in uf.bridges.items():
        roots = {uf.find(long_) for long_ in longs}
        if len(roots) == 1 and any(classified[long_].get("first_vol", 1) <= classified[short].get("first_vol", 1)
                                   for long_ in longs):
            uf.union(short, longs[0])


def _appositive_kinship_pairs(
    classified: list[dict[str, Any]], normalized: list[str], uf: "_UnionFind",
    paragraphs_by_id: dict[str, str], known: list[tuple[int, int, bool]],
) -> list[tuple[int, int, bool]]:
    """[32] A first-person narrator names a parent once ("my mom, Effa") and says "Mom" hundreds
    of times after. The two forms share no letters and rarely share a sampled evidence paragraph,
    so neither tier-d signal proposes them. An appositive in the text does: "<kin>, <Name>" or
    "<Name>, my <kin>". The pair goes to the same LLM adjudication as any other, with that
    paragraph first in both forms' evidence. One regex per kinship candidate, one corpus pass."""
    names = {normalized[j]: j for j, c in enumerate(classified)
             if c["type"] == "CHARACTER" and normalized[j] not in _KINSHIP_WORDS}
    seen = {(min(i, j), max(i, j)) for i, j, _ in known}
    cap = r"([A-Z][\w’'-]+(?: [A-Z][\w’'-]+)*)"
    out: list[tuple[int, int, bool]] = []
    for i, c in enumerate(classified):
        if c["type"] != "CHARACTER" or normalized[i] not in _KINSHIP_WORDS:
            continue
        kin = re.escape(normalized[i])
        pat = re.compile(rf"\b(?i:{kin}),\s+{cap}|{cap},\s+(?:my|our|his|her|their)\s+(?i:{kin})\b")
        for pid, text in paragraphs_by_id.items():
            for m in pat.finditer(text):
                j = names.get(unidecode(m.group(1) or m.group(2)).casefold())
                if j is None or uf.find(i) == uf.find(j) or (min(i, j), max(i, j)) in seen:
                    continue
                seen.add((min(i, j), max(i, j)))
                for k in (i, j):
                    classified[k]["evidence_para_ids"] = [pid, *classified[k].get("evidence_para_ids", [])]
                out.append((i, j, True))
    return out


_RENAME_CUE = re.compile(
    r"\b(?:chang(?:e|ed|ing) (?:my|his|her|their) name|return(?:ed)? to being|(?:real|true|old|former|"
    r"new|other) name|formerly (?:known as|called)|went by the name|used to be called|now called)\b", re.I)


def _rename_pairs(
    classified: list[dict[str, Any]], normalized: list[str], uf: "_UnionFind",
    paragraphs_by_id: dict[str, str], known: list[tuple[int, int, bool]],
) -> list[tuple[int, int, bool]]:
    """[33] A character who renames himself ("I changed my name... call me Ainz"; later "I'll
    gladly return to being Momonga") is two names that share no letters, and a sampled evidence
    paragraph rarely holds both: Overlord's protagonist was two pages. A paragraph with a rename
    cue proposes every pair of character names in it, that paragraph first in both forms'
    evidence; the model still decides."""
    names = [(j, re.compile(rf"\b{re.escape(c['surface'])}\b")) for j, c in enumerate(classified)
             if c["type"] == "CHARACTER"]
    seen = {(min(i, j), max(i, j)) for i, j, _ in known}
    out: list[tuple[int, int, bool]] = []
    for pid, text in paragraphs_by_id.items():
        if not _RENAME_CUE.search(text):
            continue
        present = [j for j, pat in names if pat.search(text)]
        for x, i in enumerate(present):
            for j in present[x + 1:]:
                key = (min(i, j), max(i, j))
                if uf.find(i) == uf.find(j) or key in seen:
                    continue
                seen.add(key)
                for k in (i, j):
                    classified[k]["evidence_para_ids"] = [pid, *classified[k].get("evidence_para_ids", [])]
                out.append((i, j, True))
    return out


def _resolve_maybe_pairs(
    classified: list[dict[str, Any]],
    uf: _UnionFind,
    normalized: list[str],
    maybe_pairs: list[tuple[int, int, bool]],
    evidence_by_surface: dict[str, list[str]],
    client: JSONClient,
    max_llm_pairs: int = MAX_LLM_PAIRS,
    on_pairs_capped: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Tier d: ask the model about every pair `_auto_cluster` could not resolve on its own.

    `max_llm_pairs` caps how many of `maybe_pairs` actually get an LLM call -- see `cluster_candidates`
    for where this now comes from (`entities.max_llm_alias_pairs` in a series config, defaulting to
    module-level `MAX_LLM_PAIRS`, [24] `docs/vision/plans/0008-pre-full-scale-audit.md` B1: at 86 v1-4
    this cap is already binding, 303 candidates -> 1,533 same-type pairs upper bound, and the tail it
    cuts is exactly the low-fuzzy nickname/call-sign pairs tier d exists to catch). `on_pairs_capped`,
    if given, is called once with `(total_pairs, dropped_count)` before any LLM call is made, so the
    caller can log/report the cap's effect even when this whole loop is a no-op (empty `maybe_pairs`).
    """
    # Co-occurrence pairs first — that signal is the only way to reach a true nickname/call-sign
    # pair, and those are by definition textually dissimilar, so ranking by fuzzy ratio alone
    # would push them to the back of a capped list. Within each group, closest fuzzy match first.
    pairs = sorted(
        maybe_pairs, key=lambda p: (not p[2], -fuzz.ratio(normalized[p[0]], normalized[p[1]]))
    )
    if on_pairs_capped is not None:
        on_pairs_capped(len(pairs), max(0, len(pairs) - max_llm_pairs))
    merge_log: list[dict[str, Any]] = []

    # [32] Ask every pair first, merge after. Merging inside the loop let one "same" verdict
    # override a confident "different" one through union-find: Spice and Wolf's model judged
    # Elsa != Melta (1.0), then "Melta" a contraction of "Miss Elsa" (0.9), and Miss Elsa was
    # already Elsa, so a v5 nun's facts went onto a v4 priest's page. A confident "different"
    # verdict is now a cannot-link constraint no merge may cross, whatever the pair order.
    positives: list[tuple[float, int, int]] = []
    cannot: set[tuple[int, int]] = set()
    # Pairs already one cluster by tiers a-c need no question. Nothing merges while asking, so
    # the calls are independent: [34] they run `concurrency` at a time (the cap went to 5000 and
    # one-at-a-time took hours), with the same answers in the same order as the serial loop.
    asked = [(i, j) for i, j, _co_occurs in pairs[:max_llm_pairs] if uf.find(i) != uf.find(j)]
    decisions = map_calls(
        lambda pair: client.complete_json(
            STAGE, _user_prompt(classified[pair[0]], classified[pair[1]], evidence_by_surface),
            AliasDecision, system=_SYSTEM_PROMPT),
        asked, workers_for(client, STAGE))
    for (i, j), decision in zip(asked, decisions):
        if decision.confidence >= LLM_MERGE_CONFIDENCE:
            if decision.same_entity:
                positives.append((decision.confidence, i, j))
            else:
                cannot.add((min(i, j), max(i, j)))

    def members(root: int) -> list[int]:
        return [k for k in range(len(classified)) if uf.find(k) == root]

    for confidence, i, j in sorted(positives, key=lambda p: -p[0]):
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            continue
        a, b = members(ri), members(rj)
        if any((min(x, y), max(x, y)) in cannot for x in a for y in b):
            continue
        if uf.union(i, j):
            merge_log.append({"a": classified[i]["surface"], "b": classified[j]["surface"], "confidence": confidence})
    return merge_log


def _pick_canonical(members: list[dict[str, Any]], honorifics: list[str]) -> dict[str, Any]:
    # Canonical: most tokens in its honorific-stripped form, then highest count. A leading
    # sentence-initial function word ("As Lena", "And Kiriya") is discounted from the token
    # count so a mining artifact can't outrank the clean name just by being longer.
    def canonical_key(c: dict[str, Any]) -> tuple[int, int]:
        stripped = _strip_honorific(c["surface"], honorifics)
        return (_canonical_rank(stripped), c["count"])

    # [32] A cluster is named from its first volume only: a page is titled by its canonical
    # name at every cutoff, so a later name is a spoiler (Overlord's v1 "Wise King of the
    # Forest" is named Hamusuke in v2). Among those, the name the text uses most, in its
    # fullest form: "Lawrence" -> "Kraft Lawrence", but Myne is not titled by her past
    # life's "Urano Motosu", which never contains "Myne". These replaced per-series renames.
    first = min(m["first_vol"] for m in members)
    earliest = [m for m in members if m["first_vol"] == first]
    # A first-person narrator's "Dad" outnumbers "Gunther", but a kinship word is a relation,
    # not a name: it names the cluster only when no proper name is in it.
    earliest = [m for m in earliest if _normalize(m["surface"], honorifics) not in _KINSHIP_WORDS] or earliest
    top = max(earliest, key=lambda c: c["count"])
    top_tokens = set(_normalize(top["surface"], honorifics).split())
    fuller = [c for c in earliest if top_tokens <= set(_normalize(c["surface"], honorifics).split())]
    canonical_member = max(fuller, key=canonical_key)
    return canonical_member


def _build_clusters(
    classified: list[dict[str, Any]], uf: _UnionFind, honorifics: list[str], llm_merges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[int, list[int]] = {}
    for i in range(len(classified)):
        groups.setdefault(uf.find(i), []).append(i)

    entities: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    # [32] Clusters kept apart only by their honorifics ("Mr. Brooke", "Miss Brooke") would all be
    # named "Brooke". Those keep the honorific, in the name and in their surface forms, so each
    # page has its own title and its own mentions.
    stripped_names = Counter(
        (_strip_honorific(_pick_canonical([classified[i] for i in idx], honorifics)["surface"], honorifics).casefold(),
         classified[idx[0]]["type"])
        for idx in groups.values()
    )
    for members_idx in groups.values():
        members = [classified[i] for i in members_idx]
        canonical_member = _pick_canonical(members, honorifics)
        canonical = _strip_honorific(canonical_member["surface"], honorifics)
        entity_type = canonical_member["type"]
        keep_honorific = stripped_names[(canonical.casefold(), entity_type)] > 1

        def strip(x: str, _keep=keep_honorific, _own=canonical.casefold(), _type=entity_type) -> str:
            # An honorific form whose bare name is ANOTHER cluster's name keeps the honorific:
            # Dorothea's "Miss Brooke" must not become an alias "Brooke", her uncle's page.
            bare = _strip_honorific(x, honorifics)
            if _keep or (bare != x and bare.casefold() != _own and stripped_names[(bare.casefold(), _type)]):
                return x
            return bare
        canonical = strip(canonical_member["surface"])

        surfaces: dict[str, dict[str, Any]] = {}  # stripped text -> {first_vol, count}
        for m in members:
            stripped = strip(m["surface"])
            source = "corpus" if stripped == m["surface"] else "alias_cluster"
            entry = surfaces.setdefault(
                stripped, {"first_vol": m["first_vol"], "count": 0, "source": source}
            )
            if m["first_vol"] < entry["first_vol"] or (
                m["first_vol"] == entry["first_vol"] and source == "corpus"
            ):
                entry["source"] = source
            entry["first_vol"] = min(entry["first_vol"], m["first_vol"])
            entry["count"] += m["count"]

        # A cluster that needed an LLM tie-break inherits the weakest of those decisions'
        # confidence, so a shaky merge shows up as a shaky entity rather than as a 0.95 default.
        member_surfaces = {m["surface"] for m in members}
        involved_merges = [
            lm for lm in llm_merges if lm["a"] in member_surfaces or lm["b"] in member_surfaces
        ]
        confidence = min((lm["confidence"] for lm in involved_merges), default=0.95)

        # Two clusters of DIFFERENT types can share one canonical name — "Spearhead" the
        # squadron and a hypothetical "Spearhead" piece of tech — and must not collide on
        # entity_id, since `automaton.build_automaton` keys on it.
        base_id = slugify(canonical)
        entity_id = base_id
        suffix = 2
        while entity_id in used_ids:
            entity_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(entity_id)

        entities.append(
            {
                "canonical": canonical,
                "entity_id": entity_id,
                "type": entity_type,
                "aliases": sorted(s for s in surfaces if s != canonical),
                "surface_forms": [
                    {"text": text, "ambiguous": False, "first_vol": info["first_vol"],
                     "source": info["source"]}
                    for text, info in sorted(surfaces.items(), key=lambda kv: kv[1]["first_vol"])
                ],
                "first_vol": min(info["first_vol"] for info in surfaces.values()),
                "mention_count": sum(info["count"] for info in surfaces.values()),
                "confidence": round(confidence, 3),
                "notes": "",
            }
        )
    return entities


# [34] How a volume decides whom a bare first name means: the full names that begin with it must
# be used there at least this often, and the leading one this many times as often as all others.
BARE_FORM_MIN_FULL = 2
BARE_FORM_DOMINANCE = 3
# ...and be used at least this share as often as the bare name in that volume: Anne's book 4 says
# "Small Anne Cordelia" 6 times and "Anne Shirley" twice, but "Anne" 702 times (0.9%); the real
# hand-overs are 3.7% or more (Faith Meredith 15/403, Walter Blythe 10/117, Mary Vance 61/267).
BARE_FORM_MIN_SHARE = 0.02


def _vol_of(para_id: str) -> int | None:
    head = para_id.split(":", 1)[0]
    return int(head[1:]) if head[:1] == "v" and head[1:].isdigit() else None


def _surface_counts_by_vol(entities: list[dict[str, Any]], paragraphs_by_id: dict[str, str]) -> dict[str, Counter[int]]:
    """Mentions of every surface text per volume, longest match first ("Anne" inside "Anne
    Shirley" is not counted), with the same matcher the mention index uses."""
    from .automaton import MentionAutomaton

    auto = MentionAutomaton()
    for text in {sf["text"] for e in entities for sf in e["surface_forms"]}:
        auto.add(text, text)
    auto.build()
    counts: dict[str, Counter[int]] = {}
    for pid, text in paragraphs_by_id.items():
        vol = _vol_of(pid)
        if vol is None:
            continue
        for _start, _end, surface, _id in auto.find_all(text):
            counts.setdefault(surface, Counter())[vol] += 1
    return counts


def _honorific_classes(entity: dict[str, Any], honorifics: list[str]) -> set[str]:
    return {c for sf in entity["surface_forms"] if (c := _honorific_of(sf["text"], honorifics))}


def _assign_bare_forms(
    entities: list[dict[str, Any]], paragraphs_by_id: dict[str, str], honorifics: list[str],
    ambiguous_bare: set[str],
) -> list[dict[str, Any]]:
    """[34] Give a bare first name to the person each volume means by it.

    Clustering keeps a bare name that several full names contain as its own entity (a bridge
    could fuse two people), and that split the Anne series' protagonist: "Anne" (3,928 mentions)
    and "Anne Shirley" (80) were two pages, because "Small Anne Cordelia" also contains "Anne";
    Matthew, Jane and Diana likewise. The other way round, a bare name meaning one person early
    and another later ("Walter": Anne's father in v1, her son in v5) cannot belong to either.

    Per volume, the full names that begin with the bare name vote with their own mentions there;
    one that is used at least `BARE_FORM_MIN_FULL` times and `BARE_FORM_DOMINANCE` times as often
    as the rest takes the volume's bare mentions, and a volume with no such vote keeps the last
    decision (before any, the bare form's own entity). One owner throughout merges the bare
    form's entity into it when the form is most of that entity's mentions (otherwise the form
    alone moves); owners that change split the form by volume (`vols` on each entity's surface
    form, CONTRACTS section 2.1), which the mention index honours and the wikifier never links.
    Surnames are never assigned ("Brooke" usually means Mr. Brooke, not Dorothea Brooke), nor a
    name the text gives several honorifics, nor an epithet, nor a kinship word. Deterministic."""
    chars = [i for i, e in enumerate(entities) if e["type"] == "CHARACTER"]
    if len(chars) < 2:
        return entities
    counts = _surface_counts_by_vol([entities[i] for i in chars], paragraphs_by_id)
    all_vols = sorted({v for c in counts.values() for v in c})
    full_by_first: dict[str, list[tuple[int, str]]] = {}
    for i in chars:
        for sf in entities[i]["surface_forms"]:
            core = _core_tokens(sf["text"], honorifics)
            if core and len(core) >= 2 and sf.get("source") != "epithet_mining":
                full_by_first.setdefault(core[0], []).append((i, sf["text"]))

    merge_into: dict[int, int] = {}
    splits: list[tuple[int, str, dict[int, int]]] = []   # (entity, surface, vol -> owner)
    for i in chars:
        e = entities[i]
        for sf in e["surface_forms"]:
            core = _core_tokens(sf["text"], honorifics)
            if (not core or len(core) != 1 or sf.get("source") == "epithet_mining"
                    or core[0] in _KINSHIP_WORDS or core[0] in ambiguous_bare):
                continue
            full = full_by_first.get(core[0], [])
            if not any(j != i for j, _ in full):
                continue
            owners: dict[int, int] = {}
            current = i
            for vol in sorted(counts.get(sf["text"], {})):
                score: Counter[int] = Counter()
                for j, text in full:
                    score[j] += counts.get(text, Counter())[vol]
                (top, n), *rest = score.most_common() or [(i, 0)]
                if (n >= BARE_FORM_MIN_FULL and n >= BARE_FORM_DOMINANCE * sum(c for _, c in rest)
                        and n >= BARE_FORM_MIN_SHARE * counts[sf["text"]][vol]):
                    current = top
                owners[vol] = current
            targets = set(owners.values())
            if targets <= {i}:
                continue
            bare_mentions = sum(counts.get(sf["text"], Counter()).values())
            all_mentions = sum(sum(counts.get(s["text"], Counter()).values()) for s in e["surface_forms"])
            (only,) = targets if len(targets) == 1 else (None,)
            if (only is not None and 2 * bare_mentions >= all_mentions
                    and not (_honorific_classes(e, honorifics) and _honorific_classes(entities[only], honorifics)
                             and _honorific_classes(e, honorifics).isdisjoint(_honorific_classes(entities[only], honorifics)))):
                merge_into[i] = only
            else:
                splits.append((i, sf["text"], owners))

    def root(k: int) -> int:
        seen = set()
        while k in merge_into and k not in seen:
            seen.add(k)
            k = merge_into[k]
        return k

    for i in list(merge_into):
        target = entities[root(i)]
        source = entities[i]
        have = {sf["text"]: sf for sf in target["surface_forms"]}
        for sf in source["surface_forms"]:
            if sf["text"] in have:
                have[sf["text"]]["first_vol"] = min(have[sf["text"]]["first_vol"], sf["first_vol"])
            else:
                target["surface_forms"].append(dict(sf))
        target["surface_forms"].sort(key=lambda s: s["first_vol"])
        target["mention_count"] += source["mention_count"]
        target["first_vol"] = min(target["first_vol"], source["first_vol"])
        # A page is titled by its canonical name at every cutoff, so the name must not come later than
        # the entity: anne@v2 merged "Dick" (v3) into "Dick Moore" (v4) and kept the later name (and id).
        named_at = next((sf["first_vol"] for sf in target["surface_forms"] if sf["text"] == target["canonical"]), 0)
        if named_at > target["first_vol"]:
            target["canonical"], target["entity_id"] = source["canonical"], source["entity_id"]
        target["confidence"] = min(target["confidence"], source["confidence"])
        target["notes"] = (target.get("notes", "") + f" [34] merged bare-name entity {source['entity_id']}").strip()

    for i, text, owners in splits:
        home = root(i)
        given: dict[int, list[int]] = {}
        for vol, owner in owners.items():
            owner = root(owner)
            if owner != home and not any(sf["text"] == text for sf in entities[owner]["surface_forms"]):
                given.setdefault(owner, []).append(vol)
        if not given:
            continue
        taken = {v for vols in given.values() for v in vols}
        for owner, vols in given.items():
            moved = sum(counts[text][v] for v in vols)
            entities[owner]["surface_forms"].append({
                "text": text, "ambiguous": True, "first_vol": min(vols), "source": "bare_by_volume",
                "vols": sorted(vols)})
            entities[owner]["mention_count"] += moved
            entities[owner]["first_vol"] = min(entities[owner]["first_vol"], min(vols))
            entities[home]["mention_count"] -= moved
        own = entities[home]
        kept = [v for v in all_vols if v not in taken]
        for sf in own["surface_forms"]:
            if sf["text"] == text:
                sf["vols"] = kept
        own["surface_forms"] = [sf for sf in own["surface_forms"] if sf.get("vols", [None]) != []]

    out = [e for k, e in enumerate(entities) if k not in merge_into and e["surface_forms"]]
    for e in out:
        e["aliases"] = sorted({sf["text"] for sf in e["surface_forms"]} - {e["canonical"]})
    return out


def add_surface_form(
    entity: dict[str, Any], text: str, first_vol: int, *, source: str = "override"
) -> bool:
    """Append `text` to `entity`'s surface_forms/aliases if not already present, and update
    `entity["first_vol"]` to the min with `first_vol` either way. Returns True if a new surface
    form was actually added (False if `text` was already present). An existing surface's
    first_vol also moves earlier when earlier evidence arrives; its source follows that evidence.
    Later/equal evidence never overwrites the existing source. Driven by
    `entities/gazetteer.py::merge_epithets` (Phase 18) from mined epithets."""
    existing = next((sf for sf in entity["surface_forms"] if sf["text"] == text), None)
    added = existing is None
    if added:
        entity["surface_forms"].append({
            "text": text, "ambiguous": False, "first_vol": first_vol, "source": source,
        })
        entity["aliases"] = sorted(set(entity["aliases"]) | {text})
    elif first_vol < existing.get("first_vol", entity["first_vol"]):
        existing["first_vol"] = first_vol
        existing["source"] = source
    entity["first_vol"] = min(entity["first_vol"], first_vol)
    return added


def _mark_ambiguous(entities: list[dict[str, Any]]) -> None:
    """A literal surface text claimed by more than one entity is a real collision — flag both,
    per CONTRACTS section 2.1 ('ambiguous forms are indexed but require a disambiguation pass')."""
    owners: dict[str, list[int]] = {}
    for ei, e in enumerate(entities):
        for sf in e["surface_forms"]:
            owners.setdefault(sf["text"], []).append(ei)
    for text, owner_indices in owners.items():
        if len(owner_indices) > 1:
            for ei in owner_indices:
                for sf in entities[ei]["surface_forms"]:
                    if sf["text"] == text:
                        sf["ambiguous"] = True


# Public alias — `entities/gazetteer.py::merge_epithets` (Phase 18) needs to rerun collision
# detection after adding mined surface forms, from outside this module.
mark_ambiguous = _mark_ambiguous


def cluster_candidates(
    classified: list[dict[str, Any]],
    paragraphs_by_id: dict[str, str],
    series_config: dict[str, Any],
    client: JSONClient,
    on_pairs_capped: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Cluster classified candidates into gazetteer entities. See module docstring for the
    tiering strategy and CONTRACTS section 2.1 for the returned shape (minus `importance`).

    `on_pairs_capped`, if given, is passed straight through to `_resolve_maybe_pairs` -- see its
    own docstring. Every series clusters with the same honorifics and the same pair cap (Phase 32,
    req. 8); `series_config` supplies only the author/illustrator metadata.
    """
    honorifics: list[str] = DEFAULT_HONORIFICS
    max_llm_pairs = MAX_LLM_PAIRS

    if not classified:
        if on_pairs_capped is not None:
            on_pairs_capped(0, 0)
        return []

    ambiguous_bare = _names_with_several_honorifics(paragraphs_by_id, honorifics)
    uf, normalized, maybe_pairs = _auto_cluster(classified, honorifics, ambiguous_bare)
    maybe_pairs += _appositive_kinship_pairs(classified, normalized, uf, paragraphs_by_id, maybe_pairs)
    maybe_pairs += _rename_pairs(classified, normalized, uf, paragraphs_by_id, maybe_pairs)

    evidence_by_surface = {
        c["surface"]: [
            paragraphs_by_id[pid] for pid in c.get("evidence_para_ids", []) if pid in paragraphs_by_id
        ]
        for c in classified
    }
    llm_merges = _resolve_maybe_pairs(
        classified, uf, normalized, maybe_pairs, evidence_by_surface, client,
        max_llm_pairs=max_llm_pairs, on_pairs_capped=on_pairs_capped,
    )
    _join_resolved_bridges(classified, uf)

    entities = _build_clusters(classified, uf, honorifics, llm_merges)
    entities = _assign_bare_forms(entities, paragraphs_by_id, honorifics, ambiguous_bare)
    _mark_ambiguous(entities)
    # [32] The author and illustrator an afterword names ("My name is Isuna Hasekura.") are
    # paratext, not characters. Read from the series metadata, never a per-series list.
    meta = series_config.get("series", {}) or {}
    paratext = {str(meta[k]).casefold() for k in ("author", "illustrator") if meta.get(k)}
    return [e for e in entities if e["canonical"].casefold() not in paratext]
