"""[31] L_param: does the generator remember the book, and does decontamination stop it?

Inputs:     the ORIGINAL series' parsed records and a DECONTAMINATED series' records with the same
            `para_id`s (`wiki decontaminate`), its `entity_map`, and an LLMClient.
Outputs:    one row per probe item (CONTRACTS §9.4) and a summary per condition:
            - name cloze (Chang et al. 2023, "Speak, Memory", arXiv 2305.00118): one proper name
              masked in a passage that contains no other mapped name; exact match.
            - source recall: on a decontaminated passage, the model answers with the ORIGINAL
              name (e.g. "Nemo" where the text says "Sorel"). This is recognition that survived
              decontamination, and it is the number this probe exists for.
            - title identification: the model names the source book (`recognize` keywords).
Invariants: - Items are aligned by `para_id` and chosen deterministically (sorted ids, seeded
              sample), so every condition is scored on the same paragraphs.
            - A pair is used only if the original has exactly one mapped name, which is a
              single-word key, and the decontaminated paragraph has exactly one occurrence of
              its replacement. Nothing else is masked.
            - Every call goes through `llm/client.py` (stage `probe_memorization`), temperature 0.
Contract:   docs/CONTRACTS.md §9.4. Plan: docs/vision/plans/0009-parametric-leakage-gutenberg.md.
"""

from __future__ import annotations

import random
import re
from typing import Any

from ..ingest.decontaminate import _pattern

STAGE = "probe_memorization"
OPTIONS = {"temperature": 0.0}

# Chang et al. 2023, §3 (name cloze), wording kept so numbers are comparable.
CLOZE_PROMPT = """You have seen the following passage in your training data. What is the proper name that fills in the [MASK] token in it? This name is exactly one word long, and is a proper name (not a pronoun or any other word). You must make a guess, even if you are uncertain.

Example:

Input: Stay gold, [MASK], stay gold.
Output: <name>Ponyboy</name>

Input: The door opened, and [MASK], dressed and hatted, entered with a cup of tea.
Output: <name>Gerty</name>

Input: {passage}
Output:"""

TITLE_PROMPT = """Which book is the following passage from? If you recognise it, give the title and author; if you do not, say unknown. Answer in the form <title>...</title><author>...</author>.

Passage: {passage}"""


def _occurrences(text: str, entity_map: dict[str, str]) -> list[re.Match[str]]:
    rx, _ = _pattern(entity_map)
    return list(rx.finditer(text))


def select_items(
    original: list[dict[str, Any]],
    decon: list[dict[str, Any]],
    entity_map: dict[str, str],
    n: int = 100,
    min_words: int = 30,
    max_words: int = 150,
    seed: int = 0,
    per_name_cap: int = 25,
) -> list[dict[str, Any]]:
    """Aligned (original, decontaminated) cloze pairs, at most `n` and at most `per_name_cap`
    per answer (one ship name would otherwise be most of the set), deterministic.

    A multi-word hit masks only its last word when that word is itself a key: "Captain Nemo"
    becomes "Captain [MASK]" with answer "Nemo", as the paper masks one-word names."""
    by_id = {r["para_id"]: r for r in decon}
    _, table = _pattern(entity_map)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for rec in sorted(original, key=lambda r: r["para_id"]):
        twin = by_id.get(rec["para_id"])
        if twin is None or not (min_words <= rec["n_words"] <= max_words):
            continue
        hits = _occurrences(rec["text"], entity_map)
        if len(hits) != 1:
            continue
        hit = hits[0].group(1)
        name = hit.split()[-1]
        if name not in table:
            continue
        start = hits[0].end() - len(name)
        stand_in = table[name]
        twin_hits = list(re.finditer(rf"(?<!\w){re.escape(stand_in)}(?!\w)", twin["text"]))
        if len(twin_hits) != 1 or len(_occurrences(twin["text"], {v: v for v in entity_map.values()})) != 1:
            continue
        t = twin_hits[0]
        by_name.setdefault(name, []).append({
            "para_id": rec["para_id"],
            "original_masked": rec["text"][:start] + "[MASK]" + rec["text"][hits[0].end():],
            "original_answer": name,
            "decon_masked": twin["text"][:t.start()] + "[MASK]" + twin["text"][t.end():],
            "decon_answer": stand_in,
            "original_text": rec["text"],
            "decon_text": twin["text"],
        })
    rng = random.Random(seed)
    items = [it for name in sorted(by_name)
             for it in (rng.sample(by_name[name], per_name_cap) if len(by_name[name]) > per_name_cap else by_name[name])]
    items = rng.sample(items, n) if len(items) > n else items
    return sorted(items, key=lambda it: it["para_id"])


def _tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text or "", re.S)
    return (m.group(1) if m else (text or "")).strip()


def _same(a: str, b: str) -> bool:
    return a.strip(" .,’'\"").lower() == b.strip(" .,’'\"").lower()


def measure(client, items: list[dict[str, Any]], recognize: dict[str, list[str]],
            workers: int = 1) -> list[dict[str, Any]]:
    """One row per item: cloze and title answers in both conditions. `recognize` has `title`
    keywords (names the book) and `author` keywords (names only the author, e.g. a wrong Verne)."""
    from ..llm.parallel import map_calls

    def ask(prompt: str) -> str:
        return client.complete(STAGE, prompt, options=OPTIONS)

    prompts = []
    for it in items:
        prompts += [CLOZE_PROMPT.format(passage=it["original_masked"]),
                    CLOZE_PROMPT.format(passage=it["decon_masked"]),
                    TITLE_PROMPT.format(passage=it["original_text"]),
                    TITLE_PROMPT.format(passage=it["decon_text"])]
    answers = map_calls(ask, prompts, workers)
    title_keys = [k.lower() for k in recognize.get("title", [])]
    author_keys = [k.lower() for k in recognize.get("author", [])]
    rows = []
    for i, it in enumerate(items):
        c_orig, c_decon, t_orig, t_decon = answers[4 * i: 4 * i + 4]
        p_orig, p_decon = _tag(c_orig, "name"), _tag(c_decon, "name")
        rows.append({
            "channel": "L_param", "probe": "memorization", "para_id": it["para_id"],
            "original_answer": it["original_answer"], "decon_answer": it["decon_answer"],
            # Remap-only text with its one name masked IS the original masked passage, so its
            # cloze answer is the original's by construction; summaries leave such items out.
            "masked_identical": it["original_masked"] == it["decon_masked"],
            "cloze_original": p_orig, "cloze_decon": p_decon,
            "cloze_original_correct": _same(p_orig, it["original_answer"]),
            "cloze_decon_correct": _same(p_decon, it["decon_answer"]),
            "cloze_decon_source_recall": _same(p_decon, it["original_answer"]),
            "title_original": t_orig.strip(), "title_decon": t_decon.strip(),
            "title_original_book": any(k in t_orig.lower() for k in title_keys),
            "title_decon_book": any(k in t_decon.lower() for k in title_keys),
            "title_original_author": any(k in t_orig.lower() for k in author_keys + title_keys),
            "title_decon_author": any(k in t_decon.lower() for k in author_keys + title_keys),
        })
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    changed = [r for r in rows if not r["masked_identical"]]

    def rate(key: str, subset: list[dict[str, Any]]) -> float | None:
        return round(sum(r[key] for r in subset) / len(subset), 4) if subset else None

    return {
        "channel": "L_param", "probe": "memorization_summary", "n_items": len(rows),
        "n_cloze_decon_scored": len(changed),
        "cloze_original_acc": rate("cloze_original_correct", rows),
        "cloze_original_acc_on_scored": rate("cloze_original_correct", changed),
        "cloze_decon_acc": rate("cloze_decon_correct", changed),
        "cloze_decon_source_recall": rate("cloze_decon_source_recall", changed),
        "title_original_book": rate("title_original_book", rows),
        "title_decon_book": rate("title_decon_book", rows),
        "title_original_author": rate("title_original_author", rows),
        "title_decon_author": rate("title_decon_author", rows),
    }
