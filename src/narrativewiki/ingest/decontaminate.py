"""[31] Decontaminate a parsed novel: remap its fictional names, then paraphrase every paragraph.

Inputs:     the SOURCE series' parsed paragraph records (CONTRACTS §1) and the derived series'
            `decontaminate:` config block (`from_series`, `entity_map`, `paraphrase`).
Outputs:    records of the same shape, with the same `para_id`s, whose `text` and `chapter_title`
            no longer carry the source's fictional names or wording. The one-to-one `para_id`
            alignment is what lets every measurement compare the original and decontaminated
            conditions paragraph for paragraph (docs/vision/PHASE_31.md).
Invariants: - Names are remapped BEFORE the paraphrase call, so the paraphrasing model never sees an
              original name and cannot anchor on the book it may have memorised. They are
              remapped again afterwards, in case the model restores one from memory.
            - The remap is whole-word and case-sensitive. Each key also matches its UPPER-CASE
              form (chapter titles). Longer keys win ("Ned Land" before "Ned"). The table is
              hand-reviewed config, never generated, because a wrong entry silently changes a fact.
            - A paraphrase that comes back empty, or whose length is outside `length_ratio` of the
              input, is not used. The remapped text is kept and the record says so
              (`decon: "remap_only"`). Nothing is dropped.
            - A paraphrase that keeps half or more of its input's word 4-grams is copied, not
              rewritten (72 of 1,788 paragraphs of anne v01, 2026-10-02). It is asked for once
              more with PARAPHRASE_RETRY; if it is still copied the text is used and the record
              says `decon: "verbatim"`, so the report never counts it as paraphrased.
            - Every LLM call goes through `llm/client.py` (stage `decon_paraphrase`).
Contract:   docs/CONTRACTS.md §1 (records unchanged in shape, plus `decon`), §1.6 for the report.
"""

from __future__ import annotations

import re
from typing import Any, Callable

PARAPHRASE_STAGE = "decon_paraphrase"

PARAPHRASE_SYSTEM = (
    "You rewrite passages of fiction in new words for a research corpus. You never add, "
    "remove or explain anything."
)

PARAPHRASE_PROMPT = """Rewrite the paragraph below in different words and different sentence structure.

Rules:
- Keep every fact, event, number, date and place, and keep them in the same order.
- Keep every personal and ship name exactly as written. Do not introduce any name that is not in the paragraph.
- Dialogue stays dialogue, spoken by the same person. Use the same quotation marks as the original (“ ” stay “ ”). Only spoken words go inside them; a speech tag such as "said I" stays outside.
- Keep the narrator's person (first person stays first person).
- Do not summarise, shorten substantially, or add commentary.

Return only the rewritten paragraph, nothing else.

Paragraph:
{text}"""

PARAPHRASE_RETRY = PARAPHRASE_PROMPT.replace(
    "Return only",
    "A first attempt copied this paragraph almost word for word. Change the wording and the structure "
    "of every sentence, spoken ones included, and keep the meaning.\n\nReturn only")

VERBATIM = 0.5  # share of the input's word 4-grams a rewrite may keep before it counts as a copy


def kept(before: str, after: str, n: int = 4) -> float:
    """Share of `before`'s word n-grams that survive in `after`; 0.0 when `before` is too short to say."""
    def grams(text: str) -> set[tuple[str, ...]]:
        words = re.findall(r"[^\W\d_]+", text.lower())
        return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}
    a = grams(before)
    return len(a & grams(after)) / len(a) if len(a) >= 5 else 0.0


def _pattern(entity_map: dict[str, str]) -> tuple[re.Pattern[str], dict[str, str]]:
    table: dict[str, str] = {}
    for original, replacement in entity_map.items():
        table[original] = replacement
        table.setdefault(original.upper(), replacement.upper())
    alternatives = sorted(table, key=len, reverse=True)
    rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(a) for a in alternatives) + r")(?!\w)")
    return rx, table


def remap(text: str, entity_map: dict[str, str]) -> str:
    """Replace every original name with its stand-in, whole word, longest match first."""
    if not text or not entity_map:
        return text
    rx, table = _pattern(entity_map)
    return rx.sub(lambda m: table[m.group(1)], text)


def residual(text: str, entity_map: dict[str, str]) -> list[str]:
    """Original names still present in `text`: the leak check run on every output."""
    if not text or not entity_map:
        return []
    rx, _ = _pattern(entity_map)
    return sorted({m.group(1) for m in rx.finditer(text)})


def _usable(original: str, rewritten: str, ratio: tuple[float, float]) -> bool:
    if not rewritten.strip():
        return False
    lo, hi = ratio
    return lo <= len(rewritten) / max(len(original), 1) <= hi


def decontaminate_records(
    records: list[dict[str, Any]],
    entity_map: dict[str, str],
    paraphrase: Callable[[str], str] | None,
    length_ratio: tuple[float, float] = (0.5, 2.0),
    map_fn: Callable[..., list[str]] | None = None,
    retry: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Return decontaminated copies of `records`, in order.

    `paraphrase(text) -> text` is the model call, or None for remap-only. `map_fn(fn, items)`
    runs it over the paragraphs (the CLI passes `llm.parallel.map_calls` bound to the stage's
    concurrency); it defaults to a plain loop. `retry(text) -> text` is the second request for a
    paragraph that came back copied (PARAPHRASE_RETRY); without it a copy is only labelled.
    """
    remapped = [remap(r["text"], entity_map) for r in records]
    if paraphrase is None:
        rewritten = remapped
    else:
        run = map_fn or (lambda fn, xs: [fn(x) for x in xs])
        rewritten = list(run(paraphrase, remapped))
        copied = [i for i, (b, a) in enumerate(zip(remapped, rewritten)) if kept(b, a or "") >= VERBATIM]
        if retry is not None and copied:
            for i, again in zip(copied, run(retry, [remapped[i] for i in copied])):
                if _usable(remapped[i], again or "", length_ratio):  # a failed retry keeps the first answer
                    rewritten[i] = again
    out = []
    for record, before, after in zip(records, remapped, rewritten):
        after = remap((after or "").strip(), entity_map)
        used = paraphrase is not None and _usable(before, after, length_ratio)
        text = after if used else before
        out.append({
            **record,
            "text": text,
            "chapter_title": remap(record.get("chapter_title") or "", entity_map) or record.get("chapter_title"),
            "n_words": len(text.split()),
            "decon": ("verbatim" if kept(before, after) >= VERBATIM else "paraphrased") if used else "remap_only",
        })
    return out


def report(source: list[dict[str, Any]], derived: list[dict[str, Any]],
           entity_map: dict[str, str]) -> dict[str, Any]:
    """Counts for `data/<derived>/01_parsed/decon_report.json` (CONTRACTS §1.6)."""
    leaks = {r["para_id"]: hits for r in derived
             if (hits := residual(r["text"] + " " + (r.get("chapter_title") or ""), entity_map))}
    unchanged = sum(1 for s, d in zip(source, derived) if s["text"] == d["text"])
    return {
        "n_paragraphs": len(derived),
        "paraphrased": sum(1 for r in derived if r.get("decon") == "paraphrased"),
        "remap_only": sum(1 for r in derived if r.get("decon") == "remap_only"),
        "verbatim": sum(1 for r in derived if r.get("decon") == "verbatim"),
        "identical_to_source": unchanged,
        "words_source": sum(r["n_words"] for r in source),
        "words_derived": sum(r["n_words"] for r in derived),
        "residual_name_paragraphs": len(leaks),
        "residual_names": leaks,
    }
