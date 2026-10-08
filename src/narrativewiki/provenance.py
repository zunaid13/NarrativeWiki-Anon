"""Per-invocation run ledger: manifest, per-call log, and a pre-run snapshot for rollback.

Inputs:     a CLI command name, the active series id, a human scope string ('v1-2', 'upto3'),
            and (for the frontier interlock) the highest volume number in play.
Outputs:    data/<series>/_runs/<run_id>/{manifest.json, calls.jsonl, budget.json, outputs/}.
Invariants: - One RunContext per CLI invocation. `cli.py` creates it right before constructing
              that command's LLMClient, and passes it in -- `LLMClient._call` is the only writer
              of `calls.jsonl`, so every model call (cache hit or miss) is logged with its full
              prompt, response, and a timestamp with no extra plumbing in the stage modules
              themselves (extract/claims.py, synth/prose.py, ... never see a RunContext).
            - `snapshot_before` copies the CURRENT, pre-run state of the stage directories this
              invocation is about to write into, so `rollback(run_id)` restores exactly what
              existed immediately before that run -- i.e. undoes it. A real copy, not a
              hardlink: every stage writes its output via `path.open("w")` (truncate-in-place),
              so a hardlinked "snapshot" would share the live file's inode and be silently
              corrupted by the stage's own next write (see `_copy_tree`'s docstring). A snapshot
              failure is recorded on the manifest, never fatal to the pipeline stage that follows.
            - `RunContext.finish()` is idempotent: only the first call sets `finished_ts`, so it
              is safe to call from every exit path (each existing `raise typer.Exit` after client
              construction, plus the success path) without double-writing.
Contract:   docs/CONTRACTS.md §7.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


class ProvenanceError(RuntimeError):
    """A run id does not exist, or its manifest is unreadable."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _slug(text: str, max_len: int = 40) -> str:
    kept = "".join(ch if ch.isalnum() else "-" for ch in text)
    while "--" in kept:
        kept = kept.replace("--", "-")
    kept = kept.strip("-")
    return (kept or "run")[:max_len]


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=paths.PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment-dependent
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _config_hashes(series_id: str, settings: Any = None) -> dict[str, str]:
    """First 12 hex chars of each config file's sha256 -- enough to notice a config edit between
    two runs without storing the whole file. `extraction` is the exception: it hashes the
    MERGED, effective taxonomy (base extraction.yaml + this series' optional overlay file + any
    `--attr`/`--trait`/`--relation`/`--only`/`--skip` CLI overrides on `settings`), not just
    extraction.yaml's own bytes -- otherwise a per-series overlay or an ad-hoc `--attr` run would
    be invisible to `wiki runs`, defeating the reason this hash exists (VISION.md 2026-09-04,
    "Per-series extraction schemas": "which schema produced this claim set" must be answerable).
    `settings` is optional so a caller with no Settings in hand (there is none today, but nothing
    stops one existing) still gets a hash of the on-disk merge."""
    hashes: dict[str, str] = {}
    for name, path in (
        ("series", paths.series_config(series_id)),
        ("models", paths.models_config()),
    ):
        if path.is_file():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]

    if settings is not None:
        payload: dict[str, Any] = {
            "extraction": settings.extraction,
            "schema_overrides": settings.schema_overrides,
            "schema_only": sorted(settings.schema_only),
            "schema_skip": sorted(settings.schema_skip),
        }
    else:
        from .config import load_settings

        payload = {"extraction": load_settings(series_id).extraction}
    hashes["extraction"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return hashes


def _copy_tree(src: Path, dst: Path) -> None:
    """Replace `dst` with a real copy of `src`. Used for both directions: snapshot (live ->
    outputs/) and restore (outputs/ -> live).

    Deliberately a full copy, NOT a hardlink, even though a hardlink would be cheaper on the
    same volume: every stage in this codebase writes its output via `path.open("w")` /
    `path.write_text(...)`, which truncates the EXISTING file in place rather than writing a new
    one and renaming over it. A hardlinked "snapshot" and the live file would then be the same
    inode, so the very next write the stage makes would silently corrupt the snapshot too --
    reproduced and confirmed by `tests/test_provenance.py::
    test_snapshot_before_and_restore_round_trip` before this was a plain copy. Stage output
    (JSONL/JSON) is small; the extra disk cost of a real copy is worth the correctness.
    """
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, copy_function=shutil.copy2)


# ---------------------------------------------------------------------------
# RunContext
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    run_id: str
    dir: Path
    series_id: str
    command: str
    scope: str
    manifest: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _budget: Any = field(default=None, repr=False, compare=False)
    # Lazily seeded from calls.jsonl, then kept in memory -- see frontier_call_count().
    _frontier_calls: int | None = field(default=None, repr=False, compare=False)

    # -- paths ---------------------------------------------------------

    @property
    def calls_path(self) -> Path:
        return self.dir / "calls.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def budget_path(self) -> Path:
        return self.dir / "budget.json"

    @property
    def outputs_dir(self) -> Path:
        return self.dir / "outputs"

    @property
    def yield_path(self) -> Path:
        return self.dir / "yield.json"

    def write_yield(self, per_volume: dict[str, Any]) -> None:
        """Persist per-volume extraction yield -- facts kept and every drop reason, keyed by
        volume. Plan Stage 0.2: `wiki extract`/`wiki scenes` previously computed this exact
        Counter and only `console.print`-ed it, so a prompt/schema change could never be judged
        without re-deriving the numbers by hand from `calls.jsonl`. `calls`/token/cost totals
        already live in `budget.json`; this file adds what that one doesn't have."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.yield_path.write_text(
            json.dumps(per_volume, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    @property
    def audit_dir(self) -> Path:
        return self.dir / "audit"

    # -- calls -----------------------------------------------------------

    def log_call(self, record: dict[str, Any]) -> None:
        """Append one LLM call record. The only caller is llm/client.py::LLMClient._call, for
        both a cache hit and a real call -- a hit is the proof no token was re-spent, which is
        exactly what makes 'search the log before re-asking' (wiki calls) actually useful."""
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.calls_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if record.get("tier") == "frontier" and self._frontier_calls is not None:
                self._frontier_calls += 1

    def frontier_call_count(self) -> int:
        """How many frontier-tier calls this run has made so far. Cache hits count too -- a hit
        still means the run *asked*, which is what the call-count interlock protects against.

        Seeded once from calls.jsonl, then counted in memory. It used to re-parse the whole file
        on EVERY frontier call, and those lines carry full prompts -- O(n^2) over a file that
        reaches tens of MB in a real run, so the interlock got slower the longer the run ran.
        """
        with self._lock:
            if self._frontier_calls is None:
                self._frontier_calls = self._count_frontier_calls_on_disk()
            return self._frontier_calls

    def _count_frontier_calls_on_disk(self) -> int:
        if not self.calls_path.is_file():
            return 0
        count = 0
        with self.calls_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:  # pragma: no cover - defensive
                    continue
                if rec.get("tier") == "frontier":
                    count += 1
        return count

    # -- budget ------------------------------------------------------------

    def attach_budget(self, budget: Any) -> None:
        """Called once by LLMClient.__init__ when constructed with `run=this context`, so
        `finish()` can persist the run's own ledger without cli.py having to do it explicitly at
        every one of the five command bodies that construct a client."""
        self._budget = budget

    # -- lifecycle -----------------------------------------------------------

    def finish(self, outcome: str, **extra: Any) -> None:
        """Idempotent: only the FIRST call sets finished_ts. Safe to call from every existing
        exit point in a command body (each `raise typer.Exit` after client construction, plus
        the success path at the end) without double-writing or double-counting the budget."""
        if self.manifest.get("finished_ts"):
            return
        self.manifest["outcome"] = outcome
        self.manifest["finished_ts"] = _iso(_now())
        if extra:
            self.manifest.update(extra)
        self._save_manifest()

        if self._budget is not None:
            self._budget.save(self.budget_path)
            try:
                from .llm.budget import Budget

                # A series-scoped cumulative total, NOT llm/budget.py's own unsegmented default
                # (data/cache/llm/budget.json) -- that path backs the LLM response cache, which
                # is deliberately shared across every series (paths.py), but cost/token totals
                # must not mix two different series' spend into one number.
                cumulative_path = paths.DATA_DIR / "budget.json"
                cumulative = Budget.load(cumulative_path)
                cumulative.accumulate(self._budget)
                cumulative.save(cumulative_path)
            except Exception:  # pragma: no cover - defensive; must never break a finished run
                pass

    def _save_manifest(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # -- snapshot / rollback -------------------------------------------------

    def snapshot_before(self, stage_keys: list[str]) -> None:
        """Copy the CURRENT (pre-run) state of the named stage directories into outputs/. `key`
        is a `paths.STAGE_DIRS` key, or the literal "site" for `paths.SITE_DIR`. Never raises --
        a copy failure is recorded on the manifest and the run proceeds regardless."""
        snapshotted: list[str] = []
        errors: list[str] = []
        for key in stage_keys:
            source = paths.SITE_DIR if key == "site" else paths.STAGE_DIRS.get(key)
            if source is None or not source.is_dir() or not any(source.iterdir()):
                continue  # nothing to protect yet -- a first-ever run has nothing to snapshot
            try:
                _copy_tree(source, self.outputs_dir / key)
                snapshotted.append(key)
            except OSError as exc:  # pragma: no cover - filesystem-dependent
                errors.append(f"{key}: {exc}")
        self.manifest["snapshotted_stages"] = snapshotted
        if errors:
            self.manifest["snapshot_errors"] = errors
        self._save_manifest()

    def restore(self) -> list[str]:
        """Copy outputs/<key> back over the live stage directory, for each stage this run
        snapshotted -- undoing whatever this run wrote. Used by `wiki rollback`."""
        previous_cutoff = paths.build_cutoff()
        target_cutoff = self.manifest.get("build_cutoff", previous_cutoff)
        if target_cutoff is not None and (type(target_cutoff) is not int or target_cutoff < 1):
            raise ProvenanceError(f"Invalid build_cutoff in run {self.run_id}: {target_cutoff!r}")
        changed_scope = target_cutoff != previous_cutoff
        try:
            if changed_scope:
                paths.set_build_cutoff(target_cutoff)
            restored: list[str] = []
            for key in self.manifest.get("snapshotted_stages", []):
                src = self.outputs_dir / key
                dest = paths.SITE_DIR if key == "site" else paths.STAGE_DIRS.get(key)
                if dest is None or not src.is_dir():
                    continue
                _copy_tree(src, dest)
                restored.append(key)
            if restored:
                # Its writes are undone, so it no longer speaks for any stage's current content:
                # `last_run_for_stage` skips it, and a failed run stops blocking once rolled back.
                self.manifest["rolled_back_ts"] = _iso(_now())
                self._save_manifest()
            return restored
        finally:
            if changed_scope:
                paths.set_build_cutoff(previous_cutoff)


# ---------------------------------------------------------------------------
# Module-level entry points
# ---------------------------------------------------------------------------


def start_run(
    series_id: str,
    command: str,
    *,
    scope: str,
    volumes: list[int] | None = None,
    volume_scope: int | None = None,
    argv: list[str] | None = None,
    stage_keys: list[str] | None = None,
    settings: Any = None,
) -> RunContext:
    """Create data/<series>/_runs/<run_id>/, write its initial manifest, and snapshot whatever
    stage directories `stage_keys` names before the caller writes to them.

    `run_id` is `<UTC timestamp>-<command>-<scope-slug>` -- sortable, and self-describing enough
    to recognise in `wiki runs` without opening the manifest. A same-second collision (two
    commands started in the same wall-clock second) gets a `-2`, `-3`, ... suffix rather than
    overwriting the earlier run's directory.

    `settings` (optional): the already-loaded, already-`--model`/`--attr`/`--only`-etc-applied
    `Settings` for this invocation, if the caller has one -- passed straight through to
    `_config_hashes` so the manifest's `extraction` hash reflects what this specific run actually
    used, not just what's on disk. Every existing stage command in cli.py has one in scope at the
    point it calls this.
    """
    ts = _now()
    base_id = f"{ts:%Y%m%dT%H%M%S}-{command}-{_slug(scope)}"
    run_dir = paths.run_dir(base_id)
    n = 2
    while run_dir.exists():
        run_dir = paths.run_dir(f"{base_id}-{n}")
        n += 1
    run_id = run_dir.name

    manifest = {
        "run_id": run_id,
        "series_id": series_id,
        "command": command,
        "scope": scope,
        "volumes": volumes,
        "volume_scope": volume_scope,
        "build_cutoff": paths.build_cutoff(),
        "argv": argv if argv is not None else sys.argv,
        "started_ts": ts.isoformat(timespec="microseconds"),
        "finished_ts": None,
        "outcome": "running",
        "git_sha": _git_sha(),
        "config_hashes": _config_hashes(series_id, settings),
    }

    ctx = RunContext(
        run_id=run_id, dir=run_dir, series_id=series_id, command=command, scope=scope,
        manifest=manifest,
    )
    ctx._save_manifest()  # noqa: SLF001 - provenance.py and RunContext are one unit
    if stage_keys:
        ctx.snapshot_before(stage_keys)
    return ctx


def _run_order(manifest: dict[str, Any]) -> tuple:
    """Start time first; deterministic natural ID order breaks legacy timestamp ties."""
    run_id = manifest["run_id"]
    try:
        started = datetime.fromisoformat(manifest["started_ts"])
    except (KeyError, TypeError, ValueError):
        try:
            started = datetime.strptime(run_id.split("-", 1)[0], "%Y%m%dT%H%M%S")
        except ValueError:
            started = datetime.min
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    natural_id = tuple((1, int(part)) if part.isdigit() else (0, part)
                       for part in re.split(r"(\d+)", run_id))
    return started, natural_id


def list_runs(limit: int | None = None) -> list[dict[str, Any]]:
    """Every active-series manifest, newest start first (not command/scope name order)."""
    root = paths.RUNS_DIR
    if not root.is_dir():
        return []
    manifests = []
    for entry in (p for p in root.iterdir() if p.is_dir()):
        mpath = entry / "manifest.json"
        if not mpath.is_file():
            continue
        try:
            manifests.append(json.loads(mpath.read_text(encoding="utf-8")))
        except json.JSONDecodeError:  # pragma: no cover - defensive
            continue
    manifests.sort(key=_run_order, reverse=True)
    return manifests[:limit] if limit else manifests


def latest_run_id() -> str | None:
    runs = list_runs(limit=1)
    return runs[0]["run_id"] if runs else None


def get_run(run_id: str) -> RunContext:
    """Load an existing run's manifest back into a RunContext, for `wiki calls`/`wiki rollback`."""
    run_dir = paths.run_dir(run_id)
    mpath = run_dir / "manifest.json"
    if not mpath.is_file():
        raise ProvenanceError(
            f"No run {run_id!r} found under {paths.relative(paths.RUNS_DIR)}. "
            f"See `wiki runs` for valid ids."
        )
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    return RunContext(
        run_id=run_id,
        dir=run_dir,
        series_id=manifest.get("series_id", ""),
        command=manifest.get("command", ""),
        scope=manifest.get("scope", ""),
        manifest=manifest,
    )


def load_step_progress() -> dict[str, int] | None:
    """{"vol": int, "chapter_idx": int} — the last increment `wiki step` completed for the
    currently active series, or None if it has never run."""
    path = paths.step_progress_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_step_progress(vol: int, chapter_idx: int) -> None:
    """Record the increment `wiki step` just completed. Called only after the full
    extract/graph_build/synthesize/site_build chain for (vol, chapter_idx) succeeds -- a failed
    step never advances this, so the same increment is retried next."""
    path = paths.step_progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"vol": vol, "chapter_idx": chapter_idx}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_calls(
    run_id: str, *, stage: str | None = None, grep: str | None = None
) -> list[dict[str, Any]]:
    """Every logged call for a run, optionally filtered by stage name and/or a case-insensitive
    substring search over the prompt and response text -- the practical form of 'search past
    prompts before re-asking' the user's operating rules call for."""
    ctx = get_run(run_id)
    if not ctx.calls_path.is_file():
        return []
    needle = grep.lower() if grep else None
    records = []
    with ctx.calls_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:  # pragma: no cover - defensive
                continue
            if stage and rec.get("stage") != stage:
                continue
            if needle and needle not in (rec.get("prompt", "") + rec.get("response", "")).lower():
                continue
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Failed-run quarantine (Phase 22 A1)
# ---------------------------------------------------------------------------

# Which stage_keys each command's start_run(...) call writes -- mirrors the literal
# `stage_keys=[...]` argument at that command's call site in cli.py. Only used to answer "did the
# most recent run that touched this stage crash" -- a stale entry here (a new command added
# without updating this table) just means a crash on that command goes undetected by the guard,
# never a false abort, so it is safe for this to fall a little behind cli.py.
COMMAND_STAGE_KEYS: dict[str, list[str]] = {
    "decontaminate": ["ingest"],   # [31]
    "gazetteer": ["gazetteer"],
    "scenes": ["scenes"],
    "extract": ["claims"],
    "graph_build": ["graph"],
    "events_build": ["events"],
    "synthesize": ["pages"],
    "site_build": ["bundle", "site"],
    "verify": ["verify"],
    # [24] "probe" is not a paths.STAGE_DIRS key (probe output lives under
    # data/<series>/_runs/<run_id>/probe/, not a fixed stage directory -- see
    # probe/report.py::probe_report_path) -- listed here only so `last_run_for_stage("probe")`
    # (used by audit/reports.py's probe report) can find the latest probe run.
    "probe": ["probe"],
}


def last_run_for_stage(stage_key: str) -> dict[str, Any] | None:
    """The most recent run manifest (current series, newest first) whose command writes
    `stage_key` and was not rolled back, or None if no such run has ever happened."""
    for manifest in list_runs():
        if manifest.get("rolled_back_ts"):
            continue
        if (stage_key in {"gazetteer", "claims"}
                and manifest.get("build_cutoff") != paths.build_cutoff()):
            continue
        if stage_key in COMMAND_STAGE_KEYS.get(manifest.get("command", ""), ()):
            return manifest
    return None


def failed_stage_artifacts(stage_keys: list[str]) -> list[dict[str, Any]]:
    """One manifest per name in `stage_keys` whose most recent writer recorded `outcome: failed`
    -- i.e. every stage currently at risk of holding a crashed run's partial output. Empty when
    every named stage's last writer finished cleanly, or has never run. A run that writes more
    than one of `stage_keys` (e.g. site_build writes both "bundle" and "site") is reported once.

    This is the check `wiki extract`/`scenes`/`graph build`/`synthesize`/`site build` run against
    their own input stages before doing any work -- the crash that fed a whole later pipeline from
    `extract`'s partial claims file before anyone noticed (docs/vision/PHASE_22.md, 2026-09-09
    audit, defect list item 1)."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in stage_keys:
        manifest = last_run_for_stage(key)
        if manifest and manifest.get("outcome") == "failed" and manifest["run_id"] not in seen:
            found.append(manifest)
            seen.add(manifest["run_id"])
    return found


def search_calls_all_runs(needle: str, *, limit_runs: int | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Every logged call, across every run recorded for the currently active series, whose
    prompt or response mentions `needle` (case-insensitive) -- the cross-run form of
    `read_calls`'s `grep`. `wiki calls --grep <text>` only searches ONE run at a time (default:
    the most recent); `wiki trace <entity>` needs "every call that ever discussed this entity",
    which means walking every run's `calls.jsonl`, not just the latest one."""
    hits: list[tuple[str, dict[str, Any]]] = []
    for m in list_runs(limit=limit_runs):
        run_id = m.get("run_id")
        if not run_id:
            continue
        for rec in read_calls(run_id, grep=needle):
            hits.append((run_id, rec))
    return hits
