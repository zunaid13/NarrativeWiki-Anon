"""[2] Paragraph records -> name-candidate surfaces. Deterministic; no LLM. CONTRACTS section 2.3.

Inputs:     Parsed paragraph records (data/01_parsed/v{NN}.jsonl, already loaded) grouped by
            volume, plus the series config's `entities:` hints (honorifics, stopword surfaces,
            min_mentions, max_candidates_per_volume).
Outputs:    One candidate dict per distinct surface form, aggregated across every volume passed
            in, sorted by mention count descending.
Invariants: - Recall over precision. A wrong candidate costs one wasted classify call and is
              filtered by `entities/classify.py` or the human roster review; a missed candidate
              is invisible and never recovered. Regexes here are deliberately permissive.
            - `first_vol` on a candidate is the minimum volume in which its exact surface text
              was seen — never guessed, never backdated by an alias relationship (that merge
              happens later, in `entities/alias.py`).
Contract:   docs/CONTRACTS.md section 2.3.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# A run of consecutive capitalised tokens, single-space-separated (punctuation or a second
# space breaks the run, which is what keeps "Shin. He" from joining into one candidate).
#
# Deliberately does NOT swallow a trailing apostrophe: "Shin's"/"I'm"/"That's" would otherwise
# be mined as their own junk candidates distinct from "Shin"/"I"/"That". Dropping the suffix
# folds the possessive/contraction back into the base token, which is both correct and self-
# merging (a possessive mention now simply adds to the base name's count instead of splitting
# it across two candidates).
# [34] A hyphen joins a name's parts: Oz's "Saw-Horse" and "Woggle-Bug" were mined as "Horse" and
# "Bug" (OPEN_GAPS G9), and "Button-Bright" as two names.
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_MAX_RUN_TOKENS = 4

# Common capitalised English words that are not names even though they satisfy the regex —
# sentence-initial pronouns, articles, conjunctions. Filtered only as a *complete* candidate
# surface (single-token match), never as part of a longer run, since "The Reaper" is a real
# nickname but bare "The" never is.
_GENERIC_STOPWORDS = {
    "the", "a", "an", "he", "she", "it", "they", "we", "you", "i", "his", "her", "its",
    "their", "our", "your", "this", "that", "these", "those", "there", "here", "who", "what",
    "when", "where", "why", "how", "but", "and", "or", "so", "yet", "for", "nor", "if", "as",
    "after", "before", "then", "than", "though", "although", "because", "since", "while",
    "one", "two", "three", "first", "second", "third", "next", "last", "now", "still", "just",
    "even", "well", "yes", "no", "okay", "oh", "ah", "hey", "huh", "hmm", "yeah", "right", "sure",
    "sir", "ma'am", "sergeant",  # bare rank words without a name attached
}

# [31] Words that open a sentence, never a name: stripped from the front of a multi-token run.
_LEADING_JUNK = {
    "but", "and", "or", "so", "yet", "nor", "if", "as", "when", "then", "now", "oh", "ah", "yes",
    "no", "well", "still", "just", "even", "though", "although", "because", "since", "while",
    "after", "before", "than", "hey", "huh", "hmm", "yeah", "okay", "where", "why", "how", "what",
    "who", "meanwhile", "besides", "moreover", "unfortunately", "sometimes",
    # [32] Prepositions: Overlord mined "in Yggdrasil" (42 mentions) and "To Momonga" as names.
    "to", "in", "at", "on", "for", "from", "with", "by", "into",
}

# [30] English titles, for a series that sets no `entities.honorifics` of its own. Setting the key
# (even to []) replaces this list; both current series set theirs.
# Kinship and address words ("Father", "Master") are left out: a bare one can be how a novel
# names a real character, and `mine_candidates` drops bare honorifics.
# [32] One list for every series (req. 8): the union of what the series configs used to add
# (Master, Sister, Brother).
DEFAULT_HONORIFICS = [
    "Mr.", "Mrs.", "Ms.", "Miss", "Mistress", "Dr.", "Sir", "Lady", "Lord", "Madam", "Master", "Captain",
    "Professor", "Saint", "Sister", "Brother", "King", "Queen", "Prince", "Princess",
]

# Evidence para_ids kept per candidate. Only the first ~5 are ever shown in a prompt (classify.py
# and alias.py both truncate further), but the full capped set is also what `entities/alias.py`
# uses to detect co-occurrence between two surfaces — the only signal that can catch a true
# nickname pair like "Shin" / "Undertaker", which never look textually similar. Capped well
# above the prompt-display count so that signal actually has something to work with.
_MAX_EVIDENCE_PER_CANDIDATE = 40

# A single-token candidate is kept only if it is capitalised at least this often out of every
# time the same word (any case) appears anywhere in the corpus. Real proper nouns are almost
# always capitalised; ordinary words ("in", "not", "don['t]") are capitalised only when they
# happen to open a sentence, so most of their occurrences are lowercase. This is what catches
# the long tail of sentence-initial junk the hand-maintained stopword list above cannot
# enumerate in advance, without hard-coding a bigger stopword list.
_ALWAYS_CAPITALISED_RATIO = 0.75


def _capitalised_runs(text: str) -> list[tuple[str, int, int]]:
    """Maximal runs of 1-4 consecutive capitalised tokens, single-space separated.

    Returns (surface, start, end) triples, `end` exclusive. A run stops at any punctuation,
    quote mark, or double space, which is what distinguishes a name from an ALL-CAPS shout or
    a sentence that merely starts with a capital.
    """
    tokens = list(_TOKEN_RE.finditer(text))
    runs: list[tuple[str, int, int]] = []
    i = 0
    n = len(tokens)
    while i < n:
        if not tokens[i].group()[0].isupper():
            i += 1
            continue
        j = i
        while (
            j + 1 < n
            and j - i < _MAX_RUN_TOKENS - 1
            and tokens[j + 1].group()[0].isupper()
            and text[tokens[j].end() : tokens[j + 1].start()] == " "
        ):
            j += 1
        runs.append((text[tokens[i].start() : tokens[j].end()], tokens[i].start(), tokens[j].end()))
        i = j + 1
    # [30] A possessive inside a name ("Merchant’s Guild", "Goblin General’s Horns") splits the
    # run above, so the name was only ever mined as "Guild". Added as an extra run: the single
    # names keep their counts. Across three corpora' v1-2 this adds exactly those two surfaces.
    runs.extend((m.group(0), m.start(), m.end()) for m in _POSSESSIVE_NAME_RE.finditer(text))
    return runs


def _of_title_runs(text: str) -> list[tuple[str, str]]:
    """[34] "<Name> of (the) <Name>" as one more run, with its head: Oz's "Wicked Witch of the
    West" and "...of the East" are two people, and "Wizard of Oz" a name, but the runs above stop
    at "of" (OPEN_GAPS G9). Kept by `mine_candidates` only when the head is itself a candidate,
    so a sentence opener ("Most of the Munchkins") never becomes one."""
    return [(m.group(0), m.group(1)) for m in _OF_TITLE_RE.finditer(text)]


_NAME_WORD = r"[A-Z][a-zA-Z’'-]*[a-zA-Z]"
_POSSESSIVE_NAME_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?[’']s [A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){0,2}\b"
)
_OF_TITLE_RE = re.compile(rf"\b({_NAME_WORD}(?: {_NAME_WORD})?) of (?:the )?{_NAME_WORD}(?: {_NAME_WORD})?\b")


def _preceded_by_honorific(surface: str, text: str, start: int, honorifics: list[str]) -> bool:
    """True when the run is introduced by an honorific — either as a preceding word the run
    itself didn't capture ("Dr. Milizé", where "Dr." breaks the run at the period), or, more
    commonly, as the run's own first word ("Major Milizé" is one capitalised run, so the
    honorific is *inside* `surface`, not before `start`)."""
    if any(surface == h or surface.startswith(h + " ") for h in honorifics):
        return True
    window = text[max(0, start - 24) : start].lower()
    return any(window.endswith(h.lower() + " ") for h in honorifics)


def _is_vocative(text: str, speech: str, start: int, end: int) -> bool:
    if speech != "dialogue":
        return False
    after_comma = text[end : end + 1] == ","
    before_quote = text[max(0, start - 2) : start] in ("“", '"', "‘")
    return after_comma or before_quote


def mine_candidates(
    volumes: dict[int, list[dict[str, Any]]],
    series_config: dict[str, Any],
    min_mentions: int = 3,
) -> list[dict[str, Any]]:
    """Mine candidate name surfaces across every volume passed in.

    `volumes` maps vol -> that volume's paragraph records (CONTRACTS section 1 shape), already
    loaded from `data/01_parsed/v{NN}.jsonl`. Only the volumes actually passed in are mined —
    running `wiki gazetteer --volumes 1-3` mines only those three.

    `min_mentions` is still checked against the count pooled across every volume passed in — a
    candidate needs enough total evidence, full stop. The capitalisation-ratio filter is not: see
    `_passes_capitalisation_ratio` (TASK-0004) for why it is evaluated per volume instead.
    """
    hints = series_config.get("entities", {}) or {}
    honorifics: list[str] = DEFAULT_HONORIFICS
    stopwords = {s.lower() for s in (hints.get("stopword_surfaces", []) or [])}
    # [30] "Mr." tokenises to "Mr": a bare honorific, never a name (classify.py's prompt already
    # calls it NOT_ENTITY), yet it was mined 60 times from one Gutenberg novel.
    bare_honorifics = {h.lower().rstrip(".") for h in honorifics}
    max_candidates = int(hints.get("max_candidates_per_volume", 400))

    agg: dict[str, dict[str, Any]] = {}
    word_freq_by_vol: dict[int, Counter[str]] = {}

    for vol in sorted(volumes):
        vol_word_freq = word_freq_by_vol.setdefault(vol, Counter())
        for record in volumes[vol]:
            text = record["text"]
            speech = record.get("speech", "narration")
            vol_word_freq.update(m.group().lower() for m in _TOKEN_RE.finditer(text))
            for surface, start, end in _capitalised_runs(text):
                # [31] A sentence-initial conjunction glued to a name ("But Captain Nemo", "If
                # Captain Nemo") became the canonical id `but-captain-nemo` on a Gutenberg novel.
                # "The" is not stripped: "The Captain" / "The Canadian" are real epithets.
                while " " in surface and surface.split(" ", 1)[0].lower() in _LEADING_JUNK:
                    head = surface.split(" ", 1)[0]
                    surface, start = surface[len(head) + 1:], start + len(head) + 1
                is_single = " " not in surface
                if all(t in _GENERIC_STOPWORDS for t in surface.lower().split()):
                    # [30] Multi-token too: a first-person novel opens sentences "But I"/"If I",
                    # 60 junk runs in The Hound of the Baskervilles. No such run is ever a name.
                    continue
                if surface.lower() in stopwords or surface.lower() in bare_honorifics:
                    continue

                signals: set[str] = set()
                if not is_single:
                    signals.add("capitalised_bigram")
                else:
                    signals.add("capitalised_token")
                if _preceded_by_honorific(surface, text, start, honorifics):
                    signals.add("honorific")
                if speech == "para_raid":
                    signals.add("para_raid_callsign")
                if _is_vocative(text, speech, start, end):
                    signals.add("vocative")

                entry = agg.get(surface)
                if entry is None:
                    entry = agg[surface] = {
                        "surface": surface,
                        "count": 0,
                        "first_vol": vol,
                        "evidence_para_ids": [],
                        "signals": set(),
                        "count_by_vol": Counter(),
                    }
                entry["count"] += 1
                entry["count_by_vol"][vol] += 1
                entry["first_vol"] = min(entry["first_vol"], vol)
                entry["signals"] |= signals
                if len(entry["evidence_para_ids"]) < _MAX_EVIDENCE_PER_CANDIDATE:
                    entry["evidence_para_ids"].append(record["para_id"])
            for surface, head in _of_title_runs(text):
                while " " in head and head.split(" ", 1)[0].lower() in _LEADING_JUNK | {"the", "a", "an"}:
                    cut = head.split(" ", 1)[0]
                    surface, head = surface[len(cut) + 1:], head[len(cut) + 1:]
                entry = agg.setdefault(surface, {
                    "surface": surface, "count": 0, "first_vol": vol, "evidence_para_ids": [],
                    "signals": {"of_title"}, "count_by_vol": Counter(), "of_head": head,
                })
                entry["count"] += 1
                entry["count_by_vol"][vol] += 1
                entry["first_vol"] = min(entry["first_vol"], vol)
                if len(entry["evidence_para_ids"]) < _MAX_EVIDENCE_PER_CANDIDATE:
                    entry["evidence_para_ids"].append(record["para_id"])

    def _passes_capitalisation_ratio(c: dict[str, Any]) -> bool:
        if " " in c["surface"]:  # only single tokens suffer the sentence-initial false positive
            return True
        # [24] TASK-0004 Option A: pass if ANY single volume's own exact/any-case ratio clears
        # the threshold, not just the ratio pooled across every volume in this mining call. A
        # word that already earned proper-noun status from one volume's own text shouldn't be
        # retroactively erased just because a later volume in the same call uses it more loosely
        # (docs/vision/plans/0004-capitalisation-ratio-corpus-composition.md — "Wisewolf" vanishing once
        # volume 2 joins volume 1's mining pass, even though it clears the ratio on volume 1 alone).
        surface_lower = c["surface"].lower()
        return any(
            word_freq_by_vol[vol][surface_lower] == 0
            or exact / word_freq_by_vol[vol][surface_lower] >= _ALWAYS_CAPITALISED_RATIO
            for vol, exact in c["count_by_vol"].items()
        )

    kept = [
        c for c in agg.values() if c["count"] >= min_mentions and _passes_capitalisation_ratio(c)
    ]
    names = {c["surface"] for c in kept if "of_head" not in c}
    kept = [c for c in kept if c.pop("of_head", None) in names | {None}]
    kept.sort(key=lambda c: (-c["count"], c["surface"]))

    cap = max_candidates * max(1, len(volumes))
    kept = kept[:cap]

    for c in kept:
        c["signals"] = sorted(c["signals"])
        del c["count_by_vol"]  # internal bookkeeping only, not part of the candidate contract
    return kept
