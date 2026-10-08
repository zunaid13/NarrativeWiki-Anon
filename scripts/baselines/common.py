"""[33] S10 baselines — the shared output contract, prompt, citation check, renderer and contexts.

Every system (plan 0013 §4.1) writes the SAME page: B5's sections, B5's sentence caps
(config/extraction.yaml `page_outline`), B5's citation markup, at
`dist/<series>@<system>/wiki/vNN/character/<slug>.md` — so every evaluation tool
(`pipeline_page_leak.py`, `assertion_precision.py`, `assertion_inventory.py`,
`artifact_exposure.py`, `recall_review.py`) scores a baseline exactly as it scores B5. A system
differs only in the passages it is given:

  X1  none (closed book)                          B1  every paragraph of volumes 1..t
  X2  every paragraph of volumes 1..5 (future-informed diagnostic, never ranked)
  B2  hybrid BM25 + dense (bge-m3) retrieval over prefix-only chunks, under an evidence budget
  B4  the full-built graph (volumes 1..5), retrieved through graph/temporal.py's cutoff filter
  B3  LightRAG — separate adapter, not here

Same generator for all (`baseline_page` stage, routed to B5's model), same cast. Passages go
first in the prompt and the character last, so calls sharing a context share a cacheable prefix.
A cited paragraph id that was not in the system's own passages is dropped and counted — a page may
not cite what its system was not shown.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field, model_validator

PARA_RE = re.compile(r"^v(\d+):c(\d+):p(\d+)$")
RELATIONS = ("PARENT_OF", "CHILD_OF", "SIBLING_OF", "RELATIVE_OF", "SPOUSE_OF", "ROMANTIC_WITH", "FRIEND_OF",
             "RIVAL_OF", "ENEMY_OF", "MENTOR_OF", "STUDENT_OF", "COMMANDS", "SERVES_UNDER", "COMRADE_OF")
# B5's caps (config/extraction.yaml page_outline): max sentences per prose section.
PROSE_CAPS = {"appearance": 4, "personality": 6, "history": 12}
MAX_QUOTES = 5
FIELDS = (("age", "Age"), ("gender", "Gender"), ("status", "Status"), ("rank", "Rank"),
          ("origin", "Origin"), ("occupation", "Role"))
LIST_FIELDS = (("titles", "Titles"), ("nicknames", "Nicknames"))


class _Lenient(BaseModel):
    """[33] A model writing a page with no passages (X1) returns empty fields as null
    (`"rank": {"text": null}`, `"para_id": null`); Anne's t=4 and Gilbert's t=5 X1 pages failed
    validation three times each. Null strings become "", null lists [], and `enforce` then drops
    the empty items, so an empty answer is an absent field rather than a failed page."""

    @model_validator(mode="before")
    @classmethod
    def _nulls_to_empty(cls, data):
        if isinstance(data, dict):
            data = {k: ([] if k == "evidence" else "") if (v is None and k in ("text", "name", "predicate", "evidence")) else v
                    for k, v in data.items()}
        return data


class Cited(_Lenient):
    text: str = ""
    evidence: list[str] = Field(default_factory=list)


class Relation(_Lenient):
    name: str = ""
    predicate: str = ""
    note: str | None = None
    evidence: list[str] = Field(default_factory=list)


class Affiliation(_Lenient):
    name: str = ""
    role: str | None = None
    evidence: list[str] = Field(default_factory=list)


class Quote(_Lenient):
    text: str = ""
    para_id: str | None = None  # X1 (no passages) writes quotes with no id; enforce() drops them


class Page(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data):  # "traits": null -> []
        if isinstance(data, dict):
            data = {k: ([] if v is None and k in _LIST_KEYS else v) for k, v in data.items()}
        return data

    age: Cited | None = None
    gender: Cited | None = None
    status: Cited | None = None
    rank: Cited | None = None
    origin: Cited | None = None
    occupation: Cited | None = None
    titles: list[Cited] = Field(default_factory=list)
    nicknames: list[Cited] = Field(default_factory=list)
    appearance: Cited | None = None
    personality: Cited | None = None
    traits: list[Cited] = Field(default_factory=list)
    abilities: list[Cited] = Field(default_factory=list)
    goals: list[Cited] = Field(default_factory=list)
    history: Cited | None = None
    background: list[Cited] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    affiliations: list[Affiliation] = Field(default_factory=list)
    relationships: list[Relation] = Field(default_factory=list)


_LIST_KEYS = {"titles", "nicknames", "traits", "abilities", "goals", "background", "quotes",
              "affiliations", "relationships"}


SYSTEM = f"""You write one character's page for a spoiler-safe wiki of a novel series. The reader has
read up to a stated volume and nothing later.

Return JSON with these keys (omit or leave empty what the passages do not support):
- age, gender, status, rank, origin, occupation: {{"text": short value, "evidence": [paragraph ids]}}
- titles, nicknames: lists of {{"text", "evidence"}}
- appearance: {{"text": 2-{PROSE_CAPS['appearance']} sentences, "evidence": [...]}}
- personality: {{"text": 2-{PROSE_CAPS['personality']} sentences, "evidence": [...]}}
- traits (personality traits and fears), abilities (skills), goals (motivations),
  background (facts about the character's past): lists of {{"text": one short phrase, "evidence": [...]}}
- history: {{"text": 3-{PROSE_CAPS['history']} sentences, a biography of what the character did and experienced, "evidence": [...]}}
- quotes: up to {MAX_QUOTES} of {{"text": the character's own words copied exactly, "para_id": id}}
- affiliations: list of {{"name": group, "role": short, "evidence": [...]}}
- relationships: list of {{"name": another character, "predicate": one of {", ".join(RELATIONS)}, "note": short, "evidence": [...]}}

Rules:
- Use only the passages given. Every item cites the ids of the paragraphs that support it, copied
  exactly as they appear in brackets, e.g. v01:c06:p0003.
- State nothing a reader of the stated volumes could not know.
- Write in the third person, in plain encyclopaedic English. JSON only."""

SYSTEM_CLOSED_BOOK = SYSTEM.replace(
    "- Use only the passages given. Every item cites the ids of the paragraphs that support it, copied\n"
    "  exactly as they appear in brackets, e.g. v01:c06:p0003.",
    "- No passages are given: write what you know of this character from the novels up to the stated\n"
    "  volume. Leave every evidence list empty.")


def format_passages(paras: Iterable[dict]) -> str:
    return "\n".join(f"[{p['para_id']}] {p['text']}" for p in paras)


def prompt(passages: str, character: str, upto: int, title: str) -> str:
    # [33] 2026-09-30: only the closed-book diagnostic (X1) is told the work: it measures what the
    # model remembers of the series, and without the title wrote Leslie Moore as a "Cryptographer and
    # Linguistic Analyst". Every system with passages is NOT told it, matching B5, whose prompts never
    # name the novel ("passages of a novel") -- a title would give the baselines parametric recall
    # (and a parametric leak channel) that the system under test does not have.
    head = (f"Passages from a novel series, volumes 1-{upto} (the reader has read exactly these volumes):\n\n{passages}\n\n"
            if passages else f"The novels: {title}, volumes 1-{upto} (the reader has read exactly these volumes).\n\n")
    return f"{head}Write the page for the character \"{character}\" as known after volume {upto}."



def prompt_future_informed(passages: str, character: str, upto: int, vmax: int) -> str:
    """[33] X2: the whole text, with the cutoff stated only as an instruction -- the naive "tell the
    model where to stop" shortcut that filter-before-generate replaces. It used to get
    `prompt(passages, ch, vmax)`, i.e. "as known after volume 5" at every t: byte-identical to
    B1 t=5 (all 14 X2 t=1 calls were cache hits of B1 t=5), so it measured nothing."""
    return (f"Passages from a novel series, volumes 1-{vmax}. The reader has read ONLY volumes 1-{upto}: "
            f"write for that reader and do not reveal anything that happens after volume {upto}.\n\n"
            f"{passages}\n\nWrite the page for the character \"{character}\" as known after volume {upto}.")


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“(])", text.strip()) if s]


def enforce(page: Page, allowed: set[str]) -> tuple[Page, dict]:
    """Drop citations to paragraphs the system was not shown; cap prose at B5's sentence limits."""
    stats = Counter()

    def keep(ids: list[str]) -> list[str]:
        good = [i for i in ids if i in allowed]
        stats["citations_dropped"] += len(ids) - len(good)
        stats["citations_kept"] += len(good)
        return good

    data = page.model_dump()
    for k, v in data.items():
        items = v if isinstance(v, list) else [v] if isinstance(v, dict) else []
        for it in items:
            if "evidence" in it:
                it["evidence"] = keep(it["evidence"])
            if k in PROSE_CAPS and it.get("text"):
                s = sentences(it["text"])
                if len(s) > PROSE_CAPS[k]:
                    stats["sentences_cut"] += len(s) - PROSE_CAPS[k]
                    it["text"] = " ".join(s[: PROSE_CAPS[k]])
    data["quotes"] = [q for q in data["quotes"] if q["para_id"] in allowed and q["text"].strip()][:MAX_QUOTES]
    for k, v in list(data.items()):  # drop empty answers: an absent field, never an empty line
        if isinstance(v, list):
            data[k] = [it for it in v if (it.get("text") or it.get("name") or "").strip()]
        elif isinstance(v, dict) and not (v.get("text") or "").strip():
            data[k] = None
    return Page.model_validate(data), dict(stats)


def _cite(ids: list[str]) -> str:
    links = []
    for i in ids:
        m = PARA_RE.match(i)
        if m:
            v, c, p = (int(x) for x in m.groups())
            links.append(f"[v{v} c{c} P{p}](../source/v{v:02d}-c{c:02d}.md#nw-{i.replace(':', '-')})")
    return f"  <sub>{', '.join(links)}</sub>" if links else ""


def _label(pred: str) -> str:
    return pred.replace("_", " ").capitalize().replace(" with", " with").replace(" of", " of")


def render(page: Page, character: str, slug: str, upto: int, system: str) -> str:
    out = ["---", f"entity_id: {slug}", f"canonical: {character}", "type: CHARACTER",
           f"upto_vol: {upto}", f"system: {system}", "---", "", f"# {character}", "", "## Overview", ""]
    for key, label in FIELDS:
        f = getattr(page, key)
        if f and f.text.strip():
            out.append(f"- **{label}:** {f.text.strip()}{_cite(f.evidence)}")
    for key, label in LIST_FIELDS:
        vals = [v for v in getattr(page, key) if v.text.strip()]
        if vals:
            out.append(f"- **{label}:** " + "; ".join(f"{v.text.strip()}{_cite(v.evidence)}" for v in vals))
    for key, title in (("appearance", "Appearance"), ("personality", "Personality")):
        _prose(out, title, getattr(page, key))
    for key, title in (("traits", "Traits"), ("abilities", "Abilities"), ("goals", "Goals")):
        _list(out, title, getattr(page, key))
    _prose(out, "History", page.history)
    _list(out, "Background by volume", page.background)
    if page.quotes:
        out += ["", "## Quotes", ""] + [f"> {q.text.strip()}{_cite([q.para_id])}\n" for q in page.quotes]
    if page.affiliations:
        out += ["", "## Affiliations", ""] + [
            f"- {a.name}" + (f" — {a.role}" if a.role else "") + _cite(a.evidence) for a in page.affiliations]
    if page.relationships:
        out += ["", "## Relationships", ""]
        for r in page.relationships:
            out += [f"### {r.name}", "", f"- {_label(r.predicate)}" + (f" — {r.note}" if r.note else "") + _cite(r.evidence), ""]
    return "\n".join(out).rstrip() + "\n"


def _prose(out: list[str], title: str, c: Cited | None) -> None:
    if c and c.text.strip():
        out += ["", f"## {title}", "", c.text.strip()]
        src = _cite(c.evidence).strip()
        if src:
            out += ["", f"_Sources: {src[len('<sub>'):-len('</sub>')]}_"]


def _list(out: list[str], title: str, items: list[Cited]) -> None:
    items = [i for i in items if i.text.strip()]
    if items:
        out += ["", f"## {title}", ""] + [f"- {i.text.strip()}{_cite(i.evidence)}" for i in items]


# --- contexts ---------------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def present(character: str, first_vol: int | None, text: str, t: int) -> bool:
    """Is the character in the cast at cutoff t? Yes when a gold fact about them is evidenced by t
    (`first_vol`, None for a --cast list), or the prefix names them in full. A system cannot know a
    character its passages never name, so no page is written for them; the character stays in every
    evaluation denominator as a page-less miss. [33] 2026-09-29: first/last-word matching was
    dropped -- "Aunt", "Little", "Mary", "Marshall" gave Aunt Jamesina, Little Jem, Mary Vance and
    Marshall Elliott pages at t=1, i.e. the page title disclosed a future name."""
    return (first_vol is not None and first_vol <= t) or re.search(rf"\b{re.escape(character)}\b", text) is not None


def chunk(paras: list[dict], max_words: int = 250) -> list[dict]:
    """Consecutive paragraphs of one chapter up to ~max_words; a chunk keeps its paragraph ids."""
    out, cur, words, key = [], [], 0, None
    for p in paras:
        k = p["para_id"].rsplit(":", 1)[0]
        n = len(p["text"].split())
        if cur and (k != key or words + n > max_words):
            out.append({"paras": cur, "text": " ".join(x["text"] for x in cur)})
            cur, words = [], 0
        cur.append(p)
        words += n
        key = k
    if cur:
        out.append({"paras": cur, "text": " ".join(x["text"] for x in cur)})
    return out


TOKEN_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over tokenized chunks — a few lines, no new dependency."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b, self.docs = k1, b, [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = sum(self.lens) / max(1, len(docs))
        df = Counter(t for d in self.docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in query:
                if t in d:
                    tf = d[t]
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return out


FACETS = ("", "appearance looks face hair eyes", "personality temper character", "family mother father sister brother",
          "friend enemy loves", "wants hopes plans dream", "past history childhood", "said")


def hybrid_select(chunks: list[dict], vectors: list[list[float]] | None, query_vec: dict[str, list[float]] | None,
                  character: str, budget_tokens: int, rrf_k: int = 60) -> list[dict]:
    """Reciprocal-rank fusion of BM25 and cosine rankings over every facet query; fill the budget in
    fused order, then return the chosen chunks in reading order."""
    bm = BM25([tokenize(c["text"]) for c in chunks])
    fused = Counter()
    for facet in FACETS:
        q = f"{character} {facet}".strip()
        rankings = [bm.scores(tokenize(q))]
        if vectors is not None and query_vec is not None:
            qv = query_vec[q]
            rankings.append([_cos(qv, v) for v in vectors])
        for scores in rankings:
            order = sorted(range(len(chunks)), key=lambda i: -scores[i])
            for rank, i in enumerate(order[:200]):
                fused[i] += 1 / (rrf_k + rank + 1)
    chosen, used = [], 0
    for i, _ in fused.most_common():
        cost = estimate_tokens(chunks[i]["text"])
        if used + cost > budget_tokens:
            continue
        chosen.append(i)
        used += cost
    return [chunks[i] for i in sorted(chosen)]


def facet_queries(characters: list[str]) -> list[str]:
    return [f"{c} {f}".strip() for c in characters for f in FACETS]


def _cos(a: list[float] | None, b: list[float] | None) -> float:
    if a is None or b is None:  # a text bge-m3 could not encode (NaN) has no vector: it matches nothing
        return 0.0
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def load_paras(parsed: Path, upto: int) -> list[dict]:
    out = []
    for v in range(1, upto + 1):
        f = parsed / f"v{v:02d}.jsonl"
        if f.is_file():
            out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out
