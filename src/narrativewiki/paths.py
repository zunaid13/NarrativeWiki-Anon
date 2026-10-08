"""Canonical filesystem paths for every pipeline artifact.

Inputs:     PROJECT_ROOT (inferred from this file's location, or NARRATIVEWIKI_ROOT).
Outputs:    Path objects. Nothing here reads or writes content.
Invariants: - No module outside this one may build a path into data/ by hand. If you need a new
              artifact location, add a function here so `wiki status` and the audit reports can
              see it too.
            - `DATA_DIR`/`PARSED_DIR`/.../`STAGE_DIRS` are module globals other functions in
              this file (and every monkeypatch in tests/) reference directly, not properties --
              `set_active_series()` reassigns them in place rather than computing them on every
              access, so both a bare `paths.PARSED_DIR` read from outside this module and a
              free-variable read from inside a function defined here (e.g. `parsed_volume`) see
              the same, current value with no extra plumbing.
Contract:   docs/CONTRACTS.md — the filenames below are the ones documented there.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

# src/narrativewiki/paths.py -> src/narrativewiki -> src -> project root
PROJECT_ROOT = Path(os.environ.get("NARRATIVEWIKI_ROOT", Path(__file__).resolve().parents[2]))

CONFIG_DIR = PROJECT_ROOT / "config"
DOCS_DIR = PROJECT_ROOT / "docs"
CORPUS_DIR = PROJECT_ROOT / "corpus"  # source EPUBs, one gitignored dir per series id

# The single source for the default series id. `config.py` reads `paths.DEFAULT_SERIES` rather
# than recomputing it -- it already does `from . import paths`, and paths.py stays a leaf module
# with no internal imports (see STRUCTURE.md's dependency order), so the import only works in
# this direction.
DEFAULT_SERIES = os.environ.get("NARRATIVEWIKI_SERIES", "86")
_active_series: str | None = None

# The LLM cache is content-addressed (sha256 of provider+model+prompt+options, see
# llm/cache.py::make_key) -- a cached answer is valid no matter which series asked the question,
# so it is deliberately NOT segmented by series and never reassigned by set_active_series below.
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LLM_CACHE_DIR = CACHE_DIR / "llm"

DIST_DIR = PROJECT_ROOT / "dist"
SITE_DIR = DIST_DIR / "wiki"  # Phase 25: MkDocs wiki tree, replacing the old SPA at dist/<s>/site/

# Stage directories. The numeric prefix is the pipeline order and is load-bearing for
# `wiki status`, which reports staleness by comparing mtimes down this list. The stage-key ->
# directory-name mapping is written ONCE, here -- both the module-scope layout below and
# set_active_series's per-series redirect build STAGE_DIRS (and the individual *_DIR globals
# every other module imports by name) from this same dict, so adding a stage is a one-line edit
# in one place instead of two.
_STAGE_SUFFIXES: dict[str, str] = {
    "ingest": "01_parsed",
    "gazetteer": "02_entities",
    "scenes": "02b_scenes",
    "claims": "03_claims",
    "graph": "04_graph",
    "events": "04b_events",
    "pages": "05_pages",
    "bundle": "06_bundle",
}


def _stage_dirs(data_dir: Path) -> dict[str, Path]:
    return {key: data_dir / suffix for key, suffix in _STAGE_SUFFIXES.items()}


STAGE_DIRS: dict[str, Path] = _stage_dirs(DATA_DIR)
PARSED_DIR = STAGE_DIRS["ingest"]
ENTITIES_DIR = STAGE_DIRS["gazetteer"]
SCENES_DIR = STAGE_DIRS["scenes"]
CLAIMS_DIR = STAGE_DIRS["claims"]
GRAPH_DIR = STAGE_DIRS["graph"]
EVENTS_DIR = STAGE_DIRS["events"]
PAGES_DIR = STAGE_DIRS["pages"]
BUNDLE_DIR = STAGE_DIRS["bundle"]

# One directory per CLI invocation that touches a stage: manifest, LLM call log, budget, and a
# pre-run snapshot of whatever stage dirs it is about to write to (Phase 10, see provenance.py).
# Segmented per series exactly like DATA_DIR itself -- reassigned in set_active_series below.
RUNS_DIR = DATA_DIR / "_runs"


def set_active_series(series_id: str | None) -> None:
    """Point DATA_DIR/DIST_DIR/SITE_DIR and every stage directory at this series's own subtree.

    Call once, early, per process (`cli.py::_settings`) — this is not a per-call parameter.
    Every consumer in this codebase imports PARSED_DIR/PAGES_DIR/etc. as bare module attributes,
    and this project is single-series-per-invocation by design (`wiki --series <id>`), so
    re-threading a `series_id` argument through every module that touches a `data/` path would
    be a much larger, riskier change for the same result.

    The DEFAULT series (`NARRATIVEWIKI_SERIES`, or "86") — and `None`, the state before any
    command has called this — keep the original, unsegmented `data/`/`dist/` layout exactly as
    it always was: the real, already-built 86 corpus (`data/01_parsed/`, `data/03_claims/`,
    `data/04_graph/graph.db`, ...) must never be silently orphaned under `data/86/` by this
    change. Only a genuinely different `--series` gets its own `data/<id>/`, `dist/<id>/site/`
    subtree — so a second series can never interleave with or overwrite the first's output.
    """
    global _active_series, DATA_DIR, DIST_DIR, SITE_DIR
    global PARSED_DIR, ENTITIES_DIR, SCENES_DIR, CLAIMS_DIR, GRAPH_DIR, EVENTS_DIR, PAGES_DIR, BUNDLE_DIR
    global STAGE_DIRS, RUNS_DIR

    _active_series = series_id
    segmented = bool(series_id) and series_id != DEFAULT_SERIES

    DATA_DIR = (PROJECT_ROOT / "data" / series_id) if segmented else (PROJECT_ROOT / "data")
    DIST_DIR = (PROJECT_ROOT / "dist" / series_id) if segmented else (PROJECT_ROOT / "dist")
    SITE_DIR = DIST_DIR / "wiki"

    STAGE_DIRS = _stage_dirs(DATA_DIR)
    if base := variant_base(series_id):  # [33] a variant reads its base's parsed text, never copies it
        STAGE_DIRS["ingest"] = _stage_dirs(PROJECT_ROOT / "data" / base)["ingest"]
    PARSED_DIR = STAGE_DIRS["ingest"]
    ENTITIES_DIR = STAGE_DIRS["gazetteer"]
    SCENES_DIR = STAGE_DIRS["scenes"]
    CLAIMS_DIR = STAGE_DIRS["claims"]
    GRAPH_DIR = STAGE_DIRS["graph"]
    EVENTS_DIR = STAGE_DIRS["events"]
    PAGES_DIR = STAGE_DIRS["pages"]
    BUNDLE_DIR = STAGE_DIRS["bundle"]
    RUNS_DIR = DATA_DIR / "_runs"

    # A prior set_build_cutoff call's redirect does not survive switching series -- re-derive
    # from this series's own DATA_DIR (render mode) until set_build_cutoff is called again.
    global _build_cutoff
    _build_cutoff = None


_build_cutoff: int | None = None


def active_series() -> str | None:
    """The series `set_active_series` last pointed this process at (`None` before any call, which
    means the default, unsegmented layout). The read side of the setter above -- so a caller that
    needs to name the series it is reading, in a report or a filename, does not have to be told
    it separately or reach into `_active_series`."""
    return _active_series


def set_build_cutoff(upto_vol: int | None) -> None:
    """[24] Redirect `ENTITIES_DIR` (and everything derived from it: `gazetteer()`,
    `mentions()`, `candidates()`, `roster_html()`) and `CLAIMS_DIR` (`claims_volume()`,
    `scene_claims_volume()`) to `data/<series>/@t<NN>/02_entities/` and
    `data/<series>/@t<NN>/03_claims/` respectively -- the cutoff-consistent ("Mode B" /
    `--gate=build`) artifact locations, kept separate from the default ("Mode A" / `--gate=render`)
    `data/<series>/02_entities/` and `data/<series>/03_claims/` so a Mode B rebuild never
    overwrites the whole-corpus artifacts the render-time gate has always used, and both can be
    measured and diffed by the same `probe/channels/index.py` functions (which just call
    `paths.gazetteer()`/`paths.candidates()` and see whichever mode is currently active).

    `upto_vol=None` restores the default, whole-corpus locations. Now covers entity-discovery
    AND claim-extraction artifacts (docs/archive/GUIDE_TO_PUBLISHING.md §5 Part 1, 2026-09-13 + this task) --
    the graph and pages are still not redirected by this switch; extending it to those stages is
    the next step, not a change to make silently here.
    """
    global ENTITIES_DIR, CLAIMS_DIR, _build_cutoff
    _build_cutoff = upto_vol
    ENTITIES_DIR = (DATA_DIR / f"@t{upto_vol:02d}" / "02_entities") if upto_vol is not None else (DATA_DIR / "02_entities")
    CLAIMS_DIR = (DATA_DIR / f"@t{upto_vol:02d}" / "03_claims") if upto_vol is not None else (DATA_DIR / "03_claims")
    STAGE_DIRS["gazetteer"] = ENTITIES_DIR
    STAGE_DIRS["claims"] = CLAIMS_DIR


def build_cutoff() -> int | None:
    """The cutoff `set_build_cutoff` last redirected `ENTITIES_DIR` to, or `None` in render mode."""
    return _build_cutoff


# ---------------------------------------------------------------------------
# Per-artifact accessors
# ---------------------------------------------------------------------------


def run_dir(run_id: str) -> Path:
    """data/<series>/_runs/<run_id>/ — one CLI invocation's manifest, call log, and snapshot."""
    return RUNS_DIR / run_id


def step_progress_path() -> Path:
    """data/<series>/_runs/step_progress.json — where `wiki step` last left off. NOT a run
    itself (lives beside `_runs/<run_id>/`, not inside one) and untouched by
    `RunContext.snapshot_before`/`.restore`: rolling back a run's output and then continuing the
    step loop from the same position is the correct combination, not a reason to move it."""
    return RUNS_DIR / "step_progress.json"


def variant_base(series_id: str | None) -> str | None:
    """[33] `<base>@<variant>` (e.g. `anne@b1`, a baseline's output) names a VARIANT of a series:
    the base's config, parsed text and gold, with its own data/dist/runs tree for outputs, so
    every evaluation tool scores a baseline's pages unchanged. Plain ids return None."""
    return series_id.split("@", 1)[0] if series_id and "@" in series_id else None


def series_config(series_id: str) -> Path:
    """config/series.<id>.yaml (a variant `<base>@<v>` reads its base's file)"""
    return CONFIG_DIR / f"series.{variant_base(series_id) or series_id}.yaml"


def models_config() -> Path:
    return CONFIG_DIR / "models.yaml"


def extraction_config() -> Path:
    """config/extraction.yaml — the universal base taxonomy, shared by every series."""
    return CONFIG_DIR / "extraction.yaml"


def eval_gold_dir(series_id: str) -> Path:
    """docs/eval/gold/<series_id>/ — hand-transcribed gold-standard YAML files (Phase 21 part 4,
    `eval/gold.py`), one per character, repo-committed like `config/` rather than per-series
    generated `data/` output (a human transcribes these once from a real reference wiki; nothing
    in the pipeline writes to this directory). Absent for a series with no gold data yet."""
    return PROJECT_ROOT / "docs" / "eval" / "gold" / series_id


def eval_baseline(series_id: str) -> Path:
    """docs/eval/gold/<series_id>/baseline.json — Phase 22 C4: the recorded fact-recall floor per
    gold-covered character, hand-maintained exactly like the gold YAML files it sits beside
    (nothing in the pipeline writes it). `wiki audit eval` FAILs when a character's freshly
    computed fact recall drops below its recorded value here — a real regression gate, distinct
    from `eval/gold.py`'s own lexical recall SIGNAL, which stays advisory for any character with
    no baseline on record. Absent for a series with no recorded baseline yet (not an error)."""
    return eval_gold_dir(series_id) / "baseline.json"


# --- 01 parsed --------------------------------------------------------------


def parsed_volume(vol: int) -> Path:
    """data/01_parsed/v03.jsonl — one record per paragraph. CONTRACTS §1."""
    return PARSED_DIR / f"v{vol:02d}.jsonl"


def parsed_volume_of(series_id: str, vol: int) -> Path:
    """[31] Another series' parsed volume, for a stage that derives one series from another
    (`wiki decontaminate` reads its `from_series` this way) without switching the active one."""
    data = PROJECT_ROOT / "data" / series_id if series_id != DEFAULT_SERIES else PROJECT_ROOT / "data"
    return _stage_dirs(data)["ingest"] / f"v{vol:02d}.jsonl"


def decon_report() -> Path:
    """data/01_parsed/decon_report.json — CONTRACTS §1.6, written by `wiki decontaminate`."""
    return PARSED_DIR / "decon_report.json"


def parsed_manifest() -> Path:
    """data/01_parsed/manifest.json — CONTRACTS §1.3."""
    return PARSED_DIR / "manifest.json"


def gazetteer() -> Path:
    """data/02_entities/gazetteer.json — CONTRACTS §2.1."""
    return ENTITIES_DIR / "gazetteer.json"


def mentions() -> Path:
    """data/02_entities/mentions.jsonl — CONTRACTS §2.2."""
    return ENTITIES_DIR / "mentions.jsonl"


def candidates() -> Path:
    """data/02_entities/candidates.jsonl — CONTRACTS §2.3."""
    return ENTITIES_DIR / "candidates.jsonl"


def surface_forms() -> Path:
    """The deterministic surface vocabulary inventory — CONTRACTS §2.4."""
    return ENTITIES_DIR / "surface_forms.jsonl"


def roster_html() -> Path:
    """The hand-audit artifact for Phase 2. Open this before running extraction."""
    return ENTITIES_DIR / "roster.html"


# --- 02b scenes (Phase 18) ---------------------------------------------------


def scenes_volume(vol: int) -> Path:
    """data/02b_scenes/v03.jsonl — one scene record per chapter span. CONTRACTS §3b."""
    return SCENES_DIR / f"v{vol:02d}.jsonl"


def scene_epithets(vol: int) -> Path:
    """data/02b_scenes/epithets_v03.jsonl — mined epithet mentions for one volume, pending
    merge into gazetteer.json via `wiki gazetteer --merge-epithets`. CONTRACTS §3b.1."""
    return SCENES_DIR / f"epithets_v{vol:02d}.jsonl"


# --- 03 claims --------------------------------------------------------------


def claims_volume(vol: int) -> Path:
    """data/03_claims/v03.jsonl — CONTRACTS §3. Written by `wiki extract` (the mention-window
    pass); as of Phase 22 C2, a targeted top-up over what `scene_claims_volume` already covers."""
    return CLAIMS_DIR / f"v{vol:02d}.jsonl"


def scene_claims_volume(vol: int) -> Path:
    """data/03_claims/scene_v03.jsonl — CONTRACTS §3, same claim shape as `claims_volume`, kept
    in its own file rather than merged into it. Written by `wiki scenes` (Phase 22 C1 — the
    chapter-span pass's participant_facts, now the PRIMARY facts source). `wiki graph build`
    reads both files; a claim_id that happens to appear in both is deduped there, not here."""
    return CLAIMS_DIR / f"scene_v{vol:02d}.jsonl"


def pass_claims_volume(pass_name: str, vol: int) -> Path:
    """data/03_claims/<pass>_v03.jsonl -- CONTRACTS section 3 claims from one `wiki infobox --pass`
    (extract/infobox.py: `infobox`, `backstory`), each in its own file so a pass can be re-run or
    discarded without touching any other. `wiki graph build` reads every claim file."""
    return CLAIMS_DIR / f"{pass_name}_v{vol:02d}.jsonl"


def infobox_claims_volume(vol: int) -> Path:
    return pass_claims_volume("infobox", vol)


# --- 04 graph ---------------------------------------------------------------


def graph_db() -> Path:
    """data/04_graph/graph.db — CONTRACTS §4."""
    return GRAPH_DIR / "graph.db"


def contradictions() -> Path:
    """data/04_graph/contradictions.json — the Phase 4 deliverable. CONTRACTS §4.2."""
    return GRAPH_DIR / "contradictions.json"


def verification() -> Path:
    """data/04_graph/verification.json — Phase 22 C3's deliverable: one entry per character with
    at least one fact flagged as unsupported by its own cited evidence. Written by `wiki verify`,
    read only by `wiki audit verify`. CONTRACTS §4.3."""
    return GRAPH_DIR / "verification.json"


# --- 04b events (Phase 19) --------------------------------------------------


def events_db() -> Path:
    """data/04b_events/events.db — CONTRACTS §4b."""
    return EVENTS_DIR / "events.db"


# --- 05 pages ---------------------------------------------------------------


def page_dir(entity_id: str) -> Path:
    return PAGES_DIR / entity_id


def page_json(entity_id: str, upto_vol: int) -> Path:
    """data/05_pages/<entity_id>/v03.json — CONTRACTS §5."""
    return page_dir(entity_id) / f"v{upto_vol:02d}.json"


def page_markdown(entity_id: str, upto_vol: int) -> Path:
    """data/05_pages/<entity_id>/v03.md — OKF Markdown. CONTRACTS §5.2."""
    return page_dir(entity_id) / f"v{upto_vol:02d}.md"


# --- 06 bundle --------------------------------------------------------------


def bundle_index() -> Path:
    return BUNDLE_DIR / "index.json"


def bundle_page(entity_id: str) -> Path:
    return BUNDLE_DIR / "pages" / f"{entity_id}.json"


def bundle_codex(kind: str) -> Path:
    """kind is a key of `codex_pages` in config/extraction.yaml: factions | places | tech."""
    return BUNDLE_DIR / "codex" / f"{kind}.json"


def bundle_relationship(a_id: str, b_id: str) -> Path:
    """data/06_bundle/relationships/<a>--<b>.json — one file per CHARACTER pair with at least
    one relation edge (Phase 21). `a_id`/`b_id` are sorted here so callers never have to agree
    on an order themselves."""
    a, b = sorted((a_id, b_id))
    return BUNDLE_DIR / "relationships" / f"{a}--{b}.json"


def bundle_timeline(vol: int) -> Path:
    """data/06_bundle/timeline/v{NN}.json — one file per volume that has at least one event
    (Phase 21 part 3), immutable once written: a volume's own events never change on a later
    `--upto` rebuild since `events.vol_start` always equals `vol` (CONTRACTS §4b.1)."""
    return BUNDLE_DIR / "timeline" / f"v{vol:02d}.json"


def bundle_links() -> Path:
    return BUNDLE_DIR / "links.json"


def bundle_search() -> Path:
    return BUNDLE_DIR / "search.json"


# --- MkDocs wiki (Phase 25, site/mkdocs_wiki.py) -----------------------------
# Accessors, not module-scope globals, so each one reads SITE_DIR/DIST_DIR at CALL time and
# follows set_active_series() -- the same reasoning paths.py's own header gives for every other
# per-series path in this file.


def wiki_cutoff_dir(cutoff: int) -> Path:
    """dist/<series>/wiki/v{NN}/ -- one self-contained tree per volume a reader might pick."""
    return SITE_DIR / f"v{cutoff:02d}"


def wiki_html_dir() -> Path:
    """dist/<series>/wiki-html/ -- `mkdocs build`'s rendered output, kept separate from the raw
    .md tree (SITE_DIR) so `wiki serve` can tell "not built yet" from "no markdown emitted"."""
    return DIST_DIR / "wiki-html"


def wiki_mkdocs_yml() -> Path:
    """dist/<series>/mkdocs.yml -- a SIBLING of SITE_DIR (wiki/), not inside it: mkdocs refuses
    `docs_dir` to be its own config file's parent directory, so the config cannot live inside the
    tree it describes (docs/vision/PHASE_25.md's 2026-09-17 entry has the error message)."""
    return DIST_DIR / "mkdocs.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dirs() -> None:
    """Create every directory the pipeline writes to. Safe to call repeatedly."""
    for path in (
        PARSED_DIR,
        ENTITIES_DIR,
        SCENES_DIR,
        CLAIMS_DIR,
        GRAPH_DIR,
        EVENTS_DIR,
        PAGES_DIR,
        BUNDLE_DIR,
        BUNDLE_DIR / "pages",
        BUNDLE_DIR / "codex",
        BUNDLE_DIR / "relationships",
        BUNDLE_DIR / "timeline",
        LLM_CACHE_DIR,
        SITE_DIR,
        RUNS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def relative(path: Path) -> str:
    """Path as a short project-relative string, for log and report output."""
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
