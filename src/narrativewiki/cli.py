"""The `wiki` command. Every CLI entry point is registered here.

Inputs:     Command-line arguments.
Outputs:    Console output; stage modules do the actual work.
Invariants: - This module orchestrates and reports. It contains no pipeline logic.
Contract:   USER_GUIDE.md is the user-facing documentation for everything here. Keep them
            in step: a new command means a new USER_GUIDE.md entry in the same commit.
"""

from __future__ import annotations

import shutil
from collections import Counter
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import paths, provenance
from .config import (
    ConfigError,
    apply_model_overrides,
    apply_schema_overrides,
    apply_set_overrides,
    available_series,
    load_settings,
)

# The corpus is full of characters cp1252 cannot encode — the series title alone contains an
# em dash, and names like Milizé and Vánagandr are everywhere. Without this, a Windows console
# raises UnicodeEncodeError mid-report. Do it before any Console is constructed.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

app = typer.Typer(
    name="wiki",
    help="Build a spoiler-scoped character wiki from a novel series.",
    no_args_is_help=True,
    add_completion=False,
)
graph_app = typer.Typer(help="Temporal knowledge graph commands.", no_args_is_help=True)
site_app = typer.Typer(help="Site build commands.", no_args_is_help=True)
probe_app = typer.Typer(help="[Phase 24] Spoiler-leak channel measurements.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")
app.add_typer(site_app, name="site")
app.add_typer(probe_app, name="probe")

console = Console()

SeriesOpt = Annotated[
    Optional[str],
    typer.Option(
        "--series", "-s",
        help="Series config to use (config/series.<id>.yaml). Defaults to spice-and-wolf -- "
        "the only series in active use right now.",
    ),
]
VolumesOpt = Annotated[
    Optional[str], typer.Option("--volumes", "-v", help="Volumes to process: '1-13', '1,3,5', or '7'.")
]
ChaptersOpt = Annotated[
    Optional[str],
    typer.Option(
        "--chapters",
        "-c",
        help="Chapters within the selected volume: '1-4', '2,5', or '3'. Requires --volumes to "
        "name exactly one volume.",
    ),
]
UptoOpt = Annotated[
    Optional[int],
    typer.Option(
        "--upto", "-u",
        help="Spoiler boundary: render as a reader who finished this volume. Defaults to this "
        "series' `site.default_upto` (config/series.<id>.yaml), or 1 if that key is unset.",
    ),
]
ForceOpt = Annotated[bool, typer.Option("--force", "-f", help="Redo work that is already done.")]
ModelOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--model",
        "-m",
        help="Override which model a stage uses: '<role>' (every stage), '<stage>=<role>' "
        "(one stage), or '<stage>=<provider>:<model>' for a model with no profile yet. "
        "Repeatable. See config/models.yaml for stage names and defined roles.",
    ),
]
SetOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--set",
        "-S",
        help="Override any config value for this run: '<root>.<key>[.<key>...]=<value>', where "
        "<root> is series, models or extraction. The value is parsed as YAML, so 6 is an int, "
        "0.5 a float, true a bool, [A, B] a list and null deletes the key. Repeatable. "
        "Example: -S extraction.page_outline.history.max_sentences=6",
    ),
]
AttrOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--attr",
        help='Add or override one attribute predicate ad hoc: \'NAME=format:"Display"[:multi]\'. '
        "Repeatable. Default is single-valued; append ':multi' for a TITLE/NICKNAME-shaped "
        "predicate that can hold more than one value. Not written to any config file — see "
        "config/extraction.<series>.yaml for a permanent addition.",
    ),
]
TraitOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--trait",
        help='Add or override one trait predicate ad hoc: \'NAME="Display"[:max_words]\'. Repeatable.',
    ),
]
RelationOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--relation",
        help='Add or override one relation predicate ad hoc: \'NAME="Display"[:symmetric|:inverse=OTHER]\'. '
        "Repeatable.",
    ),
]
OnlyOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--only",
        help="Restrict extraction to these predicate names (e.g. AGE) or kinds (attribute/"
        "relation/trait). Repeatable; everything not named is excluded.",
    ),
]
SkipOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--skip",
        help="Exclude these predicate names or kinds (attribute/relation/trait) from extraction. "
        "Repeatable.",
    ),
]
AllowPartialOpt = Annotated[
    bool,
    typer.Option(
        "--allow-partial",
        help="Proceed even though the most recent run for an input stage failed, or a volume's "
        "claims look incomplete. Use only after confirming the partial data is actually fine.",
    ),
]



# 2026-09-17 standing instruction: spice-and-wolf is the only series in active use until its
# pipeline+eval+benchmark work is complete end-to-end; 86 is not to be touched, and Overlord
# comes only after spice-and-wolf's project is done. This is a CLI-default override, not a
# change to `config.DEFAULT_SERIES`/`paths.DEFAULT_SERIES` -- those stay "86" because that is
# genuinely which series owns the unsegmented `data/`/`dist/` directory (the original corpus
# already built there; see paths.py::set_active_series's own docstring on why that must never
# move). Every `wiki` command always passes an explicit series id from here on, so that
# unsegmented-directory legacy fact stops being reachable through everyday use.
_ACTIVE_SERIES_DEFAULT = "spice-and-wolf"


def _settings(series: str | None, model: list[str] | None = None, set_: list[str] | None = None):
    try:
        settings = load_settings(series or _ACTIVE_SERIES_DEFAULT)
        paths.set_active_series(settings.series_id)
        for warning in apply_model_overrides(settings, model):
            console.print(f"[yellow]{warning}[/yellow]")
        for note in apply_set_overrides(settings, set_):
            console.print(f"[cyan]{note}[/cyan]")
        return settings
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        if known := available_series():
            console.print(f"Available series: {', '.join(known)}")
        raise typer.Exit(code=1) from exc


def _resolve_upto(settings, upto: int | None) -> int:
    """`--upto` falls through to this series' `site.default_upto` (config/series.<id>.yaml), then
    to 1, when not given on the command line -- the CLI default used to be hardcoded `1` in five
    commands independently, which left `site.default_upto` dead config no edit could reach."""
    if upto is not None:
        return upto
    return int(settings.series.get("site", {}).get("default_upto") or 1)


def _guard_upstream_failures(input_stage_keys: list[str], allow_partial: bool) -> None:
    """Abort if the most recent run that wrote any of `input_stage_keys` recorded
    `outcome: failed` -- the crash that fed `graph build`/`synthesize`/`site build` from a stage's
    partial output before anyone noticed (docs/vision/PHASE_22.md A1). Called as the first thing
    every stage command does after loading settings, before any file-existence check, so the user
    sees WHY a stage looks empty/short rather than just "not found. Run X first." `--allow-partial`
    bypasses this without even asking provenance -- so a scan of every run's manifest is skipped
    entirely on the common, all-clean path only when a caller actually wants the bypass, not as a
    performance concern.
    """
    if allow_partial:
        return
    failed = provenance.failed_stage_artifacts(input_stage_keys)
    if not failed:
        return
    for manifest in failed:
        console.print(
            f"[red]{manifest['run_id']}[/red] (writes [cyan]{manifest['command']}[/cyan]) "
            f"failed: {manifest.get('error', 'no error recorded')}"
        )
    console.print(
        "\n[red]Refusing to build on a stage whose last run failed.[/red] Inspect with "
        f"[cyan]wiki calls {failed[0]['run_id']}[/cyan], then either roll it back with "
        f"[cyan]wiki rollback {failed[0]['run_id']} --yes[/cyan] and redo it, or pass "
        "[cyan]--allow-partial[/cyan] to proceed anyway."
    )
    raise typer.Exit(code=1)


def _incomplete_claim_volumes(
    claims: list[dict], mentions: list[dict], characters_by_id: dict[str, dict], claimed_vols: set[int],
    min_mentions: int = 1,
) -> dict[int, list[str]]:
    """{volume: [canonical names]} for every already-extracted volume (one in `claimed_vols`)
    where a gazetteer CHARACTER has a mention that volume but never appears as a claim `subject`
    there -- the signature a crashed `wiki extract` run leaves (docs/vision/PHASE_22.md A1). Pure
    and file-free so it is testable without real gazetteer/claims/mentions fixtures."""
    # [33] `min_mentions`: a character named once in a list ("Brain Unglaus", Overlord v2, a
    # main character only from v3) correctly gets no claim; the crash signature is a character
    # the volume is actually about. A crashed run itself is caught by _guard_upstream_failures.
    counts: Counter[tuple[int, str]] = Counter(
        (m["vol"], m["entity_id"]) for m in mentions
        if m["vol"] in claimed_vols and m["entity_id"] in characters_by_id)
    mentioned_by_vol: dict[int, set[str]] = {}
    for (vol, eid), n in counts.items():
        if n >= min_mentions:
            mentioned_by_vol.setdefault(vol, set()).add(eid)
    claimed_by_vol: dict[int, set[str]] = {}
    for c in claims:
        claimed_by_vol.setdefault(c["first_vol"], set()).add(c["subject"])

    incomplete: dict[int, list[str]] = {}
    for vol, mentioned in mentioned_by_vol.items():
        missing = mentioned - claimed_by_vol.get(vol, set())
        if missing:
            incomplete[vol] = sorted(characters_by_id[eid]["canonical"] for eid in missing)
    return incomplete


def _extract_finished_ok(vol: int) -> bool:
    """True when the newest `wiki extract` run covering `vol` (this series and build cutoff, not
    rolled back) recorded `outcome: ok`."""
    for m in provenance.list_runs():
        if (m.get("command") == "extract" and vol in (m.get("volumes") or [])
                and not m.get("rolled_back_ts") and m.get("build_cutoff") == paths.build_cutoff()):
            return m.get("outcome") == "ok"
    return False


def _find_character(gaz: dict, name: str) -> dict:
    """Resolve a `wiki explain` argument to one CHARACTER gazetteer entry. Only CHARACTER gets a
    unique page (CLAUDE.md §2) — anything else belongs on a codex page, not `explain`."""
    characters = [e for e in gaz["entities"] if e["type"] == "CHARACTER"]
    needle = name.strip().lower()

    for e in characters:
        if e["entity_id"] == needle or e["canonical"].lower() == needle:
            return e

    alias_matches = [e for e in characters if needle in {a.lower() for a in e.get("aliases", [])}]
    if len(alias_matches) == 1:
        return alias_matches[0]

    partial = [e for e in characters if needle in e["canonical"].lower()]
    if len(partial) == 1:
        return partial[0]

    candidates = alias_matches or partial
    if candidates:
        names = ", ".join(sorted({e["canonical"] for e in candidates}))
        console.print(f"[red]'{name}' is ambiguous.[/red] Matches: {names}")
    else:
        console.print(f"[red]No character matches '{name}'.[/red]")
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    series: SeriesOpt = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    attr: AttrOpt = None,
    trait: TraitOpt = None,
    relation: RelationOpt = None,
    only: OnlyOpt = None,
    skip: SkipOpt = None,
) -> None:
    """Check that everything needed to run the pipeline is present and reachable.

    Run this first. It exists so a missing model or an unreadable corpus is a five-second
    discovery rather than something you learn three hours into an extraction run. Also prints the
    effective extraction taxonomy for `--series` (base + any per-series
    `config/extraction.<series>.yaml` overlay + any `--attr`/`--only`/`--skip` given here) —
    "a silently wrong schema is otherwise only discovered by reading generated pages" (VISION.md
    2026-09-04).
    """
    table = Table(title="narrativewiki readiness", header_style="bold")
    table.add_column("Check", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail")

    failures = 0
    warnings = 0

    def row(name: str, ok: bool | None, detail: str) -> None:
        nonlocal failures, warnings
        if ok is True:
            table.add_row(name, "[green]ok[/green]", detail)
        elif ok is None:
            table.add_row(name, "[yellow]optional[/yellow]", detail)
            warnings += 1
        else:
            table.add_row(name, "[red]missing[/red]", detail)
            failures += 1

    # --- runtime ----------------------------------------------------------
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    row("Python", sys.version_info >= (3, 11), f"{version} (need >= 3.11)")

    for package, label in [
        ("ebooklib", "EbookLib"),
        ("bs4", "beautifulsoup4"),
        ("lxml", "lxml"),
        ("networkx", "networkx"),
        ("rapidfuzz", "RapidFuzz"),
        ("pydantic", "pydantic"),
        ("httpx", "httpx"),
        ("yaml", "PyYAML"),
        ("jinja2", "Jinja2"),
    ]:
        try:
            __import__(package)
            row(label, True, "installed")
        except ImportError:
            row(label, False, "run: pip install -e .")

    # Aho-Corasick: pyahocorasick (C ext) if present, else entities/automaton.py's own hand-rolled
    # naive multi-substring scan -- there is no ahocorapy tier; that package was declared as a
    # dependency and checked for here without ever being imported by automaton.py itself
    # (docs/vision/plans/0008-pre-full-scale-audit.md Sec3.6 item 2), so this used to report a
    # fallback that would never actually run. Removed from pyproject.toml alongside this fix.
    try:
        import ahocorasick  # noqa: F401

        row("Aho-Corasick", True, "pyahocorasick (C extension)")
    except ImportError:
        row("Aho-Corasick", True, "naive Python fallback (correct, slower -- pip install pyahocorasick to speed this up)")

    # --- configuration and corpus ----------------------------------------
    settings = _settings(series, model, set_)
    for warning in apply_schema_overrides(settings, attrs=attr, traits=trait, relations=relation, only=only, skip=skip):
        console.print(f"[yellow]{warning}[/yellow]")
    row("Series config", True, f"{settings.series_id} — {settings.series_title}")

    try:
        volumes = settings.discover_volumes()
        numbers = [v.vol for v in volumes]
        gaps = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
        detail = f"{len(volumes)} volume(s): {min(numbers)}–{max(numbers)}"
        if gaps:
            detail += f"  [yellow](missing {gaps})[/yellow]"
        row("Source volumes", True, detail)
    except ConfigError as exc:
        row("Source volumes", False, str(exc).splitlines()[0])

    # --- models -----------------------------------------------------------
    from .llm.client import LLMClient

    client = LLMClient(settings)
    checked: set[str] = set()
    for stage, entry in settings.models.get("routing", {}).items():
        role = entry.get("role") if isinstance(entry, dict) else str(entry)
        fallback = entry.get("fallback") if isinstance(entry, dict) else None
        if role in checked:
            continue
        checked.add(role)
        health = client.health(role)
        optional = bool(fallback) and not health.ok
        row(
            f"model: {role}",
            True if health.ok else (None if optional else False),
            f"{health.detail}" + (f" -> falls back to {fallback}" if optional else ""),
        )

    # A --model override can point a stage at a role, or an ad-hoc profile, that the loop above
    # never checked (it only walks config/models.yaml's static routing table) -- probe those too.
    for stage, adhoc in settings.adhoc_profiles.items():
        health = client.health_profile(adhoc)
        row(f"model: {adhoc} (--model override)", health.ok, health.detail)
    for stage, role in settings.model_overrides.items():
        if role in checked:
            continue
        checked.add(role)
        health = client.health(role)
        row(f"model: {role} (--model override)", health.ok, health.detail)

    # Which model each stage would actually use right now.
    routed = []
    for stage in settings.models.get("routing", {}):
        try:
            profile = settings.resolve_role(stage)
            overridden = stage in settings.adhoc_profiles or stage in settings.model_overrides
            overridden = overridden or "*" in settings.adhoc_profiles or "*" in settings.model_overrides
            suffix = "  [yellow](--model override)[/yellow]" if overridden else ""
            routed.append(f"{stage} -> {profile}{suffix}")
        except ConfigError as exc:
            routed.append(f"{stage} -> [red]{exc}[/red]")

    # --- writable data dirs and disk -------------------------------------
    try:
        paths.ensure_dirs()
        row("Data directories", True, paths.relative(paths.DATA_DIR))
    except OSError as exc:
        row("Data directories", False, f"cannot create {paths.relative(paths.DATA_DIR)}: {exc}")

    free_gb = shutil.disk_usage(paths.PROJECT_ROOT).free / 1e9
    row("Disk space", free_gb > 5, f"{free_gb:.1f} GB free (want > 5 GB)")

    # Effective extraction taxonomy: extraction.yaml + any --attr/--trait/--relation/--only/--skip
    # given on THIS invocation.
    taxonomy_lines = [
        f"entity_types: {', '.join(sorted(settings.entity_types)) or '(none)'}",
        f"attributes ({len(settings.attributes)}): {', '.join(sorted(settings.attributes)) or '(none)'}",
        f"relations ({len(settings.relations)}): {', '.join(sorted(settings.relations)) or '(none)'}",
        f"traits ({len(settings.traits)}): {', '.join(sorted(settings.traits)) or '(none)'}",
    ]
    if settings.schema_overrides or settings.schema_only or settings.schema_skip:
        taxonomy_lines.append(
            "[yellow]CLI overrides applied this run — not persisted to any config file[/yellow]"
        )

    console.print(table)
    console.print(
        Panel("\n".join(routed), title="Stage -> model routing", border_style="blue", expand=False)
    )
    console.print(
        Panel(
            "\n".join(taxonomy_lines),
            title=f"Effective extraction taxonomy ({settings.series_id})",
            border_style="blue",
            expand=False,
        )
    )

    if failures:
        console.print(f"\n[red]{failures} required check(s) failed.[/red] Fix these before running.")
        raise typer.Exit(code=1)
    if warnings:
        console.print(
            f"\n[green]Ready.[/green] {warnings} optional item(s) unavailable — "
            f"those stages fall back to the local model."
        )
    else:
        console.print("\n[green]Ready.[/green] All checks passed.")


# ---------------------------------------------------------------------------
# status / budget
# ---------------------------------------------------------------------------


@app.command()
def status(series: SeriesOpt = None, set_: SetOpt = None) -> None:
    """Show which stages are complete and what to run next."""
    settings = _settings(series, set_=set_)

    table = Table(title=f"Pipeline status — {settings.series_title}", header_style="bold")
    table.add_column("Stage", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Output")

    # series-agnostic: derive the example commands' volume range from this series' own config
    # instead of hard-coding 86's 13 volumes (docs/vision/plans/0008-pre-full-scale-audit.md Sec3.6
    # item 3 -- these were an 86-ism in an otherwise series-agnostic command).
    last_vol = max(settings.volume_numbers(), default=13)
    next_command: str | None = None
    commands = {
        "ingest": f"wiki ingest --volumes 1-{last_vol}",
        "gazetteer": f"wiki gazetteer --volumes 1-{last_vol}",
        "scenes": f"wiki scenes --volumes 1-{last_vol}",
        "claims": f"wiki extract --volumes 1-{last_vol}",
        "graph": "wiki graph build",
        "pages": f"wiki synthesize --upto {last_vol}",
        "bundle": "wiki site build",
    }

    for stage, directory in paths.STAGE_DIRS.items():
        files = sorted(p for p in directory.rglob("*") if p.is_file()) if directory.is_dir() else []
        if files:
            newest = max(p.stat().st_mtime for p in files)
            import datetime as _dt

            when = _dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
            table.add_row(stage, "[green]done[/green]", f"{len(files)} file(s), newest {when}")
        else:
            table.add_row(stage, "[dim]pending[/dim]", paths.relative(directory))
            next_command = next_command or commands.get(stage)

    console.print(table)
    if next_command:
        console.print(f"\nNext: [cyan]{next_command}[/cyan]")
    else:
        console.print("\n[green]All stages have output.[/green] Next: [cyan]wiki serve[/cyan]")


@app.command()
def budget(
    series: SeriesOpt = None,
    run: Annotated[Optional[str], typer.Option(help="Show one run's own numbers instead of the series-wide cumulative total. See `wiki runs`.")] = None,
) -> None:
    """Show tokens and estimated cost per stage, cumulative across every run for this series
    (or, with --run, for just one run — see `wiki runs`)."""
    _settings(series)
    from .llm.budget import Budget

    if run:
        try:
            ctx = provenance.get_run(run)
        except provenance.ProvenanceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        ledger = Budget.load(ctx.budget_path)
    else:
        ledger = Budget.load(paths.DATA_DIR / "budget.json")
    if not ledger.stages:
        console.print("No budget recorded yet. Run a stage that calls a model first.")
        return

    table = Table(title="Token usage and cost", header_style="bold")
    for column in ("Stage", "Calls", "Cached", "In", "Out", "Cost"):
        table.add_column(column, justify="right" if column != "Stage" else "left")
    for row in ledger.summary_rows():
        table.add_row(*row)

    tokens_in, tokens_out = ledger.total_tokens
    table.add_section()
    table.add_row(
        "[bold]total[/bold]",
        "",
        "",
        f"[bold]{tokens_in:,}[/bold]",
        f"[bold]{tokens_out:,}[/bold]",
        f"[bold]{'free' if ledger.total_cost == 0 else f'${ledger.total_cost:.2f}'}[/bold]",
    )
    console.print(table)


@app.command()
def runs(
    series: SeriesOpt = None,
    limit: Annotated[int, typer.Option(help="Max runs to show.")] = 20,
) -> None:
    """[Phase 10] List past pipeline runs for this series, newest first.

    Every command that writes to a stage directory (gazetteer/extract/graph build/synthesize/
    site build) starts a run: a manifest, a per-call log of every model prompt and response with
    a timestamp, and a snapshot of what the stage directory looked like right before it ran. Use
    `wiki calls --run <id>` to read what was actually asked, and `wiki rollback <id>` to undo it.
    """
    _settings(series)
    manifests = provenance.list_runs(limit=limit)
    if not manifests:
        console.print("[dim]No runs recorded yet.[/dim]")
        return

    table = Table(title="Runs (newest first)", header_style="bold")
    for column in ("Run", "Command", "Scope", "Outcome", "Started", "Snapshotted"):
        table.add_column(column, no_wrap=(column not in ("Run",)))
    for m in manifests:
        outcome = m.get("outcome") or "?"
        if not m.get("finished_ts"):
            outcome, color = "incomplete", "yellow"
        elif outcome == "ok":
            color = "green"
        elif outcome == "failed":
            color = "red"
        else:
            color = "yellow"
        table.add_row(
            m.get("run_id", ""),
            m.get("command", ""),
            m.get("scope", ""),
            f"[{color}]{outcome}[/{color}]",
            (m.get("started_ts") or "")[:19],
            ", ".join(m.get("snapshotted_stages", [])) or "-",
        )
    console.print(table)


@app.command()
def calls(
    run: Annotated[Optional[str], typer.Option(help="Run id from `wiki runs` (default: the most recent run).")] = None,
    stage: Annotated[Optional[str], typer.Option(help="Only this stage's calls, e.g. 'prose' or 'claim_extract'.")] = None,
    grep: Annotated[Optional[str], typer.Option(help="Only calls whose prompt or response contains this text.")] = None,
    limit: Annotated[int, typer.Option(help="Max calls to print.")] = 20,
    series: SeriesOpt = None,
) -> None:
    """[Phase 10] Show logged LLM calls for one run — prompt, response, and a timestamp.

    Search here before re-asking the model something it may already have answered in this run;
    a cache hit is logged too, so a repeat question shows up as `(cache hit)` rather than
    silently spending nothing but also telling you nothing.
    """
    _settings(series)
    run_id = run or provenance.latest_run_id()
    if not run_id:
        console.print("[red]No runs recorded yet.[/red] Run a stage command first.")
        raise typer.Exit(code=1)
    try:
        records = provenance.read_calls(run_id, stage=stage, grep=grep)
    except provenance.ProvenanceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not records:
        console.print(f"[dim]No matching calls in run {run_id}.[/dim]")
        return

    for rec in records[:limit]:
        cached = " [dim](cache hit)[/dim]" if rec.get("cache_hit") else ""
        cost = "free" if not rec.get("cost_usd") else f"${rec['cost_usd']:.4f}"
        body = f"[bold]Prompt:[/bold]\n{rec.get('prompt', '')[:2000]}\n\n[bold]Response:[/bold]\n{rec.get('response', '')[:2000]}"
        if rec.get("error"):
            body += f"\n\n[red]Error: {rec['error']}[/red]"
        console.print(
            Panel(
                body,
                title=f"{rec.get('ts', '?')}  [{rec.get('stage', '?')}] {rec.get('provider', '?')}:{rec.get('model', '?')}{cached}  {cost}",
                border_style="blue",
                expand=False,
            )
        )
    if len(records) > limit:
        console.print(f"[dim]...{len(records) - limit} more. Raise --limit or narrow with --stage/--grep.[/dim]")


@app.command()
def rollback(
    run_id: Annotated[str, typer.Argument(help="The run to undo. See `wiki runs` for ids.")],
    series: SeriesOpt = None,
    yes: Annotated[bool, typer.Option("--yes", help="Actually do it — required, this overwrites live stage output.")] = False,
) -> None:
    """[Phase 10] Restore stage output to what it was immediately BEFORE the given run.

    Every stage-writing command snapshots the directories it is about to touch before it touches
    them. This restores that snapshot, undoing everything the run wrote — the manual-audit-pause
    workflow's rollback: if a run's output looks wrong after review, undo it and try again rather
    than hand-editing generated files.
    """
    _settings(series)
    try:
        ctx = provenance.get_run(run_id)
    except provenance.ProvenanceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    stages = ctx.manifest.get("snapshotted_stages", [])
    if not stages:
        console.print(
            f"[yellow]Run {run_id} has no snapshot[/yellow] (it wrote nothing new, or predates "
            f"this feature) — nothing to roll back."
        )
        return

    console.print(
        f"This will overwrite [bold]{', '.join(stages)}[/bold] with their state immediately "
        f"before run [bold]{run_id}[/bold] ({ctx.manifest.get('command')}, {ctx.manifest.get('scope')})."
    )
    if ctx.manifest.get("build_cutoff") is not None:
        console.print(f"Target: build-cutoff {ctx.manifest['build_cutoff']} artifacts.")
    if not yes:
        console.print("[yellow]Re-run with --yes to actually do this.[/yellow]")
        raise typer.Exit(code=1)

    restored = ctx.restore()
    console.print(f"[green]Restored:[/green] {', '.join(restored)}")


# ---------------------------------------------------------------------------
# Pipeline stages (implemented phase by phase)
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    chapters: ChaptersOpt = None,
    force: ForceOpt = False,
) -> None:
    """[Phase 1] Parse EPUBs into paragraph records. Deterministic; no model involved."""
    import json

    from .config import parse_chapter_range, parse_volume_range
    from .ingest.epub import parse_epub
    from .ingest.segment import build_manifest_entry, build_paragraph_records

    settings = _settings(series)
    if settings.series.get("decontaminate"):
        # [31] Parsing the EPUB here would write the ORIGINAL text into a decontaminated series.
        console.print("[red]This series is derived by `wiki decontaminate`, not parsed from an EPUB.[/red]")
        raise typer.Exit(code=1)
    all_volumes = {v.vol: v for v in settings.discover_volumes()}
    try:
        wanted = parse_volume_range(volumes, sorted(all_volumes))
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if chapters and len(wanted) != 1:
        console.print("[red]--chapters requires --volumes to select exactly one volume.[/red]")
        raise typer.Exit(code=1)

    paths.ensure_dirs()

    manifest_path = paths.parsed_manifest()
    manifest: dict = {"series": settings.series_id, "volumes": []}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {entry["vol"]: entry for entry in manifest.get("volumes", [])}

    for vol in wanted:
        out_path = paths.parsed_volume(vol)
        if not chapters and out_path.is_file() and not force:
            console.print(f"v{vol:02d}: [dim]skip (exists, use --force to redo)[/dim]")
            continue

        volume = all_volumes[vol]
        console.print(f"v{vol:02d}: parsing {volume.filename}...")
        raw_volume = parse_epub(volume.path, vol, settings.series)
        records = build_paragraph_records(raw_volume)

        if chapters:
            available_chapters = sorted({r["chapter_idx"] for r in records})
            try:
                wanted_chapters = set(parse_chapter_range(chapters, available_chapters))
            except ConfigError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc

            existing_records: list[dict] = []
            if out_path.is_file():
                existing_records = [
                    json.loads(line)
                    for line in out_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            existing_chapters = {r["chapter_idx"] for r in existing_records}
            to_write = wanted_chapters if force else (wanted_chapters - existing_chapters)
            if not to_write:
                console.print(
                    f"v{vol:02d}: [dim]chapter(s) {sorted(wanted_chapters)} already ingested, "
                    f"use --force to redo[/dim]"
                )
                continue

            fresh = [r for r in records if r["chapter_idx"] in to_write]
            records = sorted(
                _merge_by_chapter(existing_records, fresh, to_write),
                key=lambda r: (r["chapter_idx"], r["seq"]),
            )

        with out_path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        entry = build_manifest_entry(raw_volume, records)
        existing[vol] = entry
        console.print(
            f"v{vol:02d}: [green]done[/green] — {entry['n_chapters']} chapters, "
            f"{entry['n_paragraphs']} paragraphs, {entry['n_words']:,} words -> "
            f"{paths.relative(out_path)}"
        )

    manifest["volumes"] = [existing[v] for v in sorted(existing)]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\nManifest written to [cyan]{paths.relative(manifest_path)}[/cyan]")



def _build_gazetteer_for_volumes(settings, wanted: list[int], min_mentions: int, force: bool) -> bool:
    """[24] The entity-discovery pipeline (mine -> classify -> cluster -> gazetteer -> automaton
    + mentions), parameterized entirely by `wanted` -- the list of volumes it is allowed to read.

    This is the one place `--gate=render` and `--gate=build` must stay byte-identical in logic:
    the ONLY difference between "the published architecture" and "the cutoff-consistent rebuild"
    (docs/archive/GUIDE_TO_PUBLISHING.md §5 Part 1) is what `wanted` is and where `paths.ENTITIES_DIR` points
    (`paths.set_build_cutoff`, set by the caller) -- never a second code path. Returns `True` if
    it built, `False` if it skipped because the output already existed and `force` is not set.
    """
    import json

    from .entities import automaton as automaton_mod
    from .entities import candidates as candidates_mod
    from .entities.alias import MAX_LLM_PAIRS as ALIAS_MAX_LLM_PAIRS
    from .entities.alias import cluster_candidates
    from .entities.classify import classify_candidates
    from .entities.gazetteer import build_gazetteer, compute_importance
    from .llm.client import LLMClient, LLMError

    out_path = paths.gazetteer()
    if out_path.is_file() and not force:
        console.print(
            f"[dim]{paths.relative(out_path)} already exists, skipping (use --force to redo).[/dim]"
        )
        return False

    console.print(f"Loading paragraph records for volume(s) {wanted}...")
    records_by_vol: dict[int, list[dict]] = {}
    paragraphs_by_id: dict[str, str] = {}
    for vol in wanted:
        records = [json.loads(line) for line in paths.parsed_volume(vol).read_text(encoding="utf-8").splitlines() if line.strip()]
        records_by_vol[vol] = records
        for r in records:
            paragraphs_by_id[r["para_id"]] = r["text"]

    console.print("Mining name candidates...")
    candidate_list = candidates_mod.mine_candidates(records_by_vol, settings.series, min_mentions=min_mentions)
    # Candidates are part of the gazetteer artifact: snapshot before replacing them,
    # so a classification failure can restore the complete previous index inputs.
    run = provenance.start_run(
        settings.series_id, "gazetteer",
        scope=f"v{min(wanted)}-{max(wanted)}", volumes=wanted, volume_scope=max(wanted),
        stage_keys=["gazetteer"], settings=settings,
    )
    with paths.candidates().open("w", encoding="utf-8") as fh:
        for c in candidate_list:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    console.print(f"  {len(candidate_list)} candidate(s) -> {paths.relative(paths.candidates())}")

    client = LLMClient(settings, run=run, volume_scope=max(wanted))

    console.print(f"Classifying candidates against {settings.resolve_role('entity_classify')}...")
    kept_count = 0

    def _progress(i: int, total: int, candidate: dict, kept: bool) -> None:
        nonlocal kept_count
        kept_count += 1 if kept else 0
        if i % 25 == 0 or i == total:
            console.print(f"  [{i}/{total}] classified, {kept_count} kept so far")

    try:
        classified = classify_candidates(candidate_list, paragraphs_by_id, settings, client, on_progress=_progress)
    except LLMError as exc:
        run.finish("failed", error=str(exc))
        console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
        raise typer.Exit(code=1) from exc
    console.print(f"  {len(classified)}/{len(candidate_list)} candidate(s) classified as entities")

    console.print("Clustering aliases...")
    pair_stats: dict[str, int] = {}

    def _on_pairs_capped(total: int, dropped: int) -> None:
        pair_stats["total"] = total
        pair_stats["dropped"] = dropped

    try:
        entities = cluster_candidates(
            classified, paragraphs_by_id, settings.series, client, on_pairs_capped=_on_pairs_capped
        )
    except LLMError as exc:
        run.finish("failed", error=str(exc))
        console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
        raise typer.Exit(code=1) from exc
    if pair_stats.get("dropped"):
        console.print(
            f"  [yellow]{pair_stats['dropped']}/{pair_stats['total']} alias candidate pair(s) "
            f"past the max_llm_alias_pairs cap, never sent to the model[/yellow] -- see "
            f"[cyan]wiki audit gazetteer[/cyan]"
        )
    compute_importance(entities)
    max_candidates_per_volume = int((settings.series.get("entities") or {}).get("max_candidates_per_volume", 400))
    max_llm_alias_pairs = int((settings.series.get("entities") or {}).get("max_llm_alias_pairs", ALIAS_MAX_LLM_PAIRS))
    gaz = build_gazetteer(
        entities, wanted,
        min_mentions=min_mentions,
        max_candidates_per_volume=max_candidates_per_volume,
        max_llm_alias_pairs=max_llm_alias_pairs,
        alias_pairs_total=pair_stats.get("total"),
        alias_pairs_dropped=pair_stats.get("dropped"),
    )
    out_path.write_text(json.dumps(gaz, ensure_ascii=False, indent=2), encoding="utf-8")
    automaton_mod.write_surface_inventory(gaz["entities"])
    console.print(f"  {len(entities)} entities -> {paths.relative(out_path)}")

    console.print("Building the Aho-Corasick automaton and indexing mentions...")
    automaton = automaton_mod.build_automaton(gaz["entities"])
    console.print(f"  backend: {automaton.backend}")
    if automaton.dropped_ambiguous_surfaces:
        console.print(
            f"  [yellow]{len(automaton.dropped_ambiguous_surfaces)} ambiguous surface(s) dropped "
            f"(claimed by more than one entity; kept whichever entity_id sorts first)[/yellow] -- "
            f"see [cyan]wiki audit gazetteer[/cyan]"
        )
    n_mentions = 0
    with paths.mentions().open("w", encoding="utf-8") as fh:
        for mention in automaton_mod.index_mentions(automaton, wanted):
            fh.write(json.dumps(mention, ensure_ascii=False) + "\n")
            n_mentions += 1
    console.print(f"  {n_mentions:,} mention(s) -> {paths.relative(paths.mentions())}")

    run.finish("ok")
    return True



@app.command()
def decontaminate(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    force: ForceOpt = False,
    model: ModelOpt = None,
    set_: SetOpt = None,
    remap_only: Annotated[bool, typer.Option("--remap-only", help="Remap names, skip the paraphrase call ($0).")] = False,
) -> None:
    """[31] Build a decontaminated series from its `decontaminate.from_series`: remap fictional
    names from the reviewed `entity_map`, then paraphrase each paragraph (stage
    `decon_paraphrase`). Same `para_id`s as the source; writes data/<series>/01_parsed/ and
    decon_report.json. One provenance run per volume, so `wiki rollback` can undo it."""
    import json

    from .config import parse_volume_range
    from .ingest import decontaminate as decon
    from .llm.client import LLMClient, LLMError
    from .llm.parallel import map_calls, workers_for

    settings = _settings(series, model, set_)
    cfg = settings.series.get("decontaminate") or {}
    source, entity_map = cfg.get("from_series"), cfg.get("entity_map") or {}
    if not source or not entity_map:
        console.print("[red]Series config needs `decontaminate.from_series` and `decontaminate.entity_map`.[/red]")
        raise typer.Exit(code=1)
    available = [v for v in settings.volume_numbers() if paths.parsed_volume_of(source, v).is_file()]
    try:
        wanted = parse_volume_range(volumes, available)
    except ConfigError as exc:
        console.print(f"[red]{exc} (ingest `{source}` first)[/red]")
        raise typer.Exit(code=1) from exc
    ratio = tuple(cfg.get("length_ratio") or (0.5, 2.0))

    paths.ensure_dirs()
    manifest_path = paths.parsed_manifest()
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file()
                else {"series": settings.series_id, "volumes": []})
    entries = {e["vol"]: e for e in manifest.get("volumes", [])}
    report_path = paths.decon_report()
    reports = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    title = settings.series.get("series", {}).get("title", settings.series_id)

    for vol in wanted:
        out_path = paths.parsed_volume(vol)
        if out_path.is_file() and not force:
            console.print(f"v{vol:02d}: [dim]skip (exists, use --force to redo)[/dim]")
            continue
        src = [json.loads(line) for line in paths.parsed_volume_of(source, vol).read_text(encoding="utf-8").splitlines()
               if line.strip()]
        run = provenance.start_run(settings.series_id, "decontaminate", scope=f"v{vol}", volumes=[vol],
                                   volume_scope=vol, stage_keys=["ingest"], settings=settings)
        paraphrase, map_fn, retry = None, None, None
        if not (remap_only or cfg.get("paraphrase") is False):
            client = LLMClient(settings, run=run, volume_scope=vol)
            workers = workers_for(client, decon.PARAPHRASE_STAGE)
            console.print(f"v{vol:02d}: paraphrasing {len(src)} paragraphs with "
                          f"{settings.resolve_role(decon.PARAPHRASE_STAGE)}...")

            def paraphrase(text: str) -> str:
                return client.complete(decon.PARAPHRASE_STAGE, decon.PARAPHRASE_PROMPT.format(text=text),
                                       system=decon.PARAPHRASE_SYSTEM)

            def retry(text: str) -> str:
                return client.complete(decon.PARAPHRASE_STAGE, decon.PARAPHRASE_RETRY.format(text=text),
                                       system=decon.PARAPHRASE_SYSTEM)

            def map_fn(fn, items):
                return map_calls(fn, items, workers)
        try:
            derived = decon.decontaminate_records(src, entity_map, paraphrase, ratio, map_fn=map_fn,
                                                  retry=retry if paraphrase else None)
        except LLMError as exc:
            run.finish("failed", error=str(exc))
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        with out_path.open("w", encoding="utf-8") as fh:
            for record in derived:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        rep = decon.report(src, derived, entity_map)
        reports[f"v{vol:02d}"] = rep
        entries[vol] = {
            "vol": vol, "title": f"{title}, Vol. {vol:02d}", "isbn": None, "pub_date": None,
            "n_chapters": len({r["chapter_idx"] for r in derived}), "n_paragraphs": len(derived),
            "n_words": rep["words_derived"], "source_file": f"decontaminated from {source}",
        }
        run.finish("ok")
        console.print(
            f"v{vol:02d}: [green]done[/green] — {rep['paraphrased']} paraphrased, {rep['verbatim']} still verbatim "
            f"after a retry, {rep['remap_only']} remap-only, "
            f"{rep['residual_name_paragraphs']} paragraph(s) with a residual original name "
            f"-> {paths.relative(out_path)}"
        )

    manifest["volumes"] = [entries[v] for v in sorted(entries)]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

@app.command()
def gazetteer(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    force: ForceOpt = False,
    min_mentions: Annotated[
        Optional[int],
        typer.Option(help="Drop candidates seen fewer times than this. Defaults to this series' "
        "`entities.min_mentions` (config/series.<id>.yaml), or 3 if that key is unset."),
    ] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    gate: Annotated[
        str,
        typer.Option(
            "--gate",
            help="[Phase 24] 'render' (default): mine/classify/cluster once over every "
            "requested volume, as the published filter-before-generate architecture does -- the "
            "index itself sees the whole range, only rendering is cutoff-filtered. 'build': "
            "rebuild the gazetteer once per volume in the requested range, each time restricted "
            "to volumes 1..t -- a cutoff-consistent index that has never seen t+1. Writes to "
            "data/<series>/@t<NN>/02_entities/ so it never overwrites the render-mode index; "
            "see probe/channels/index.py and docs/vision/PHASE_24.md.",
        ),
    ] = "render",
    merge_epithets: Annotated[
        bool,
        typer.Option(
            "--merge-epithets",
            help="Skip mining/classification/clustering entirely. Merge epithets mined by "
            "`wiki scenes` into the existing gazetteer's surface_forms and rebuild the "
            "automaton + mention index only -- local, free, no LLM call.",
        ),
    ] = False,
) -> None:
    """[Phase 2] Discover entities, cluster aliases, and build the mention index."""
    from .config import parse_volume_range

    if gate not in ("render", "build"):
        console.print(f"[red]--gate must be 'render' or 'build', got {gate!r}.[/red]")
        raise typer.Exit(code=1)
    if merge_epithets and gate == "build":
        # [24] `--merge-epithets` reads/writes paths.gazetteer() directly and returns before the
        # gate branch below is ever reached -- it would silently ignore --gate entirely rather
        # than merging epithets into a cutoff-t gazetteer (which set_build_cutoff doesn't even
        # redirect SCENES_DIR for, so there is no cutoff-consistent epithets_v{NN}.jsonl to merge
        # from in the first place). See docs/vision/PHASE_24.md's 2026-09-14 (late pm) entry --
        # this exact silent-ignore was one confound behind two now-corrected headline numbers.
        console.print(
            "[red]--gate=build --merge-epithets is not supported: epithet merging always operates "
            "on the render-mode gazetteer and would silently ignore --gate. --gate=build gazetteers "
            "have no epithet channel at all yet (SCENES_DIR is not cutoff-redirected) -- see "
            "docs/vision/PHASE_24.md.[/red]"
        )
        raise typer.Exit(code=1)

    settings = _settings(series, model, set_)
    if min_mentions is None:
        min_mentions = int(settings.series.get("entities", {}).get("min_mentions") or 3)
    parsed_volumes = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    if not parsed_volumes:
        console.print("[red]No parsed volumes found.[/red] Run [cyan]wiki ingest[/cyan] first.")
        raise typer.Exit(code=1)
    try:
        wanted = parse_volume_range(volumes, parsed_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    paths.ensure_dirs()

    if merge_epithets:
        _merge_epithets_into_gazetteer(settings, wanted)
        return

    if gate == "render":
        if _build_gazetteer_for_volumes(settings, wanted, min_mentions, force):
            console.print(
                f"\n[green]Done.[/green] Run [cyan]wiki audit gazetteer[/cyan] and review "
                f"[cyan]{paths.relative(paths.roster_html())}[/cyan] before extraction."
            )
        return

    # --gate=build: one index per cutoff, each seeing only volumes 1..t.
    console.print(
        f"[Phase 24] Cutoff-consistent rebuild: {len(wanted)} gazetteer(s), each restricted to "
        f"its own volumes 1..t -- see docs/vision/PHASE_24.md."
    )
    built = 0
    try:
        for t in sorted(wanted):
            cutoff_volumes = [v for v in parsed_volumes if v <= t]
            if not cutoff_volumes:
                console.print(f"[yellow]t={t}: no parsed volume <= {t} on disk, skipping.[/yellow]")
                continue
            console.print(f"\n-- t={t} (volumes {cutoff_volumes}) --")
            paths.set_build_cutoff(t)
            paths.ensure_dirs()
            if _build_gazetteer_for_volumes(settings, cutoff_volumes, min_mentions, force):
                built += 1
    finally:
        paths.set_build_cutoff(None)

    console.print(
        f"\n[green]Done.[/green] {built} gazetteer(s) built under "
        f"[cyan]{paths.relative(paths.DATA_DIR)}/@t<NN>/02_entities/[/cyan]. "
        f"Compare against the render-mode index with [cyan]wiki probe run index[/cyan]."
    )


def _chapter_of_para_id(para_id: str) -> int:
    """'v03:c02:p0015' -> 2. Mention records (entities/automaton.py::index_mentions) carry no
    chapter_idx field of their own, only para_id -- this is the one place that needs it."""
    return int(para_id.split(":")[1][1:])


def _merge_by_chapter(existing: list[dict], fresh: list[dict], touched_chapters: set[int]) -> list[dict]:
    """Keep every existing record/claim whose chapter_idx is NOT in touched_chapters, and append
    the freshly (re)processed ones for the touched chapters -- the merge-not-overwrite behavior
    that lets `ingest --chapters`/`extract --chapters` accumulate a volume's file one chapter at
    a time instead of clobbering chapters an earlier, narrower call already wrote."""
    return [r for r in existing if r["chapter_idx"] not in touched_chapters] + fresh


def _merge_by_entity(existing: list[dict], fresh: list[dict], touched_entity_ids: set[str]) -> list[dict]:
    """Like `_merge_by_chapter` but scoped to claim `subject` (an entity_id) instead of
    chapter_idx -- Phase 22 C2's `extract --entities` top-up: keep every existing claim whose
    subject is NOT one of the just-(re)extracted characters, and append the fresh ones, so
    targeting one character's re-extraction never touches another character's claims."""
    return [c for c in existing if c["subject"] not in touched_entity_ids] + fresh


def _claim_file_volume(path) -> int:
    """'v03.jsonl' -> 3, 'scene_v03.jsonl' -> 3 (Phase 22 C1 -- claims now come from two files,
    `wiki extract`'s own `v{NN}.jsonl` and `wiki scenes`' `scene_v{NN}.jsonl`, see
    `paths.py::scene_claims_volume`), and 'infobox_v03.jsonl' -> 3 ([30] a third file)."""
    return int(path.stem.rsplit("v", 1)[1])


def _merge_claims_by_id(claims: list[dict]) -> list[dict]:
    """Dedup a combined claim list (Phase 22 C1: `wiki extract` + `wiki scenes` can each
    independently derive the identical fact). Recompute the current semantic claim id first so
    legacy artifacts migrate in memory and differences in polarity/qualifier never collapse;
    then merge evidence into one claim per id, same posture as
    `claims.py::_merge_claim` -- `graph/store.py::write_claims` uses INSERT OR REPLACE so a raw
    duplicate wouldn't crash, but merging here keeps both sides' evidence instead of silently
    dropping whichever file loads first."""
    from .extract.schema import make_claim_id

    by_id: dict[str, dict] = {}
    for claim in claims:
        object_norm = (
            claim.get("object") or str(claim.get("value") or "").strip().lower()
        )
        if claim.get("predicate") and claim.get("first_vol") is not None:
            # A row missing either part cannot have an id recomputed; keep its stored one so the
            # completeness gate below reports the malformed volume instead of this helper raising.
            claim = {**claim, "claim_id": make_claim_id(
                claim["subject"],
                claim["predicate"],
                object_norm,
                claim["first_vol"],
                polarity=claim.get("polarity", "asserted"),
                qualifier=claim.get("qualifier"),
            )}
        current_id = claim["claim_id"]
        existing = by_id.get(current_id)
        if existing is None:
            by_id[current_id] = claim
            continue
        seen = {(e["para_id"], e["quote"]) for e in existing["evidence"]}
        for e in claim["evidence"]:
            key = (e["para_id"], e["quote"])
            if key not in seen:
                existing["evidence"].append(e)
                seen.add(key)
        existing["confidence"] = max(existing["confidence"], claim["confidence"])
    return list(by_id.values())


def _merge_by_volume(existing: list[dict], fresh: list[dict], touched_volumes: set[int]) -> list[dict]:
    """Like `_merge_by_chapter` but scoped to `vol` -- `mentions.jsonl` is otherwise rebuilt whole
    from the automaton in one shot, so `wiki gazetteer --merge-epithets` must only replace the
    touched volumes' mentions, not clobber the whole corpus-flat file."""
    return [r for r in existing if r["vol"] not in touched_volumes] + fresh


def _merge_epithets_into_gazetteer(settings, wanted: list[int]) -> None:
    """`wiki gazetteer --merge-epithets` body: local-only, no LLM call. Requires an existing
    gazetteer.json and at least one epithets_vNN.jsonl (both produced by prior commands) --
    errors cleanly, naming the missing prerequisite, rather than silently doing nothing."""
    import json

    from .entities import automaton as automaton_mod
    from .entities.gazetteer import load as load_gazetteer
    from .entities.gazetteer import merge_epithets as merge_epithets_into_gaz

    gaz_path = paths.gazetteer()
    if not gaz_path.is_file():
        console.print("[red]No gazetteer.json yet.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)

    missing_scenes = [vol for vol in wanted if not paths.scene_epithets(vol).is_file()]
    if missing_scenes:
        console.print(
            f"[red]No scenes output for volume(s) {missing_scenes}.[/red] "
            "Run [cyan]wiki scenes[/cyan] first."
        )
        raise typer.Exit(code=1)

    epithet_records: list[dict] = []
    for vol in wanted:
        ep_path = paths.scene_epithets(vol)
        epithet_records.extend(
            json.loads(line) for line in ep_path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    if not epithet_records:
        # A real, common outcome (not every chapter mints an epithet) -- not an error, so
        # `wiki step`/`run-all` calling this after every `wiki scenes` never hard-fails over it.
        console.print("[dim]No epithets mined yet for this volume(s) -- nothing to merge.[/dim]")
        return

    run = provenance.start_run(
        settings.series_id, "gazetteer",
        scope=f"v{min(wanted)}-{max(wanted)} (merge-epithets)", volumes=wanted, volume_scope=max(wanted),
        stage_keys=["gazetteer"], settings=settings,
    )

    min_confidence = float(settings.extraction_behaviour.get("scene", {}).get("min_epithet_confidence", 0.6))
    gaz = load_gazetteer()
    n_before = sum(len(e["surface_forms"]) for e in gaz["entities"])
    gaz = merge_epithets_into_gaz(gaz, epithet_records, min_confidence)
    n_after = sum(len(e["surface_forms"]) for e in gaz["entities"])
    gaz_path.write_text(json.dumps(gaz, ensure_ascii=False, indent=2), encoding="utf-8")
    automaton_mod.write_surface_inventory(gaz["entities"])
    console.print(
        f"Considered {len(epithet_records)} mined epithet mention(s) -> {n_after - n_before} new "
        f"surface form(s) merged."
    )

    console.print("Rebuilding the Aho-Corasick automaton and re-indexing mentions...")
    automaton = automaton_mod.build_automaton(gaz["entities"])
    fresh_mentions = list(automaton_mod.index_mentions(automaton, wanted))
    existing_mentions: list[dict] = []
    if paths.mentions().is_file():
        existing_mentions = [
            json.loads(line)
            for line in paths.mentions().read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    merged_mentions = _merge_by_volume(existing_mentions, fresh_mentions, set(wanted))
    with paths.mentions().open("w", encoding="utf-8") as fh:
        for mention in merged_mentions:
            fh.write(json.dumps(mention, ensure_ascii=False) + "\n")

    run.finish("ok")
    console.print(
        f"[green]Done.[/green] {len(merged_mentions):,} mention(s) -> {paths.relative(paths.mentions())}"
    )


@app.command()
def scenes(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    chapters: ChaptersOpt = None,
    force: ForceOpt = False,
    model: ModelOpt = None,
    set_: SetOpt = None,
    allow_partial: AllowPartialOpt = False,
) -> None:
    """[Phase 18; Phase 22 C1] Chapter-major pass: whole-chapter scene records (participants,
    location, beat summary, state changes, attributed quotes), epithet-alias mining, AND (as of
    C1) the PRIMARY attribute/trait/relation claims for every participant — written to
    `data/03_claims/scene_v{NN}.jsonl`, never touching `wiki extract`'s own
    `data/03_claims/v{NN}.jsonl`. Run `wiki gazetteer --merge-epithets` afterward so mined
    epithets reach the automaton before the next `wiki extract` run.
    """
    import json

    from .config import parse_chapter_range, parse_volume_range
    from .entities.gazetteer import load as load_gazetteer
    from .extract import schema
    from .extract.scenes import extract_volume_scenes
    from .llm import client as llm_client
    from .llm.client import LLMClient, LLMError

    settings = _settings(series, model, set_)
    _guard_upstream_failures(["gazetteer"], allow_partial)

    if not paths.gazetteer().is_file() or not paths.mentions().is_file():
        console.print("[red]No gazetteer/mention index found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)
    gaz = load_gazetteer()

    parsed_volumes = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    if not parsed_volumes:
        console.print("[red]No parsed volumes found.[/red] Run [cyan]wiki ingest[/cyan] first.")
        raise typer.Exit(code=1)
    try:
        wanted = parse_volume_range(volumes, parsed_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if chapters and len(wanted) != 1:
        console.print("[red]--chapters requires --volumes to select exactly one volume.[/red]")
        raise typer.Exit(code=1)

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, "scenes",
        scope=f"v{min(wanted)}-{max(wanted)}", volumes=wanted, volume_scope=max(wanted),
        stage_keys=["scenes", "claims"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=max(wanted))

    console.print("Loading mention index...")
    mentions_by_vol: dict[int, list[dict]] = {v: [] for v in wanted}
    with paths.mentions().open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            mention = json.loads(line)
            if mention["vol"] in mentions_by_vol:
                mentions_by_vol[mention["vol"]].append(mention)

    console.print(f"{len(wanted)} volume(s) to process.")
    yield_report: dict[str, Any] = {}

    for vol in wanted:
        scenes_path = paths.scenes_volume(vol)
        epithets_path = paths.scene_epithets(vol)
        claims_path = paths.scene_claims_volume(vol)
        records = [
            json.loads(line)
            for line in paths.parsed_volume(vol).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        vol_mentions = mentions_by_vol[vol]

        existing_scenes: list[dict] = []
        if scenes_path.is_file():
            existing_scenes = [
                json.loads(line)
                for line in scenes_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        existing_epithets: list[dict] = []
        if epithets_path.is_file():
            existing_epithets = [
                json.loads(line)
                for line in epithets_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        existing_claims: list[dict] = []
        if claims_path.is_file():
            existing_claims = [
                json.loads(line)
                for line in claims_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        chapters_to_process: set[int] | None = None
        if chapters:
            available_chapters = sorted({r["chapter_idx"] for r in records})
            try:
                wanted_chapters = set(parse_chapter_range(chapters, available_chapters))
            except ConfigError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            existing_chapters = {c["chapter_idx"] for c in existing_scenes}
            chapters_to_process = wanted_chapters if force else (wanted_chapters - existing_chapters)
            if not chapters_to_process:
                console.print(
                    f"v{vol:02d}: [dim]chapter(s) {sorted(wanted_chapters)} already processed, "
                    f"use --force to redo[/dim]"
                )
                continue
            records = [r for r in records if r["chapter_idx"] in chapters_to_process]
        elif scenes_path.is_file() and not force:
            console.print(f"v{vol:02d}: [dim]skip (exists, use --force to redo)[/dim]")
            continue

        console.print(f"v{vol:02d}: extracting scenes against {settings.resolve_role('scene_extract')}...")

        def _progress(i: int, total: int, chapter_idx: int, n_scenes: int) -> None:
            console.print(f"  [{i}/{total}] chapter {chapter_idx}: {n_scenes} scene(s)")

        try:
            scene_records, epithet_records, claim_records, drops = extract_volume_scenes(
                vol, records, vol_mentions, gaz["entities"], settings, client, on_progress=_progress
            )
        except LLMError as exc:
            run.finish("failed", error=str(exc))
            console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
            raise typer.Exit(code=1) from exc

        if chapters_to_process is not None:
            scene_records = _merge_by_chapter(existing_scenes, scene_records, chapters_to_process)
            epithet_records = _merge_by_chapter(existing_epithets, epithet_records, chapters_to_process)
            claim_records = _merge_by_chapter(existing_claims, claim_records, chapters_to_process)

        with scenes_path.open("w", encoding="utf-8") as fh:
            for rec in scene_records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with epithets_path.open("w", encoding="utf-8") as fh:
            for rec in epithet_records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with claims_path.open("w", encoding="utf-8") as fh:
            for rec in claim_records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        console.print(
            f"v{vol:02d}: [green]done[/green] — {len(scene_records)} scene(s), "
            f"{len(epithet_records)} epithet mention(s), {len(claim_records)} claim(s) -> "
            f"{paths.relative(scenes_path)}"
        )
        if drops:
            dropped = ", ".join(f"{reason}: {n}" for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]))
            console.print(f"  [dim]dropped — {dropped}[/dim]")
        yield_report[f"v{vol:02d}"] = {
            "scenes": len(scene_records), "epithet_mentions": len(epithet_records),
            "claims_kept": len(claim_records), "drops": dict(drops),
            "incomplete_fact_drops": dict(schema.pop_incomplete_fact_drops()),
            "truncation_salvages": llm_client.pop_truncation_salvage_count(),
        }


    run.write_yield(yield_report)
    run.finish("ok")
    console.print(
        "\n[green]Done.[/green] Run [cyan]wiki audit scenes[/cyan], then "
        "[cyan]wiki gazetteer --merge-epithets[/cyan] before the next [cyan]wiki extract[/cyan]."
    )


def _extract_claims_for_volumes(
    settings,
    wanted: list[int],
    entities_visible: list[dict[str, Any]],
    chapters: Optional[str],
    force: bool,
    entities_filter: Optional[str],
    limit: Optional[int],
) -> None:
    """[24] The claim-extraction pipeline (mentions -> per-volume `extract_volume` -> write),
    parameterized entirely by `wanted` (which volumes it reads) and `entities_visible` (which
    gazetteer entities it is allowed to know about, both for the character list it extracts AND
    for the "other people/things named" context `extract_volume` builds per window). This is the
    one place `--gate=render` and `--gate=build` must stay byte-identical in logic — mirrors
    `_build_gazetteer_for_volumes`'s split (same file).
    """
    import json

    from .config import parse_chapter_range
    from .extract import schema
    from .extract.claims import extract_volume
    from .llm import client as llm_client
    from .llm.client import LLMClient, LLMError

    characters = [e for e in entities_visible if e["type"] == "CHARACTER"]
    if entities_filter:
        wanted_names = {n.strip().lower() for n in entities_filter.split(",") if n.strip()}
        characters = [
            e for e in characters if e["canonical"].lower() in wanted_names or e["entity_id"] in wanted_names
        ]
    if limit:
        characters = characters[:limit]
    if not characters:
        console.print("[red]No CHARACTER entities to extract (check --entities, or the cutoff gazetteer).[/red]")
        raise typer.Exit(code=1)
    targeted_entity_ids = {e["entity_id"] for e in characters} if entities_filter else None

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, "extract",
        scope=f"v{min(wanted)}-{max(wanted)}", volumes=wanted, volume_scope=max(wanted),
        stage_keys=["claims"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=max(wanted))

    console.print("Loading mention index...")
    mentions_by_vol: dict[int, list[dict]] = {v: [] for v in wanted}
    with paths.mentions().open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            mention = json.loads(line)
            if mention["vol"] in mentions_by_vol:
                mentions_by_vol[mention["vol"]].append(mention)

    console.print(f"{len(characters)} character(s), {len(wanted)} volume(s) to extract.")
    yield_report: dict[str, Any] = {}

    for vol in wanted:
        out_path = paths.claims_volume(vol)
        records = [
            json.loads(line)
            for line in paths.parsed_volume(vol).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        vol_mentions = mentions_by_vol[vol]

        existing_claims: list[dict] = []
        if out_path.is_file():
            existing_claims = [
                json.loads(line)
                for line in out_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        chapters_to_process: set[int] | None = None
        if chapters:
            available_chapters = sorted({r["chapter_idx"] for r in records})
            try:
                wanted_chapters = set(parse_chapter_range(chapters, available_chapters))
            except ConfigError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            existing_chapters = {c["chapter_idx"] for c in existing_claims}
            chapters_to_process = wanted_chapters if force else (wanted_chapters - existing_chapters)
            if not chapters_to_process:
                console.print(
                    f"v{vol:02d}: [dim]chapter(s) {sorted(wanted_chapters)} already extracted, "
                    f"use --force to redo[/dim]"
                )
                continue
            records = [r for r in records if r["chapter_idx"] in chapters_to_process]
            vol_mentions = [m for m in vol_mentions if _chapter_of_para_id(m["para_id"]) in chapters_to_process]
        elif out_path.is_file() and not force and targeted_entity_ids is None:
            console.print(f"v{vol:02d}: [dim]skip (exists, use --force to redo)[/dim]")
            continue

        if targeted_entity_ids is not None and chapters_to_process is None and out_path.is_file():
            console.print(
                f"v{vol:02d}: topping up {len(characters)} character(s) against "
                f"{settings.resolve_role('claim_extract')} (existing claims for other characters kept)..."
            )
        else:
            console.print(
                f"v{vol:02d}: extracting claims for {len(characters)} character(s) against "
                f"{settings.resolve_role('claim_extract')}..."
            )

        def _progress(i: int, total: int, entity: dict, n_claims: int) -> None:
            console.print(f"  [{i}/{total}] {entity['canonical']}: {n_claims} claim(s)")

        try:
            claims, drops = extract_volume(
                characters, vol, records, vol_mentions, entities_visible, settings, client, on_progress=_progress
            )
        except LLMError as exc:
            run.finish("failed", error=str(exc))
            console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
            raise typer.Exit(code=1) from exc

        if chapters_to_process is not None:
            claims = _merge_by_chapter(existing_claims, claims, chapters_to_process)
        elif targeted_entity_ids is not None and existing_claims:
            claims = _merge_by_entity(existing_claims, claims, targeted_entity_ids)

        with out_path.open("w", encoding="utf-8") as fh:
            for claim in claims:
                fh.write(json.dumps(claim, ensure_ascii=False) + "\n")

        console.print(f"v{vol:02d}: [green]done[/green] — {len(claims)} claim(s) -> {paths.relative(out_path)}")
        if drops:
            dropped = ", ".join(f"{reason}: {n}" for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]))
            console.print(f"  [dim]dropped — {dropped}[/dim]")
        yield_report[f"v{vol:02d}"] = {
            "claims_kept": len(claims), "drops": dict(drops),
            "incomplete_fact_drops": dict(schema.pop_incomplete_fact_drops()),
            "truncation_salvages": llm_client.pop_truncation_salvage_count(),
        }


    run.write_yield(yield_report)
    run.finish("ok")


@app.command()
def infobox(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    force: ForceOpt = False,
    entities: Annotated[Optional[str], typer.Option(help="Comma-separated names, for a small slice.")] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    allow_partial: AllowPartialOpt = False,
    pass_name: Annotated[str, typer.Option("--pass", help="'infobox' (GENDER/AGE/ORIGIN) or 'backstory' (BACKGROUND).")] = "infobox",
) -> None:
    """[30] Fill the infobox fields the windowed pass leaves empty (extract/infobox.py): one
    long-context call per character per volume over that character's whole dossier, each field
    bound to a verbatim quote. Writes data/<series>/03_claims/infobox_vNN.jsonl, read by `wiki
    graph build` alongside the other claim files. Never touches `claim_extract`'s output."""
    import json

    from .config import parse_volume_range
    from .entities.gazetteer import load as load_gazetteer
    from .extract import infobox as infobox_mod
    from .llm.client import LLMClient, LLMError

    if pass_name not in infobox_mod.PASSES:
        console.print(f"[red]--pass must be one of {sorted(infobox_mod.PASSES)}, got {pass_name!r}.[/red]")
        raise typer.Exit(code=1)
    settings = _settings(series, model, set_)
    _guard_upstream_failures(["gazetteer"], allow_partial)
    if not paths.gazetteer().is_file() or not paths.mentions().is_file():
        console.print("[red]No gazetteer/mention index found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)
    parsed_volumes = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    try:
        wanted = parse_volume_range(volumes, parsed_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    all_entities = load_gazetteer()["entities"]
    characters = [e for e in all_entities if e["type"] == "CHARACTER"]
    if entities:
        names = {n.strip().lower() for n in entities.split(",") if n.strip()}
        characters = [e for e in characters if e["canonical"].lower() in names or e["entity_id"] in names]
    if not characters:
        console.print("[red]No CHARACTER entities selected.[/red]")
        raise typer.Exit(code=1)

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, pass_name, scope=f"v{min(wanted)}-{max(wanted)}", volumes=wanted,
        volume_scope=max(wanted), stage_keys=["claims"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=max(wanted))
    mentions = [json.loads(line) for line in paths.mentions().read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = {e["entity_id"] for e in characters}
    yield_report: dict[str, Any] = {}

    for vol in wanted:
        out_path = paths.pass_claims_volume(pass_name, vol)
        existing = []
        if out_path.is_file():
            if not force and not entities:
                console.print(f"v{vol:02d}: [dim]skip (exists, use --force to redo)[/dim]")
                continue
            existing = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        records = [json.loads(line) for line in paths.parsed_volume(vol).read_text(encoding="utf-8").splitlines() if line.strip()]
        vol_mentions = [m for m in mentions if m["vol"] == vol]
        console.print(f"v{vol:02d}: {pass_name} for {len(characters)} character(s) against {settings.resolve_role(infobox_mod.PASSES[pass_name]['stage'])}...")
        try:
            claims, drops = infobox_mod.extract_volume(
                characters, vol, records, vol_mentions, all_entities, settings, client,
                on_progress=lambda i, n, e, k: console.print(f"  [{i}/{n}] {e['canonical']}: {k} field(s)"),
                name=pass_name,
            )
        except LLMError as exc:
            run.finish("failed", error=str(exc))
            console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
            raise typer.Exit(code=1) from exc
        # A targeted run replaces only the selected characters' rows.
        claims = [c for c in existing if c["subject"] not in selected] + claims
        out_path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in claims), encoding="utf-8")
        console.print(f"v{vol:02d}: [green]done[/green] -- {len(claims)} claim(s) -> {paths.relative(out_path)}")
        if drops:
            console.print("  [dim]dropped -- " + ", ".join(f"{r}: {n}" for r, n in drops.most_common()) + "[/dim]")
        yield_report[f"v{vol:02d}"] = {"claims_kept": len(claims), "drops": dict(drops)}

    run.write_yield(yield_report)
    run.finish("ok")
    console.print("Run [cyan]wiki graph build[/cyan] to merge these into the graph.")


@app.command()
def extract(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    chapters: ChaptersOpt = None,
    force: ForceOpt = False,
    entities: Annotated[Optional[str], typer.Option(help="Comma-separated names, for prompt tuning.")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Cap characters per volume, for a smoke test.")] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    attr: AttrOpt = None,
    trait: TraitOpt = None,
    relation: RelationOpt = None,
    only: OnlyOpt = None,
    skip: SkipOpt = None,
    allow_partial: AllowPartialOpt = False,
    gate: Annotated[
        str,
        typer.Option(
            "--gate",
            help="[Phase 24] 'render' (default): load the gazetteer once (whichever mode paths.py "
            "currently points at) and extract every requested volume against the full entity "
            "registry, as today. 'build': re-extract once per cutoff t in the requested volumes, "
            "each restricted to volumes 1..t and to entities with first_vol <= t from that "
            "cutoff's own --gate=build gazetteer (which must already exist on disk). Writes to "
            "data/<series>/@t<NN>/03_claims/; see docs/vision/PHASE_24.md.",
        ),
    ] = "render",
) -> None:
    """[Phase 3] Extract typed claims with evidence. The long stage; runs locally, resumable.

    `--attr`/`--trait`/`--relation` add a predicate to THIS run's prompt vocabulary ad hoc;
    `--only`/`--skip` narrow or widen which configured predicates are sent at all — "volume 9
    introduces a stat block, extract it only there" as a command line rather than a config edit
    (VISION.md 2026-09-04). None of these are written back to config/extraction.yaml or its
    per-series overlay — promote a predicate there once it's proven useful.

    `--entities` doubles as a targeted top-up: if the volume's claim file already exists, passing
    `--entities` no longer skips it (that still requires `--force` with no `--entities`) — instead
    it re-extracts only the named character(s) and merges the result in, replacing their prior
    claims while leaving every other character's claims untouched. Use this to chase a specific
    `wiki audit eval` recall gap without paying for a whole-volume re-run (Phase 22 C2).

    `--gate=build` is a cutoff sweep and does not compose with any of `--chapters`/`--entities`/
    `--limit`/`--only`/`--skip`, which target one volume or a subset of characters/predicates.
    """
    from .config import parse_volume_range
    from .entities.gazetteer import load as load_gazetteer

    if gate not in ("render", "build"):
        console.print(f"[red]--gate must be 'render' or 'build', got {gate!r}.[/red]")
        raise typer.Exit(code=1)

    if gate == "build":
        narrowing = {
            "--chapters": chapters, "--entities": entities, "--limit": limit,
            "--only": only, "--skip": skip,
        }
        used = [flag for flag, val in narrowing.items() if val]
        if used:
            console.print(
                f"[red]--gate=build is incompatible with {', '.join(used)} — a cutoff sweep "
                f"doesn't compose with per-volume/per-character narrowing.[/red]"
            )
            raise typer.Exit(code=1)

    settings = _settings(series, model, set_)
    if gate == "render":
        _guard_upstream_failures(["gazetteer"], allow_partial)
    for note in apply_schema_overrides(settings, attrs=attr, traits=trait, relations=relation, only=only, skip=skip):
        console.print(f"[yellow]{note}[/yellow]")

    if gate == "render" and (not paths.gazetteer().is_file() or not paths.mentions().is_file()):
        console.print("[red]No gazetteer/mention index found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)

    parsed_volumes = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    if not parsed_volumes:
        console.print("[red]No parsed volumes found.[/red] Run [cyan]wiki ingest[/cyan] first.")
        raise typer.Exit(code=1)
    try:
        wanted = parse_volume_range(volumes, parsed_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if chapters and len(wanted) != 1:
        console.print("[red]--chapters requires --volumes to select exactly one volume.[/red]")
        raise typer.Exit(code=1)

    if gate == "render":
        gaz = load_gazetteer()
        _extract_claims_for_volumes(settings, wanted, gaz["entities"], chapters, force, entities, limit)
        console.print("\n[green]Done.[/green] Run [cyan]wiki audit claims[/cyan] to review.")
        return

    # --gate=build: one cutoff-consistent extraction per t, each seeing only volumes 1..t and
    # only entities with first_vol <= t.
    console.print(
        f"[Phase 24] Cutoff-consistent rebuild: {len(wanted)} extraction(s), each restricted to "
        f"its own volumes 1..t and its own cutoff-t entity registry -- see docs/vision/PHASE_24.md."
    )
    try:
        for t in sorted(wanted):
            cutoff_volumes = [v for v in parsed_volumes if v <= t]
            if not cutoff_volumes:
                console.print(f"[yellow]t={t}: no parsed volume <= {t} on disk, skipping.[/yellow]")
                continue
            paths.set_build_cutoff(t)
            if not paths.gazetteer().is_file() or not paths.mentions().is_file():
                console.print(
                    f"[red]No cutoff-{t} gazetteer/mention index at "
                    f"{paths.relative(paths.gazetteer())}.[/red] Run "
                    f"[cyan]wiki gazetteer --gate=build --volumes {min(wanted)}-{max(wanted)}[/cyan] first."
                )
                raise typer.Exit(code=1)
            _guard_upstream_failures(["gazetteer"], allow_partial)
            gaz = load_gazetteer()
            entities_visible = [e for e in gaz["entities"] if e.get("first_vol", 0) <= t]
            console.print(f"\n-- t={t} (volumes {cutoff_volumes}) --")
            _extract_claims_for_volumes(settings, cutoff_volumes, entities_visible, None, force, None, None)
    finally:
        paths.set_build_cutoff(None)

    console.print(
        f"\n[green]Done.[/green] Claims built under "
        f"[cyan]{paths.relative(paths.DATA_DIR)}/@t<NN>/03_claims/[/cyan]."
    )


@graph_app.command("build")
def graph_build(
    series: SeriesOpt = None, model: ModelOpt = None, set_: SetOpt = None, allow_partial: AllowPartialOpt = False,
    nli: Annotated[bool, typer.Option("--nli", help="Score evidence with local MiniCheck before arbitration.")] = False,
) -> None:
    """[Phase 4] Assemble claims into the temporal graph and resolve contradictions.

    Always a full rebuild from data/03_claims/ (cheap, deterministic, and every arbitration call
    is cached) — there is no --force here because there is nothing to skip.
    """
    import json

    from .entities.gazetteer import load as load_gazetteer
    from .graph import contradictions as contradictions_mod
    from .graph import store
    from .llm.client import LLMClient, LLMError

    settings = _settings(series, model, set_)
    _guard_upstream_failures(["gazetteer", "claims"], allow_partial)

    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)
    gaz = load_gazetteer()

    claim_files = []
    if paths.CLAIMS_DIR.is_dir():
        claim_files = (
            sorted(paths.CLAIMS_DIR.glob("v*.jsonl")) + sorted(paths.CLAIMS_DIR.glob("scene_v*.jsonl"))
            + sorted(paths.CLAIMS_DIR.glob("infobox_v*.jsonl"))
            + sorted(paths.CLAIMS_DIR.glob("backstory_v*.jsonl"))
        )
    if not claim_files:
        console.print(
            "[red]No claims found.[/red] Run [cyan]wiki scenes[/cyan] and/or [cyan]wiki extract[/cyan] first."
        )
        raise typer.Exit(code=1)

    raw_claims: list[dict] = []
    for path in claim_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw_claims.append(json.loads(line))
    claims = _merge_claims_by_id(raw_claims)

    mentions: list[dict] = []
    if paths.mentions().is_file():
        for line in paths.mentions().read_text(encoding="utf-8").splitlines():
            if line.strip():
                mentions.append(json.loads(line))

    claimed_vols = {_claim_file_volume(p) for p in claim_files}
    characters_by_id = {e["entity_id"]: e for e in gaz["entities"] if e["type"] == "CHARACTER"}
    incomplete = _incomplete_claim_volumes(claims, mentions, characters_by_id, claimed_vols, min_mentions=10)
    # [33] A volume whose last `wiki extract` finished `ok` really has no claim about that character
    # (A1's prefix gazetteer typed "God" and "Elaine" as characters); only a killed run, which never
    # records an outcome, leaves the crash signature this guard exists for.
    incomplete = {v: n for v, n in incomplete.items() if not _extract_finished_ok(v)}
    if incomplete and not allow_partial:
        console.print(
            "[red]Claims look incomplete for the following volume(s) — the signature of a "
            "crashed `wiki extract` run:[/red]"
        )
        for vol in sorted(incomplete):
            console.print(f"  v{vol:02d}: {', '.join(incomplete[vol])} — mentioned, zero claims")
        console.print(
            "\nRe-run [cyan]wiki extract --volumes <N> --force[/cyan] for the affected volume(s), "
            "or pass [cyan]--allow-partial[/cyan] to build the graph anyway."
        )
        raise typer.Exit(code=1)

    console.print(
        f"{len(claims)} claim(s), {len(gaz['entities'])} entities. Resolving contradictions "
        f"against {settings.resolve_role('support' if nli else 'arbitrate')}..."
    )
    volume_scope = max(claimed_vols, default=None)
    run = provenance.start_run(
        settings.series_id, "graph_build",
        scope=f"v1-{volume_scope}" if volume_scope else "all", volume_scope=volume_scope,
        stage_keys=["graph"], settings=settings,
    )
    # Phase 26 part C: the gazetteer's own type for every entity, so `build_graph` can enforce
    # each predicate's `object_types` on claims that were extracted before that gate existed.
    entity_types = {e["entity_id"]: e["type"] for e in gaz["entities"]}
    client = LLMClient(settings, run=run, volume_scope=volume_scope)
    try:
        if nli:
            from .graph import support
            scores = support.score_claims(
                support.arbitration_claims(claims, settings),
                {e["entity_id"]: e for e in gaz["entities"]}, settings, client,
            )
            intervals, edges, contradictions_doc = contradictions_mod.build_graph(
                claims, settings, client, support_scores=scores,
                support_threshold=float(settings.extraction_behaviour.get("support", {}).get("threshold", 0.5)),
                entity_types=entity_types,
            )
        else:
            intervals, edges, contradictions_doc = contradictions_mod.build_graph(
                claims, settings, client, entity_types=entity_types
            )
    except (LLMError, OSError, ValueError) as exc:
        run.finish("failed", error=str(exc))
        console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
        raise typer.Exit(code=1) from exc

    paths.ensure_dirs()
    conn = store.connect()
    store.reset(conn)
    store.write_entities(conn, gaz["entities"])
    store.write_claims(conn, claims)
    store.write_intervals(conn, intervals)
    store.write_edges(conn, edges)
    store.write_mention_counts(conn, store.mention_counts_from_records(mentions))
    # `reset()` keeps `verdicts` now (see its docstring), so this is what stops a verdict
    # outliving the interval it judged. Whatever survives, `verify` below decides on per character.
    orphans = store.prune_orphan_verdicts(conn)
    conn.close()
    if orphans:
        console.print(f"  dropped {orphans} verdict(s) whose fact no longer exists")

    paths.contradictions().write_text(
        json.dumps(contradictions_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = contradictions_doc["summary"]
    run.finish("ok")
    console.print(
        f"\n[green]Done.[/green] {len(intervals)} interval(s), {len(edges)} edge(s) -> "
        f"{paths.relative(paths.graph_db())}"
    )
    console.print(
        f"{summary['total']} conflict(s) — {summary['extraction_error']} extraction_error, "
        f"{summary['narrative_change']} narrative_change, {summary['flagged']} flagged -> "
        f"{paths.relative(paths.contradictions())}"
    )
    console.print("Run [cyan]wiki audit contradictions[/cyan] to review.")

    # Phase 23 B5: verify every time the graph is (re)built, not as an optional later stage a run
    # can skip (`wiki verify` had never run against the live Spice and Wolf series the Phase 23
    # audit inspected -- data/<series>/04_graph/verification.json simply did not exist). A
    # verify failure (no reachable model at all) degrades this to a skip, exactly like
    # graph/verify.py's own documented contract ("verification that cannot run degrades to
    # skipped, never to a fabricated pass/fail") -- it must never turn a graph build that itself
    # succeeded into a failed command.
    console.print("\nVerifying facts against their own cited evidence...")
    try:
        if nli:
            verify(series=series, upto=volume_scope or 1, model=model, nli=True)
        else:
            verify(series=series, upto=volume_scope or 1, model=model)
    except typer.Exit as exc:
        if exc.exit_code != 0:
            console.print(
                "[yellow]wiki verify could not run (see message above) -- the graph itself "
                "was still built successfully.[/yellow]"
            )


@app.command("verify")
def verify(
    series: SeriesOpt = None,
    upto: UptoOpt = None,
    entities: Annotated[
        Optional[str], typer.Option(help="Comma-separated names/ids, to verify a few characters.")
    ] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    allow_partial: AllowPartialOpt = False,
    nli: Annotated[bool, typer.Option("--nli", help="Use local per-fact MiniCheck scores instead of chat verification.")] = False,
) -> None:
    """[Phase 22 C3] One cheap LLM call per character: does each currently-visible fact's own
    cited evidence actually support it?

    The backstop for errors that survive extraction — a fact hallucinated about the wrong entity,
    or a relationship/membership claim the evidence doesn't actually support — not a bigger
    extraction pass. Reads data/04_graph/graph.db (already built by `wiki graph build`); never
    mutates a claim or interval, only writes data/04_graph/verification.json for `wiki audit
    verify` to surface. A character with no visible facts at `--upto` is skipped, no call made.
    """
    import json

    from .entities.gazetteer import load as load_gazetteer
    from .graph import store
    from .graph import verify as verify_mod
    from .graph.store import connect as connect_graph
    from .llm.client import LLMClient, LLMError
    from .llm.parallel import workers_for
    from .synth.assemble import fact_set_for_verification

    settings = _settings(series, model, set_)
    upto = _resolve_upto(settings, upto)
    _guard_upstream_failures(["gazetteer", "graph"], allow_partial)

    if not paths.graph_db().is_file():
        console.print("[red]No graph found.[/red] Run [cyan]wiki graph build[/cyan] first.")
        raise typer.Exit(code=1)
    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)

    gaz = load_gazetteer()
    entities_by_id = {e["entity_id"]: e for e in gaz["entities"]}
    characters = [e for e in gaz["entities"] if e["type"] == "CHARACTER"]

    if entities:
        wanted_names = {n.strip().lower() for n in entities.split(",") if n.strip()}
        characters = [
            e for e in characters if e["canonical"].lower() in wanted_names or e["entity_id"] in wanted_names
        ]
    if not characters:
        console.print("[red]No CHARACTER entities match.[/red]")
        raise typer.Exit(code=1)

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, "verify",
        scope=f"upto{upto}", volume_scope=upto, stage_keys=["verify"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=upto)
    conn = connect_graph()

    if nli:
        from .graph import support
        try:
            doc, verdicts = support.verify_intervals(
                conn, characters, entities_by_id, upto, settings, client, run.run_id,
            )
            store.write_verdicts(conn, verdicts)
            paths.verification().write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except (LLMError, OSError, ValueError, KeyError) as exc:
            run.finish("failed", error=str(exc))
            console.print(f"[red]NLI verification failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        finally:
            conn.close()
        run.finish("ok")
        summary = doc["summary"]
        console.print(f"MiniCheck: {summary['facts_flagged']} / {summary['facts_checked']} facts unsupported.")
        console.print("Run [cyan]wiki audit verify[/cyan] to review.")
        return

    results: list[dict] = []
    checked = 0
    reused = 0
    verdicts_by_id: dict[str, dict] = {}  # Phase 23 B5 -- fed to store.write_verdicts below
    model_name = str(settings.resolve_role("verify"))
    evidence_context = verify_mod.EvidenceContext(upto)
    # A verdict survives `graph build` now, and verification asks about one fact per call, so the
    # reuse unit is the fact: only facts whose own evidence moved are re-asked. MEASUREMENTS
    # §§28-29 are what this is for -- a rebuild that changed one relation used to re-roll 12
    # reader-visible gates, and per-character reuse still re-rolled a whole character for one
    # changed fact.
    stored_verdicts = store.read_verdicts(conn)
    workers = workers_for(client, verify_mod.STAGE)
    for entity in characters:
        facts = fact_set_for_verification(conn, entity["entity_id"], upto, entities_by_id)
        if not facts:
            continue
        verify_mod.attach_pair_siblings(facts)
        context_hashes = {f["interval_id"]: verify_mod.fact_context_sha256(f) for f in facts}

        def _carried(fact: dict) -> dict | None:
            stored = stored_verdicts.get(fact["interval_id"])
            if (
                stored is not None
                and stored.get("context_sha256") == context_hashes[fact["interval_id"]]
                and stored.get("evidence_cutoff") == upto
                and stored.get("model") == model_name
                and stored.get("model_revision") == verify_mod.METHOD_REVISION
            ):
                return stored
            return None

        carried = {f["interval_id"]: c for f in facts if (c := _carried(f)) is not None}
        to_check = [f for f in facts if f["interval_id"] not in carried]
        flagged: list[dict] = [
            {**f, "rationale": carried[f["interval_id"]].get("rationale")}
            for f in facts
            if f["interval_id"] in carried and carried[f["interval_id"]]["verdict"] == "unsupported"
        ]
        for iid, stored in carried.items():
            # A relation is verified once per side and the two sides now build an identical
            # prompt, so they agree; keeping the stored row also keeps its original run_id.
            if verdicts_by_id.get(iid, {}).get("verdict") != "unsupported":
                verdicts_by_id[iid] = {**stored, "target_id": iid}
        reused += len(carried)

        if to_check:
            checked += 1
            verify_mod.attach_definitions(to_check, settings)
            try:
                flagged += verify_mod.verify_facts(
                    verify_mod.subject_label(entity, upto), to_check, client,
                    context=evidence_context.for_facts(to_check), workers=workers,
                )
            except (LLMError, OSError, ValueError) as exc:
                conn.close()
                run.finish("failed", error=str(exc))
                console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
                raise typer.Exit(code=1) from exc

        flagged_ids = {f["interval_id"]: f["rationale"] for f in flagged}
        for fact in to_check:
            iid = fact["interval_id"]
            # "unsupported" stays sticky across a relation's two sides rather than whichever pass
            # ran last silently overwriting the other -- the same "never silently pick a winner"
            # posture CLAUDE.md sets for every other conflict in this pipeline.
            if verdicts_by_id.get(iid, {}).get("verdict") == "unsupported":
                continue
            verdicts_by_id[iid] = {
                "target_id": iid,
                "verdict": "unsupported" if iid in flagged_ids else "supported",
                "rationale": flagged_ids.get(iid),
                "model": model_name,
                "run_id": run.run_id,
                "context_sha256": context_hashes[iid],
                "evidence_cutoff": upto,
                "model_revision": verify_mod.METHOD_REVISION,
            }

        kept_note = f", {len(carried)} kept" if carried else ""
        if flagged:
            results.append(
                {
                    "entity_id": entity["entity_id"], "canonical": entity["canonical"],
                    "upto_vol": upto, "flagged": flagged,
                }
            )
            console.print(f"  {entity['canonical']}: [yellow]{len(flagged)} flagged[/yellow]{kept_note}")
        else:
            console.print(f"  {entity['canonical']}: ok{kept_note}")

    # Phase 23 B5: this is what actually activates B1/B2's withholding machinery -- until this
    # write, `verdicts` stayed empty forever and synth/assemble.py's/site/bundle.py's checks were
    # dead code. Written before close() so a page built with `wiki synthesize` right after this
    # run sees today's verdicts, not last run's.
    store.write_verdicts(conn, verdicts_by_id.values())
    conn.close()
    # Both endpoints may flag the same relationship. Match the unique-interval denominator,
    # rather than reporting two failures for one fact (or an impossible rate above 100%).
    flagged_total = sum(v["verdict"] == "unsupported" for v in verdicts_by_id.values())
    doc = {
        "upto_vol": upto,
        "results": results,
        "summary": {
            "characters_checked": checked, "facts_reused": reused,
            "characters_flagged": len(results), "facts_flagged": flagged_total,
            "facts_checked": len(verdicts_by_id),  # Phase 23 B5 -- denominator for audit's unsupported-rate gate
        },
    }
    paths.verification().write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    run.finish("ok")
    console.print(
        f"\n[green]Done.[/green] {checked} character(s) checked"
        + (f", {reused} fact(s) unchanged (verdicts kept)" if reused else "")
        + f", {len(results)} with a flagged fact ({flagged_total} total)"
        + f" -> {paths.relative(paths.verification())}"
    )
    console.print("Run [cyan]wiki audit verify[/cyan] to review.")


@app.command("events-build")
def events_build(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    force: ForceOpt = False,
    set_: SetOpt = None,
) -> None:
    """[Phase 19] Build the event layer from scene records.

    Reads data/02b_scenes/ and writes data/04b_events/events.db — one event node per non-empty
    scene span plus state_change and quote claims anchored to each event. No LLM involved.
    Run `wiki events-build --force` to rebuild from scratch (clears the DB first).
    """
    from .config import parse_volume_range
    from .graph.event_build import build_events
    from .graph.events import connect as connect_events, reset as reset_events

    settings = _settings(series, set_=set_)

    scene_files: list = []
    if paths.SCENES_DIR.is_dir():
        all_scene_files = sorted(paths.SCENES_DIR.glob("v*.jsonl"))
        if volumes:
            try:
                all_vols = sorted(int(p.stem[1:]) for p in all_scene_files)
                # series-agnostic fallback when no scene files exist yet to infer a range from
                # (docs/vision/plans/0008-pre-full-scale-audit.md Sec3.6 item 3 -- this used to be a
                # hard-coded 13-volume range, an 86-ism in an otherwise series-agnostic command).
                wanted = parse_volume_range(volumes, all_vols or settings.volume_numbers())
            except ConfigError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            scene_files = [p for p in all_scene_files if int(p.stem[1:]) in set(wanted)]
        else:
            scene_files = all_scene_files

    missing = [p for p in scene_files if not p.is_file()] if scene_files else []
    if not scene_files:
        console.print(
            "[red]No scene files found.[/red] Run [cyan]wiki scenes[/cyan] first."
        )
        raise typer.Exit(code=1)

    volume_scope = max((int(p.stem[1:]) for p in scene_files), default=None)
    console.print(
        f"Building event layer from {len(scene_files)} scene file(s) "
        f"({'force rebuild' if force else 'incremental'})..."
    )

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, "events_build",
        scope=f"v1-{volume_scope}" if volume_scope else "all",
        volume_scope=volume_scope,
        stage_keys=["events"],
        settings=settings,
    )

    conn = connect_events()
    n_events, n_claims = build_events(scene_files, conn, force=force)
    conn.close()

    run.finish("ok")
    console.print(
        f"\n[green]Done.[/green] {n_events} event(s), {n_claims} event claim(s) "
        f"-> {paths.relative(paths.events_db())}"
    )
    console.print("Run [cyan]wiki audit events[/cyan] to review.")


@app.command()
def explain(
    name: Annotated[str, typer.Argument(help="Character name or entity id.")],
    upto: UptoOpt = None,
    series: SeriesOpt = None,
    set_: SetOpt = None,
) -> None:
    """[Phase 5] Print the evidence a page would be built from at this volume cutoff.

    Shows the assembled field values AND, for each one, the claim(s) and quoted passage(s) they
    rest on — a debug view of synth/assemble.py's output before Phase 6 spends any tokens on
    prose. Nothing here calls a model.
    """
    import json

    from .entities.gazetteer import load as load_gazetteer
    from .graph import ppr, temporal
    from .graph.events import connect as connect_events
    from .graph.store import connect as connect_graph
    from .synth.assemble import assemble_page

    settings = _settings(series, set_=set_)
    upto = _resolve_upto(settings, upto)

    if not paths.graph_db().is_file():
        console.print("[red]No graph found.[/red] Run [cyan]wiki graph build[/cyan] first.")
        raise typer.Exit(code=1)
    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)

    gaz = load_gazetteer()
    entity = _find_character(gaz, name)
    entities_by_id = {e["entity_id"]: e for e in gaz["entities"]}

    conn = connect_graph()
    events_conn = connect_events() if paths.events_db().is_file() else None
    page = assemble_page(conn, events_conn, entity, upto, settings, entities_by_id)
    if events_conn is not None:
        events_conn.close()
    quote_chars = 0

    def _evidence_lines(claim_ids: list[str]) -> list[str]:
        nonlocal quote_chars
        lines: list[str] = []
        for ev in temporal.evidence_at(conn, claim_ids, upto):
            quote_chars += len(ev["quote"])
            lines.append(f"[dim]conf={ev['confidence']:.2f} {ev['para_id']}:[/dim] \"{ev['quote']}\"")
        return lines

    def _label(entity_id: str) -> str:
        other = entities_by_id.get(entity_id)
        return other["canonical"] if other else entity_id

    console.print(
        Panel(
            f"[bold]{entity['canonical']}[/bold]  ({entity['entity_id']})\n"
            f"As known to a reader through Volume {upto}",
            border_style="blue",
        )
    )

    # --- attribute fields, each with the interval(s) and evidence behind it -------------------
    attr_table = Table(title="Fields", header_style="bold", show_lines=True)
    attr_table.add_column("Field", no_wrap=True)
    attr_table.add_column("Value")
    attr_table.add_column("Evidence")

    for predicate, cfg in sorted(settings.attributes.items()):
        key = predicate.lower()
        if key not in page["fields"]:
            continue
        field = page["fields"][key]
        if cfg.get("single"):
            rows = [r for r in temporal.state_at(conn, entity["entity_id"], upto) if r["predicate"] == predicate]
            history_rows = [
                r for r in temporal.history_at(conn, entity["entity_id"], upto) if r["predicate"] == predicate
            ]
            value_text = f"{field['value']} (since v{field['since_vol']})"
            for h in history_rows:
                value_text += f"\n[dim]previously: {h['value']} (v{h['vol_start']}-{h['vol_end']})[/dim]"
            evidence = [ln for r in (*rows, *history_rows) for ln in _evidence_lines(json.loads(r["claim_ids_json"]))]
        else:
            rows = [r for r in temporal.state_at(conn, entity["entity_id"], upto) if r["predicate"] == predicate]
            value_text = ", ".join(f"{v['value']} (v{v['since_vol']})" for v in field)
            evidence = [ln for r in rows for ln in _evidence_lines(json.loads(r["claim_ids_json"]))]
        attr_table.add_row(cfg.get("display", predicate), value_text or "-", "\n".join(evidence) or "-")

    if attr_table.row_count:
        console.print(attr_table)

    # --- relations: affiliations + relationships, same evidence treatment ---------------------
    rel_rows = temporal.relations_at(conn, entity["entity_id"], upto)
    if rel_rows:
        rel_table = Table(title="Relations", header_style="bold", show_lines=True)
        rel_table.add_column("Predicate", no_wrap=True)
        rel_table.add_column("With")
        rel_table.add_column("Evidence")
        for r in rel_rows:
            other = r["object"] if r["subject"] == entity["entity_id"] else r["subject"]
            rel_table.add_row(
                r["predicate"], _label(other), "\n".join(_evidence_lines(json.loads(r["claim_ids_json"]))) or "-"
            )
        console.print(rel_table)

    # --- PPR-ranked multi-hop related entities -------------------------------------------------
    related = ppr.personalized_pagerank(conn, entity["entity_id"], upto)
    if related:
        ppr_table = Table(title="Related (multi-hop, Personalized PageRank)", header_style="bold")
        ppr_table.add_column("Entity")
        ppr_table.add_column("Score", justify="right")
        for r in related:
            ppr_table.add_row(_label(r["entity_id"]), f"{r['score']:.4f}")
        console.print(ppr_table)

    est_tokens = quote_chars // 4  # rough chars/4 estimate; the same evidence text this cutoff's
    # prose prompt would cite (Phase 6) — not a request to a model, just a sizing preview.
    console.print(
        f"\n[dim]claim_set_hash: {page['claim_set_hash']}  |  "
        f"~{est_tokens:,} evidence tokens (chars/4 estimate) would feed Phase 6 prose[/dim]"
    )
    conn.close()


def _polish_threshold(characters: list[dict], percentile: float) -> float:
    """The `importance` value at `percentile` among CHARACTER entities — anyone at or above it
    is a "major character" for `--polish` routing (config/models.yaml `budget.
    polish_importance_percentile`). Nearest-rank, no numpy dependency: fine at a few hundred
    entities and matches `entities/gazetteer.py::compute_importance`'s own precision."""
    if not characters:
        return 1.0
    values = sorted(e["importance"] for e in characters)
    idx = min(len(values) - 1, int(percentile * len(values)))
    return values[idx]


@app.command()
def synthesize(
    upto: UptoOpt = None,
    series: SeriesOpt = None,
    force: ForceOpt = False,
    polish: Annotated[bool, typer.Option("--polish", help="Route major characters' prose to the API model.")] = False,
    entities: Annotated[Optional[str], typer.Option(help="Comma-separated names, to regenerate a few pages.")] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    allow_partial: AllowPartialOpt = False,
) -> None:
    """[Phase 6] Build page models and generate the short background and personality prose.

    Builds every cutoff from a character's `first_vol` through `--upto`, in order. A cutoff
    whose visible claim set is unchanged from the last one actually written is skipped entirely
    — no LLM call, no new file (CONTRACTS §5.1) — which is what keeps a 13-volume run to roughly
    2-3 generations per character instead of 13.
    """
    import json

    from .entities.gazetteer import load as load_gazetteer
    from .graph.store import connect as connect_graph
    from .llm.client import LLMClient, LLMError
    from .site.okf import render_markdown
    from .synth import cache as page_cache
    from .synth.assemble import assemble_page, find_name_collisions, has_min_evidence
    from .synth.prose import generate_prose, generate_relationship_prose

    settings = _settings(series, model, set_)
    upto = _resolve_upto(settings, upto)
    _guard_upstream_failures(["gazetteer", "graph"], allow_partial)

    if not paths.graph_db().is_file():
        console.print("[red]No graph found.[/red] Run [cyan]wiki graph build[/cyan] first.")
        raise typer.Exit(code=1)
    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)

    gaz = load_gazetteer()
    entities_by_id = {e["entity_id"]: e for e in gaz["entities"]}
    characters = [e for e in gaz["entities"] if e["type"] == "CHARACTER"]

    # Phase 22 A5 (S5): a same-name, different-type collision (Saint Ruvinheigen the character
    # vs. the city) is a gazetteer-wide check, not per-page -- print once, don't fail the build.
    collisions = find_name_collisions(gaz["entities"])
    if collisions:
        console.print("[yellow]Name collisions (different entity types, same canonical name):[/yellow]")
        for name, ids in collisions:
            console.print(f"  [yellow]{name}[/yellow]: {', '.join(ids)}")

    if entities:
        wanted_names = {n.strip().lower() for n in entities.split(",") if n.strip()}
        characters = [
            e for e in characters if e["canonical"].lower() in wanted_names or e["entity_id"] in wanted_names
        ]
    if not characters:
        console.print("[red]No CHARACTER entities match.[/red]")
        raise typer.Exit(code=1)

    threshold = _polish_threshold(
        [e for e in gaz["entities"] if e["type"] == "CHARACTER"],
        float(settings.budget_config.get("polish_importance_percentile", 0.85)),
    )

    paths.ensure_dirs()
    run = provenance.start_run(
        settings.series_id, "synthesize",
        scope=f"upto{upto}", volume_scope=upto, stage_keys=["pages"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=upto)
    conn = connect_graph()

    # Open events.db if it exists — the chronology and quotes sections read from it (Phase 19/20).
    # If absent (events-build not yet run), generate_prose receives None and silently skips
    # the chronology section; build_quotes_section also returns None gracefully.
    from .graph.events import connect as connect_events
    from .synth.assemble import build_quotes_section
    events_conn = connect_events() if paths.events_db().is_file() else None

    # Identify the quotes section config once (may be None if no kind: quotes in page_outline).
    quotes_section_cfg = next(
        (s for s in settings.page_outline if s["kind"] == "quotes"), None
    )

    min_claims = int(settings.page_gate_config.get("min_claims", 1))
    written = 0
    skipped = 0
    generated = 0
    gated = 0

    for entity in characters:
        first_vol = int(entity.get("first_vol", 1))
        if first_vol > upto:
            continue

        for vol in range(first_vol, upto + 1):
            # Phase 22 A5 (S5): a crash-truncated run can leave a character with zero real
            # evidence at this cutoff -- skip before assemble_page even runs rather than write a
            # blank page (config/extraction.yaml page_gate.min_claims). Also remove any page file
            # already on disk for this cutoff: a re-extraction can legitimately take a cutoff from
            # "had evidence" to "gated" (e.g. a phantom-entity fix removes the claims that used to
            # clear the bar), and a stale file left behind would otherwise keep serving pre-gate
            # (possibly pre-A5-shape) data to every downstream reader indefinitely.
            if not has_min_evidence(conn, entity["entity_id"], vol, min_claims):
                gated += 1
                paths.page_json(entity["entity_id"], vol).unlink(missing_ok=True)
                paths.page_markdown(entity["entity_id"], vol).unlink(missing_ok=True)
                continue
            page = assemble_page(conn, events_conn, entity, vol, settings, entities_by_id)
            prev = None if force else page_cache.previous_page(entity["entity_id"], vol)
            if page_cache.unchanged(prev, page["claim_set_hash"]):
                skipped += 1
                # [30] A skipped cutoff is served by the earlier page, so a file left here by an
                # older run would shadow it: v02 pages kept rendering without the GENDER/AGE the
                # fresh v01 page gained, because each v02.json predated the change.
                paths.page_json(entity["entity_id"], vol).unlink(missing_ok=True)
                paths.page_markdown(entity["entity_id"], vol).unlink(missing_ok=True)
                continue

            is_major = entity["importance"] >= threshold
            stage = "prose_polish" if (polish and is_major) else "prose"

            try:
                prose = generate_prose(
                    conn, client, entity, vol, settings, stage=stage, events_conn=events_conn
                )
                # Phase 26: one paragraph per related CHARACTER, explaining what the bond
                # consists of. One call per page (not per pair) so the model can reconcile a
                # pair carrying conflicting predicates. Cached with the page: `claim_set_hash`
                # already covers relation intervals, so this regenerates exactly when the
                # relations behind it move.
                page["relationship_prose"] = generate_relationship_prose(
                    events_conn, client, entity, page, vol, entities_by_id, stage=stage
                )
            except LLMError as exc:
                conn.close()
                if events_conn is not None:
                    events_conn.close()
                run.finish("failed", error=str(exc))
                console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
                raise typer.Exit(code=1) from exc

            page["prose"] = prose
            if any(prose.values()):
                generated += 1
                page["generated_by"] = str(client.profile_for(stage))

            # Phase 20: fill in the quotes section deterministically (no LLM).
            if quotes_section_cfg is not None and "quotes" in page:
                if events_conn is not None:
                    # Pass canonical name so speaker surface-form matching works.
                    cfg_with_hint = dict(quotes_section_cfg, _entity_canonical=entity["canonical"])
                    page["quotes"] = build_quotes_section(
                        events_conn, entity["entity_id"], vol, cfg_with_hint
                    )

            page_dir = paths.page_dir(entity["entity_id"])
            page_dir.mkdir(parents=True, exist_ok=True)
            paths.page_json(entity["entity_id"], vol).write_text(
                json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            paths.page_markdown(entity["entity_id"], vol).write_text(
                render_markdown(page, entity, settings, entities_by_id), encoding="utf-8"
            )
            written += 1
        console.print(f"  {entity['canonical']}: done")

    conn.close()
    if events_conn is not None:
        events_conn.close()
    run.finish("ok")
    console.print(
        f"\n[green]Done.[/green] {written} page(s) written, {skipped} cutoff(s) unchanged "
        f"(skipped), {gated} cutoff(s) gated for insufficient evidence, {generated} prose "
        f"generation(s) -> {paths.relative(paths.PAGES_DIR)}"
    )
    console.print("Run [cyan]wiki audit pages[/cyan] to review.")


def _memorization_rows(settings, cutoffs: list[int], n_items: int, run) -> list[dict[str, Any]]:
    """[31] `probe run memorization`: aligned items from `decontaminate.from_series` and this
    series, measured by probe/memorization.py, summary row last."""
    import json

    from .llm.client import LLMClient
    from .llm.parallel import workers_for
    from .probe import memorization as mem

    cfg = settings.series.get("decontaminate") or {}
    if not cfg.get("from_series"):
        console.print("[red]`memorization` runs on a decontaminated series (config `decontaminate:`).[/red]")
        raise typer.Exit(code=1)

    def load(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    original = [r for v in cutoffs for r in load(paths.parsed_volume_of(cfg["from_series"], v))]
    derived = [r for v in cutoffs for r in load(paths.parsed_volume(v))]
    items = mem.select_items(original, derived, cfg["entity_map"], n=n_items)
    client = LLMClient(settings, run=run, volume_scope=max(cutoffs))
    console.print(f"memorization: {len(items)} aligned item(s), {4 * len(items)} call(s) "
                  f"against {settings.resolve_role(mem.STAGE)}")
    rows = mem.measure(client, items, cfg.get("recognize") or {}, workers_for(client, mem.STAGE))
    rows = [{**r, "series": settings.series_id, "upto_vol": max(cutoffs)} for r in rows]
    summary = {**mem.summarize(rows), "series": settings.series_id, "upto_vol": max(cutoffs),
               "model": f"{settings.resolve_role(mem.STAGE).provider}:{settings.resolve_role(mem.STAGE).model}"}
    console.print(summary)
    return rows + [summary]


def _parametric_rows(settings, cutoffs: list[int], run) -> list[dict[str, Any]]:
    """[31] `probe run parametric`: pages for each gold character from volumes <= t and from
    the name alone, scored for future and control facts (probe/parametric.py)."""
    import json

    import yaml

    from .llm.client import LLMClient
    from .llm.parallel import map_calls, workers_for
    from .probe import parametric as par
    from .probe.report import probe_report_path

    t = max(cutoffs)
    cfg = settings.series.get("decontaminate") or {}
    source = cfg.get("from_series", settings.series_id)
    gold_path = paths.DOCS_DIR / "eval" / "parametric" / f"{source}.yaml"
    if not gold_path.is_file():
        console.print(f"[red]No fact file {paths.relative(gold_path)}.[/red]")
        raise typer.Exit(code=1)
    gold = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
    entity_map = cfg.get("entity_map") or {}
    records = [json.loads(line) for v in range(1, t + 1)
               for line in paths.parsed_volume(v).read_text(encoding="utf-8").splitlines() if line.strip()]
    text = par.volume_text(records)
    jobs = par.build_prompts(gold, text, entity_map, t)
    keywords = par.valid_keywords(gold, text, entity_map, t)
    client = LLMClient(settings, run=run, volume_scope=t)
    console.print(f"parametric: {len(jobs)} page(s) at t={t} against {settings.resolve_role(par.STAGE)}")
    pages = map_calls(lambda job: client.complete(par.STAGE, job["prompt"]), jobs, workers_for(client, par.STAGE))
    rows = par.score(client, jobs, pages, gold, entity_map, keywords, t)
    page_map = {(j["character"], j["mode"]): p for j, p in zip(jobs, pages)}
    pages_path = probe_report_path(run.run_id).with_name("pages.json")
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps([{"character": c, "mode": m, "page": p} for (c, m), p in page_map.items()],
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    profile = settings.resolve_role(par.STAGE)
    summaries = [{**sm, "series": settings.series_id, "upto_vol": t, "model": f"{profile.provider}:{profile.model}"}
                 for sm in par.summarize(rows, page_map)]
    for sm in summaries:
        console.print(sm)
    return [{**r, "series": settings.series_id, "upto_vol": t} for r in rows] + summaries


@probe_app.command("run")
def probe_run(
    channel: Annotated[str, typer.Argument(help="Probe: 'index' (selection), 'disclosure' (surface occurrence), or 'leak' (future facts).")] = "index",
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    json_out: Annotated[
        Optional[str], typer.Option("--json", help="Write JSONL rows here instead of just printing a summary.")
    ] = None,
    n_items: Annotated[int, typer.Option("--n", help="memorization: number of aligned cloze items.")] = 100,
    model: ModelOpt = None,
) -> None:
    """[Phase 24] Measure a spoiler-leak channel that the query-time gate cannot reach.

    `index` (L_build): re-runs candidate mining restricted to each cutoff and diffs it against
    the shipped, whole-corpus-mined gazetteer -- zero LLM calls, reads only files already on
    disk. See src/narrativewiki/probe/channels/index.py.
    `leak` (L_query): searches cutoff page artifacts for exact future-fact values, with no LLM calls.
    `disclosure` (L_build): checks exact and normalized surface occurrence in cutoff text.
    `memorization` (L_param, [31]): name cloze + title identification on the same paragraphs of
    the original and a decontaminated series (run it on the decontaminated one). BILLS: one
    call per item per probe per condition (4n), stage `probe_memorization`.
    `parametric` (L_param, [31]): pages written from volumes <= t (`--volumes t`) or from the name
    alone, scored against docs/eval/parametric/<source>.yaml by local MiniCheck. BILLS: two
    calls per character (stage `parametric_page`), one with volumes <= t in context.
    """
    from .config import parse_volume_range
    from .probe import report as probe_report

    if channel not in {"index", "leak", "disclosure", "memorization", "parametric"}:
        console.print(f"[red]Unknown probe channel {channel!r}. Choose 'index', 'disclosure', 'leak', 'memorization' or 'parametric'.[/red]")
        raise typer.Exit(code=1)

    settings = _settings(series, model)
    parsed_volumes = [v for v in settings.volume_numbers() if paths.parsed_volume(v).is_file()]
    if not parsed_volumes:
        console.print("[red]No parsed volumes found.[/red] Run [cyan]wiki ingest[/cyan] first.")
        raise typer.Exit(code=1)
    try:
        cutoffs = parse_volume_range(volumes, parsed_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # [24] registered in the run ledger like every other stage command, so `wiki runs`/`wiki
    # status` see a probe run and `wiki audit probe` (audit/reports.py) can find its output --
    # previously probe_run wrote only wherever --json pointed, if given at all, and never touched
    # provenance.start_run (docs/vision/plans/0008-pre-full-scale-audit.md S5).
    run = provenance.start_run(
        settings.series_id, "probe",
        scope=f"{channel}:v{min(cutoffs)}-{max(cutoffs)}", volumes=cutoffs, volume_scope=max(cutoffs),
        settings=settings,
    )
    if channel == "memorization":
        rows = _memorization_rows(settings, cutoffs, n_items, run)
    elif channel == "parametric":
        rows = _parametric_rows(settings, cutoffs, run)
    elif channel == "leak":
        rows = probe_report.run_leak_channel(settings.series_id, cutoffs)
    elif channel == "disclosure":
        rows = probe_report.run_disclosure_channel(settings.series_id, cutoffs)
    else:
        rows = probe_report.run_index_channel(settings.series_id, settings.series, cutoffs)
    canonical_path = probe_report.probe_report_path(run.run_id)
    probe_report.write_jsonl(rows, canonical_path)
    errors = [row for row in rows if "error" in row]
    if errors:
        run.finish("failed", error=f"{len(errors)} probe measurement(s) failed; see probe/index.jsonl")
    else:
        run.finish("ok")
    console.print(f"{len(rows)} row(s) -> [cyan]{paths.relative(canonical_path)}[/cyan] (see [cyan]wiki audit probe[/cyan])\n")

    for row in rows:
        if "error" in row:
            console.print(f"[yellow]t={row['upto_vol']} {row['probe']}: {row['error']}[/yellow]")
            continue
        if row["probe"] == "candidate_mining":
            console.print(
                f"t={row['upto_vol']}  candidate_mining  "
                f"leak_rate={row['leak_rate']:.3f}  "
                f"({len(row['surfaces_missing_at_cutoff'])}/{row['cutoff_visible_surface_forms']} "
                f"surface forms would not survive a cutoff-only mining run)"
            )
        elif row["probe"] == "vocabulary_exposure":
            console.print(
                f"t={row['upto_vol']}  vocabulary_exposure  "
                f"tainted_claim_rate={row['tainted_claim_rate']:.3f}  "
                f"future_surface_form_rate={row['future_surface_form_rate']:.3f}"
            )

        elif row["probe"] == "future_fact_leak":
            console.print(
                f"t={row['upto_vol']}  future_fact_leak  rate={row['future_fact_leak_rate']:.3f} "
                f"({row['leaked_count']}/{row['future_claims_total']} "
                f"future facts findable in the cutoff artifact)"
            )

        elif row["probe"] == "first_occurrence":
            console.print(
                f"t={row['upto_vol']}  first_occurrence  "
                f"unseeable_exact_rate={row['unseeable_exact_rate']:.3f}  "
                f"unseeable_normalised_rate={row['unseeable_normalised_rate']:.3f} "
                f"({row['cutoff_visible_surface_forms']} surface forms)"
            )

    if json_out:
        out_path = Path(json_out)
        probe_report.write_jsonl(rows, out_path)
        console.print(f"\n[green]{len(rows)} row(s)[/green] -> {out_path}")

    if errors:
        raise typer.Exit(code=1)


@site_app.command("build")
def site_build(
    series: SeriesOpt = None,
    upto: UptoOpt = None,
    force: ForceOpt = False,
    entities: Annotated[
        Optional[str], typer.Option(help="Comma-separated names/ids, to limit codex regeneration for a smoke test.")
    ] = None,
    model: ModelOpt = None,
    set_: SetOpt = None,
    allow_partial: AllowPartialOpt = False,
    html: Annotated[
        bool,
        typer.Option(
            "--html",
            help="Also render the Markdown wiki to browsable HTML via `mkdocs build` (requires "
            "the `wiki` extra: pip install -e \".[wiki]\"). The .md tree is written either way.",
        ),
    ] = False,
) -> None:
    """[Phase 7] Hyperlink entity mentions, assemble the JSON bundle, and build the MkDocs wiki.

    Reads every `data/05_pages/<id>/v{NN}.json` at or below `--upto` (already written by
    `wiki synthesize`) and every codex-typed gazetteer entity, and writes `data/06_bundle/` plus
    `dist/wiki/` (`site/mkdocs_wiki.py`, Phase 25). `--force` regenerates a codex entry's summary even where its evidence/members/
    related set is unchanged from the previous cutoff (CONTRACTS §6's per-entry cache); it does
    not affect character pages, which are always copied through fresh from whatever `wiki
    synthesize` already wrote. `--entities` limits which codex entities are (re)processed — the
    character roster and index/links/search bundles are still built for everyone, since those are
    cheap disk reads with no LLM cost. Also writes `data/06_bundle/relationships/<a>--<b>.json`
    (Phase 21) for every CHARACTER pair with a relation edge, read from `graph.db` and (if
    `wiki events build` has run) `events.db` — fully deterministic, no LLM cost regardless of
    `--force`/`--entities`.
    """
    import json

    from .entities.automaton import build_automaton
    from .entities.gazetteer import load as load_gazetteer
    from .graph import events as events_store
    from .graph.store import connect as connect_graph
    from .llm.client import LLMClient, LLMError
    from .site import bundle as bundle_mod
    from .site.mkdocs_wiki import build_wiki

    settings = _settings(series, model, set_)
    upto = _resolve_upto(settings, upto)
    _guard_upstream_failures(["gazetteer", "graph", "pages"], allow_partial)

    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)
    if not paths.graph_db().is_file():
        console.print("[red]No graph found.[/red] Run [cyan]wiki graph build[/cyan] first.")
        raise typer.Exit(code=1)
    if not paths.PAGES_DIR.is_dir() or not any(paths.PAGES_DIR.iterdir()):
        console.print("[red]No pages found.[/red] Run [cyan]wiki synthesize --upto N[/cyan] first.")
        raise typer.Exit(code=1)

    gaz = load_gazetteer()
    entities_by_id = {e["entity_id"]: e for e in gaz["entities"]}
    characters = [e for e in gaz["entities"] if e["type"] == "CHARACTER"]

    wanted_ids: set[str] | None = None
    if entities:
        wanted_names = {n.strip().lower() for n in entities.split(",") if n.strip()}
        wanted_ids = {
            e["entity_id"]
            for e in gaz["entities"]
            if e["canonical"].lower() in wanted_names or e["entity_id"] in wanted_names
        }
        if not wanted_ids:
            console.print("[red]No entities match the given --entities filter.[/red]")
            raise typer.Exit(code=1)

    paths.ensure_dirs()
    console.print(f"Building the Aho-Corasick automaton over {len(gaz['entities'])} entities...")
    automaton = build_automaton(gaz["entities"])

    console.print(f"Loading paragraphs and mentions for volume(s) 1-{upto}...")
    records_by_vol: dict[int, list[dict]] = {}
    for vol in range(1, upto + 1):
        path = paths.parsed_volume(vol)
        if path.is_file():
            records_by_vol[vol] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    mentions_by_vol: dict[int, list[dict]] = {v: [] for v in range(1, upto + 1)}
    if paths.mentions().is_file():
        for line in paths.mentions().read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            mention = json.loads(line)
            if mention["vol"] in mentions_by_vol:
                mentions_by_vol[mention["vol"]].append(mention)

    # Phase 23 E4: para_id -> print_page, from the paragraph records already loaded above --
    # attaches visible "vol N, p.NNN" citations to every prose section (site/bundle.py::
    # _citations_for), surfacing CLAUDE.md §3 trap #8's print-page markers for the first time.
    print_pages: dict[str, int] = {
        r["para_id"]: r["print_page"]
        for records in records_by_vol.values()
        for r in records
        if r.get("print_page") is not None
    }

    console.print("Assembling character page bundles...")
    pages_bundle = bundle_mod.build_pages_bundle(characters, automaton, entities_by_id, settings, upto, print_pages)
    # Phase 26 part B: which characters are a live link target AT EACH CUTOFF -- `mkdocs_wiki.py
    # ::resolve_cutoff` emits a character at volume N iff it has a page at some cutoff <= N, so a
    # character first written at v02 is a 404 on every v01 page. Flat `set(pages_bundle)` was too
    # coarse and let `[Jakob](../character/jakob.md)` ship on v01's Lawrence page.
    written_ids_at = {
        vol: {
            eid for eid, doc in pages_bundle.items()
            if any(int(k) <= vol for k in doc.get("cutoffs", {}))
        }
        for vol in range(1, upto + 1)
    }
    pages_dir = paths.BUNDLE_DIR / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    # This stage owns pages_dir exclusively, so any file here for an entity `build_pages_bundle`
    # no longer returns (e.g. every one of its cutoffs is now gated for insufficient evidence --
    # cli.py's synthesize gate) is stale and must go, or a re-extraction that legitimately takes a
    # character below the evidence floor would otherwise keep serving its old bundle forever.
    for stale in pages_dir.glob("*.json"):
        if stale.stem not in pages_bundle:
            stale.unlink()
    for entity_id, doc in pages_bundle.items():
        paths.bundle_page(entity_id).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  {len(pages_bundle)} character page bundle(s) -> {paths.relative(pages_dir)}")

    console.print(f"Generating codex summaries against {settings.resolve_role('codex_summary')}...")
    run = provenance.start_run(
        settings.series_id, "site_build",
        scope=f"upto{upto}", volume_scope=upto, stage_keys=["bundle", "site"], settings=settings,
    )
    client = LLMClient(settings, run=run, volume_scope=upto)
    conn = connect_graph()
    events_conn = events_store.connect() if paths.events_db().is_file() else None
    try:
        codex_bundles = bundle_mod.build_codex_bundles(
            conn, client, gaz, entities_by_id, automaton, settings, upto,
            mentions_by_vol, records_by_vol, events_conn=events_conn, force=force, wanted_ids=wanted_ids,
            written_ids_at=written_ids_at,
        )
        relationship_bundles = bundle_mod.build_relationship_bundles(
            conn, events_conn, entities_by_id, automaton, settings, upto, force=force, wanted_ids=wanted_ids,
            written_ids_at=written_ids_at,
        )
        # Phase 26: the same `written_ids` filter Phase 23 E6 applied to roster/links/search
        # (computed below) also has to reach timeline participants -- see build_timeline_bundles.
        timeline_bundles = bundle_mod.build_timeline_bundles(
            events_conn, upto, automaton, entities_by_id, settings, written_ids_at=written_ids_at
        )
    except LLMError as exc:
        run.finish("failed", error=str(exc))
        console.print(f"\n[red]{exc}[/red]\nRun [cyan]wiki doctor[/cyan] to check the model is reachable.")
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()
        if events_conn is not None:
            events_conn.close()

    codex_dir = paths.BUNDLE_DIR / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    # Only clean up stale files on a full run -- `wanted_ids` (--entities) deliberately restricts
    # codex_bundles to a subset for a cheap smoke test, and every OTHER kind's file must survive
    # that narrow rebuild untouched.
    if wanted_ids is None:
        for stale in codex_dir.glob("*.json"):
            if stale.stem not in codex_bundles:
                stale.unlink()
    for kind, doc in codex_bundles.items():
        paths.bundle_codex(kind).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"  codex/{kind}.json: {len(doc)} entr(ies)")

    rel_dir = paths.BUNDLE_DIR / "relationships"
    rel_dir.mkdir(parents=True, exist_ok=True)
    if wanted_ids is None:
        live_pairs = {f"{a}--{b}" for doc in relationship_bundles.values() for a, b in [doc["pair"]]}
        for stale in rel_dir.glob("*.json"):
            if stale.stem not in live_pairs:
                stale.unlink()
    for pair_key, doc in relationship_bundles.items():
        a, b = doc["pair"]
        paths.bundle_relationship(a, b).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  {len(relationship_bundles)} relationship page bundle(s) -> {paths.relative(rel_dir)}")

    timeline_dir = paths.BUNDLE_DIR / "timeline"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    if wanted_ids is None:  # [33] Anne's v06.json from an --upto 6 build broke `audit links` at --upto 5
        for stale in timeline_dir.glob("v*.json"):
            if stale.name not in {paths.bundle_timeline(v).name for v in timeline_bundles}:
                stale.unlink()
    for vol, doc in timeline_bundles.items():
        paths.bundle_timeline(vol).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  {len(timeline_bundles)} timeline page(s) -> {paths.relative(timeline_dir)}")

    # Phase 23 E6: restrict the roster/links/search CHARACTER entries to entity_ids that actually
    # got a page written (`pages_bundle`'s own keys) -- a character gated out everywhere (e.g. by
    # page_gate.min_claims) must not still appear as a dangling, 404-on-click roster/search/link
    # entry (site/bundle.py::build_index's docstring has the full rationale).
    written_ids = set(pages_bundle)

    console.print("Writing index.json...")
    index_doc = bundle_mod.build_index(gaz, settings, upto, written_ids)
    index_doc["relationship_pairs"] = sorted(doc["pair"] for doc in relationship_bundles.values())
    index_doc["timeline_volumes"] = sorted(timeline_bundles)
    paths.bundle_index().write_text(json.dumps(index_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    console.print("Writing links.json and search.json...")
    paths.bundle_links().write_text(
        json.dumps(bundle_mod.build_links(gaz, settings, upto, written_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.bundle_search().write_text(
        json.dumps(bundle_mod.build_search(gaz, settings, upto, written_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    console.print("Writing the MkDocs wiki...")
    build_wiki(
        settings,
        pages_bundle=pages_bundle,
        codex_bundles=codex_bundles,
        relationship_bundles=relationship_bundles,
        timeline_bundles=timeline_bundles,
        index_doc=index_doc,
        entities_by_id=entities_by_id,
        upto=upto,
    )

    if html:
        import importlib.util
        import subprocess

        # [30] The module, run by this interpreter: `shutil.which("mkdocs")` missed a venv whose
        # Scripts/ is not on PATH and reported an installed mkdocs as missing.
        if importlib.util.find_spec("mkdocs") is None:
            console.print(
                "[red]--html requested but `mkdocs` is not installed.[/red] "
                "Run [cyan]pip install -e \".\\[wiki]\"[/cyan] first."
            )
            run.finish("failed", error="mkdocs not installed for --html")
            raise typer.Exit(code=1)
        console.print("Rendering HTML with mkdocs...")
        result = subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "-f", str(paths.wiki_mkdocs_yml())],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]mkdocs build failed:[/red]\n{result.stderr}")
            run.finish("failed", error="mkdocs build failed")
            raise typer.Exit(code=1)
        console.print(f"  HTML -> {paths.relative(paths.wiki_html_dir())}")

    run.finish("ok")
    console.print(
        f"\n[green]Done.[/green] {paths.relative(paths.BUNDLE_DIR)} -> {paths.relative(paths.SITE_DIR)}"
    )
    console.print(
        "Run [cyan]wiki audit links[/cyan] to verify every link resolves. "
        "Read the .md tree directly, or re-run with [cyan]--html[/cyan] (needs "
        "`pip install -e \".\\[wiki]\"`) and [cyan]wiki serve[/cyan] for a browsable site."
    )


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", "-p")] = 8080,
    series: SeriesOpt = None,
) -> None:
    """[Phase 25] Serve the built wiki locally.

    Serves `dist/<series>/wiki-html/` — `mkdocs build`'s rendered output (`wiki site build --html`,
    or `mkdocs build -f dist/<series>/mkdocs.yml` by hand; requires the `wiki` extra,
    `pip install -e ".[wiki]"`). The raw Markdown at `dist/<series>/wiki/` is also readable
    directly from disk (or in any Markdown-aware editor/viewer) without building or serving
    anything — this command exists only for the rendered, browsable version.
    """
    import functools
    import http.server

    _settings(series)

    html_dir = paths.wiki_html_dir()
    if not html_dir.is_dir() or not any(html_dir.iterdir()):
        if paths.SITE_DIR.is_dir() and any(paths.SITE_DIR.iterdir()):
            console.print(
                f"[yellow]No rendered HTML found at {paths.relative(html_dir)}.[/yellow] The "
                f"Markdown wiki is already at [cyan]{paths.relative(paths.SITE_DIR)}[/cyan] and "
                f"readable directly from disk. To render+serve it: "
                f"[cyan]pip install -e \".\\[wiki]\"[/cyan] then "
                f"[cyan]mkdocs build -f {paths.relative(paths.wiki_mkdocs_yml())}[/cyan]."
            )
        else:
            console.print("[red]No built wiki found.[/red] Run [cyan]wiki site build[/cyan] first.")
        raise typer.Exit(code=1)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(html_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    console.print(
        f"Serving [cyan]{paths.relative(html_dir)}[/cyan] at "
        f"[bold]http://127.0.0.1:{port}[/bold] — Ctrl+C to stop."
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _audit_report(stage: str, settings, *, html_path: Path | None = None) -> bool | None:
    """Run one `audit/reports.py` stage report exactly as `wiki audit <stage>` always has, and —
    when `html_path` is given — also save a self-contained HTML copy of that same output.

    `rich.Console(record=True)` prints live to the terminal AND records what it printed in one
    pass, so the report function runs exactly once regardless of whether HTML is wanted — no
    parallel HTML-building code per report, no risk of the two diverging. Returns the report's
    own OK/FAIL verdict (`audit/reports.py`'s module docstring) so a caller can act on it.
    """
    from .audit import reports

    rec_console = Console(record=True, width=120)
    ok = reports.run(stage, settings, rec_console)
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        rec_console.save_html(str(html_path), inline_styles=True)
    return ok


def _write_run_index(run_id: str, settings) -> None:
    """`wiki audit run <run_id>` — one self-contained HTML index for a single run: its manifest,
    every `audit/*.html` report saved into it so far (via `wiki audit <stage>` or `wiki step`'s
    own end-of-chapter pass), and — for a `wiki step` run specifically — the character page(s) it
    touched, with their live URLs (persisted onto the manifest by `step()` via `run.finish(...,
    touched_pages=...)`)."""
    import html as html_mod

    ctx = provenance.get_run(run_id)
    m = ctx.manifest

    def esc(s) -> str:
        return html_mod.escape(str(s))

    report_names = sorted(p.name for p in ctx.audit_dir.glob("*.html")) if ctx.audit_dir.is_dir() else []
    report_links = "".join(f'<li><a href="{esc(name)}">{esc(name)}</a></li>' for name in report_names) or (
        '<li class="dim">none saved yet — run <code>wiki audit &lt;stage&gt;</code> to add one</li>'
    )

    touched_html = ""
    touched_pages = m.get("touched_pages")
    if touched_pages:
        rows = "".join(
            f'<li><code>{esc(eid)}</code> — '
            f'<a href="http://127.0.0.1:8080/#/character/{esc(eid)}">'
            f'http://127.0.0.1:8080/#/character/{esc(eid)}</a></li>'
            for eid in touched_pages
        )
        touched_html = f"<h2>Affected page(s)</h2>\n<ul>{rows}</ul>"

    doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Run {esc(run_id)} — {esc(settings.series_title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; max-width: 900px; }}
dt {{ font-weight: bold; margin-top: 0.5rem; }}
dd {{ margin: 0 0 0.3rem 0; }}
code {{ background: #f0f0f0; padding: 0 3px; }}
.dim {{ color: #999; }}
li {{ margin: 0.2rem 0; }}
</style></head>
<body>
<h1>Run {esc(run_id)}</h1>
<dl>
<dt>Command</dt><dd>{esc(m.get("command"))} (scope: {esc(m.get("scope"))})</dd>
<dt>Outcome</dt><dd>{esc(m.get("outcome"))}</dd>
<dt>Started / finished</dt><dd>{esc(m.get("started_ts"))} &rarr; {esc(m.get("finished_ts"))}</dd>
<dt>Volumes</dt><dd>{esc(m.get("volumes"))} (volume_scope={esc(m.get("volume_scope"))})</dd>
<dt>Git sha</dt><dd>{esc(m.get("git_sha"))}</dd>
<dt>Snapshotted stages</dt><dd>{esc(m.get("snapshotted_stages")) or "-"}</dd>
</dl>
{touched_html}
<h2>Audit reports</h2>
<ul>{report_links}</ul>
</body></html>
"""
    index_path = ctx.audit_dir / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(doc, encoding="utf-8")
    console.print(f"[green]Written:[/green] {paths.relative(index_path)}")


@app.command("audit")
def audit_cmd(
    stage: Annotated[str, typer.Argument(
            help="ingest | gazetteer | scenes | claims | contradictions | verify | events | "
            "outline | pages | eval | links | relationships | probe | run"
        )],
    run_id: Annotated[
        Optional[str],
        typer.Argument(help="Run id — only for `wiki audit run <run_id>`. See `wiki runs`."),
    ] = None,
    series: SeriesOpt = None,
    set_: SetOpt = None,
) -> None:
    """Print the report for a completed stage, and save a self-contained HTML copy of it into
    the most recent run's `_runs/<run_id>/audit/<stage>.html` (if any run has happened yet).

    `wiki audit run <run_id>` is the other form: prints an index of everything recorded for one
    specific run instead of a stage — its manifest, every audit HTML report saved into it, and
    (for a `wiki step` run) the character page(s) it touched.
    """
    settings = _settings(series, set_=set_)
    from .audit import reports

    if stage.lower().strip() == "run":
        if not run_id:
            console.print("[red]wiki audit run requires a run id.[/red] See [cyan]wiki runs[/cyan].")
            raise typer.Exit(code=1)
        try:
            _write_run_index(run_id, settings)
        except provenance.ProvenanceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        return

    target_run_id = provenance.latest_run_id()
    html_path = (
        provenance.get_run(target_run_id).audit_dir / f"{stage.lower().strip()}.html"
        if target_run_id
        else None
    )

    try:
        ok = _audit_report(stage, settings, html_path=html_path)
    except reports.UnknownStage as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except reports.StageNotReady as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=2) from exc

    if html_path is not None:
        console.print(f"\n[dim]HTML copy: {paths.relative(html_path)}[/dim]")
    if ok is False:
        raise typer.Exit(code=1)


class StepBlocked(RuntimeError):
    """`wiki step` has existing progress but neither --continue nor --redo was given."""


def _chapter_count(vol: int) -> int:
    """How many chapters data/01_parsed/v{vol:02d}.jsonl currently has, or 0 if not ingested yet.
    Safe to rely on contiguous 0..n-1 numbering: `ingest/epub.py` assigns `chapter_idx=len(chapters)`
    at append time, so chapter indices are always sequential from 0 with no gaps."""
    import json

    path = paths.parsed_volume(vol)
    if not path.is_file():
        return 0
    max_idx = -1
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                max_idx = max(max_idx, json.loads(line)["chapter_idx"])
    return max_idx + 1


def _resolve_step_target(
    settings, progress: dict[str, int] | None, *, do_continue: bool, redo: bool
) -> tuple[int, int] | None:
    """The next (vol, chapter_idx) `wiki step` should process, or None if the whole series is
    already done. Raises StepBlocked if progress exists and neither --continue nor --redo was
    given -- advancing always requires an explicit, deliberate flag (the "pause for manual
    audit" rule). Pure function of (settings, progress, flags) plus what's on disk in
    data/01_parsed/ -- no LLM/network involved, so this is unit-testable on its own."""
    all_volumes = sorted(v.vol for v in settings.discover_volumes())
    if not all_volumes:
        return None

    if progress is None:
        return all_volumes[0], 0

    if redo:
        return progress["vol"], progress["chapter_idx"]

    if not do_continue:
        raise StepBlocked(
            f"already at v{progress['vol']:02d} chapter {progress['chapter_idx']} — pass "
            f"--continue to advance or --redo to repeat it"
        )

    vol, chapter_idx = progress["vol"], progress["chapter_idx"]
    if chapter_idx + 1 < _chapter_count(vol):
        return vol, chapter_idx + 1

    pos = all_volumes.index(vol)
    if pos + 1 < len(all_volumes):
        return all_volumes[pos + 1], 0
    return None


@app.command()
def step(
    series: SeriesOpt = None,
    do_continue: Annotated[bool, typer.Option("--continue")] = False,
    redo: Annotated[bool, typer.Option("--redo")] = False,
    model: ModelOpt = None,
    set_: SetOpt = None,
) -> None:
    """The default, audited path: process ONE chapter, then stop.

    Bare `wiki step` does the very first chapter. `--continue` advances one chapter (or into
    the next volume once the current one is finished). `--redo` repeats the last completed
    chapter. Each call runs extract -> graph build -> synthesize -> site build for that one
    chapter, prints the affected character page(s), and pauses -- advancing again always
    requires `--continue` or `--redo` explicitly, so nothing proceeds without you looking first.
    See USER_GUIDE.md.
    """
    import json

    settings = _settings(series, model, set_)
    progress = provenance.load_step_progress()
    try:
        target = _resolve_step_target(settings, progress, do_continue=do_continue, redo=redo)
    except StepBlocked as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1) from exc

    if target is None:
        console.print("[green]Series fully processed[/green] — no chapters left to step through.")
        return

    vol, chapter_idx = target
    console.print(f"[bold]wiki step[/bold] — v{vol:02d} chapter {chapter_idx}\n")

    # This run wraps the whole chapter increment. It never snapshots a stage dir itself (each
    # delegated command below already snapshots its own before writing to it, so `wiki rollback
    # <this run's id>` would have nothing new to restore) -- its purpose is to be the one place
    # `wiki audit run <id>` can point at: the HTML claims/contradictions reports this step
    # produces (below) and, on success, the affected page list (Phase 12).
    step_run = provenance.start_run(
        settings.series_id, "step",
        scope=f"v{vol:02d}c{chapter_idx}", volumes=[vol], volume_scope=vol, settings=settings,
    )

    try:
        ingest(series=series, volumes=str(vol), force=False)
        scenes(series=series, volumes=str(vol), chapters=str(chapter_idx), force=redo, model=model, set_=set_)
        gazetteer(series=series, volumes=str(vol), model=model, set_=set_, merge_epithets=True)
        extract(series=series, volumes=str(vol), chapters=str(chapter_idx), force=redo, model=model, set_=set_)
        graph_build(series=series, model=model, set_=set_)
        events_build(series=series, force=redo, set_=set_)
        synthesize(upto=vol, series=series, force=redo, model=model, set_=set_)
        site_build(series=series, upto=vol, force=redo, model=model, set_=set_)
    except typer.Exit as exc:
        step_run.finish("failed", error=f"a delegated stage exited with code {exc.exit_code}")
        raise

    touched: set[str] = set()
    claims_path = paths.claims_volume(vol)
    if claims_path.is_file():
        for line in claims_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            claim = json.loads(line)
            if claim.get("chapter_idx") == chapter_idx:
                touched.add(claim["subject"])

    if touched:
        from .entities.gazetteer import load as load_gazetteer

        names = {e["entity_id"]: e["canonical"] for e in load_gazetteer()["entities"]}
        console.print("\n[bold]Affected page(s):[/bold]")
        for entity_id in sorted(touched):
            label = names.get(entity_id, entity_id)
            console.print(f"  {label} -> http://127.0.0.1:8080/#/character/{entity_id}")
        console.print("  ([cyan]wiki serve[/cyan] if it isn't running)")
    else:
        console.print("\n[dim]No claims for this chapter — nothing new on any page.[/dim]")

    for stage in ("claims", "contradictions"):
        try:
            _audit_report(stage, settings, html_path=step_run.audit_dir / f"{stage}.html")
        except Exception as exc:  # noqa: BLE001 - best-effort; never blocks the step
            console.print(f"[dim]({stage} audit skipped: {exc})[/dim]")

    step_run.finish("ok", touched_pages=sorted(touched), vol=vol, chapter_idx=chapter_idx)
    provenance.save_step_progress(vol, chapter_idx)
    console.print(
        f"\n[green]Done.[/green] v{vol:02d} chapter {chapter_idx} saved as the current step "
        f"position. HTML audit index: [cyan]wiki audit run {step_run.run_id}[/cyan]. Run "
        f"[cyan]wiki step --continue[/cyan] when you're ready for the next one."
    )


@app.command("run-all")
def run_all(
    series: SeriesOpt = None,
    volumes: VolumesOpt = None,
    upto: UptoOpt = None,
    polish: Annotated[bool, typer.Option("--polish")] = False,
    model: ModelOpt = None,
    set_: SetOpt = None,
    i_accept_unaudited: Annotated[
        bool,
        typer.Option(
            "--i-accept-unaudited",
            help="Required to run more than one volume unattended. Prefer `wiki step` — it "
            "processes one chapter at a time with a pause for manual audit between each.",
        ),
    ] = False,
) -> None:
    """Run every stage in order, stopping at the first failure.

    Right for an unattended full run of ONE volume. Prefer `wiki step` for the normal,
    audited path — it processes one chapter at a time and pauses between each. A multi-volume
    scope here is refused unless `--i-accept-unaudited` is given explicitly.
    """
    from .config import parse_volume_range

    settings = _settings(series, model, set_)
    upto = _resolve_upto(settings, upto)
    all_volumes = sorted(v.vol for v in settings.discover_volumes())
    try:
        wanted = parse_volume_range(volumes, all_volumes)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if len(wanted) > 1 and not i_accept_unaudited:
        console.print(
            f"[red]run-all was asked to process {len(wanted)} volumes unattended.[/red] Prefer "
            f"[cyan]wiki step[/cyan] — it processes one chapter at a time with a pause for manual "
            f"audit between each. Pass [cyan]--i-accept-unaudited[/cyan] to run this multi-volume "
            f"scope anyway."
        )
        raise typer.Exit(code=1)

    ingest(series=series, volumes=volumes, force=False)
    gazetteer(series=series, volumes=volumes, force=False, model=model, set_=set_)
    scenes(series=series, volumes=volumes, force=False, model=model, set_=set_)
    gazetteer(series=series, volumes=volumes, model=model, set_=set_, merge_epithets=True)
    extract(series=series, volumes=volumes, force=False, model=model, set_=set_)
    graph_build(series=series, model=model, set_=set_)  # Phase 23 B5: now verifies internally, at upto=1
    events_build(series=series, force=False, set_=set_)
    synthesize(upto=upto, series=series, force=False, polish=polish, model=model, set_=set_)
    site_build(series=series, upto=upto, force=False, model=model, set_=set_)


@app.command()
def trace(
    name: Annotated[str, typer.Argument(help="Character name or entity id.")],
    series: SeriesOpt = None,
    calls_limit: Annotated[
        int, typer.Option("--calls-limit", help="Max LLM calls to list per run before summarizing.")
    ] = 5,
) -> None:
    """[Phase 12] End-to-end provenance for one character, by a non-coder, in one command.

    Complements `wiki explain <name> --upto N`, which shows the ASSEMBLED, spoiler-scoped graph
    state at one cutoff. `wiki trace` shows the full RAW history instead: every claim ever
    extracted about this entity regardless of cutoff (with its evidence quote and source
    paragraph), every page file built from that evidence, whether it has a live site URL, and —
    across every run this series has ever done, not just the latest — every LLM call that
    mentioned them (the cross-run form of `wiki calls --grep <name>`).
    """
    import json

    from .entities.gazetteer import load as load_gazetteer

    settings = _settings(series)

    if not paths.gazetteer().is_file():
        console.print("[red]No gazetteer found.[/red] Run [cyan]wiki gazetteer[/cyan] first.")
        raise typer.Exit(code=1)
    gaz = load_gazetteer()
    entity = _find_character(gaz, name)
    entity_id = entity["entity_id"]

    console.print(
        Panel(
            f"[bold]{entity['canonical']}[/bold]  ({entity_id})\n"
            f"type={entity['type']}  first_vol={entity['first_vol']}  "
            f"aliases: {', '.join(entity.get('aliases', [])) or '-'}",
            border_style="blue",
        )
    )

    # --- every raw claim, across every volume, regardless of spoiler cutoff -------------------
    claim_files = sorted(paths.CLAIMS_DIR.glob("v*.jsonl")) if paths.CLAIMS_DIR.is_dir() else []
    claims: list[dict] = []
    for f in claim_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            claim = json.loads(line)
            if claim["subject"] == entity_id or claim.get("object") == entity_id:
                claims.append(claim)

    if claims:
        table = Table(title=f"Raw claims ({len(claims)})", header_style="bold", show_lines=True)
        for col in ("Vol", "Ch", "Predicate", "Value/object", "Polarity", "Conf", "Evidence"):
            table.add_column(col)
        for c in sorted(claims, key=lambda c: (c["first_vol"], c.get("chapter_idx") or 0)):
            value = c.get("value") if c.get("value") is not None else c.get("object", "-")
            evidence = "\n".join(
                f"[dim]{ev['para_id']}:[/dim] \"{ev['quote'][:120]}\"" for ev in c.get("evidence", [])
            )
            table.add_row(
                f"v{c['first_vol']:02d}",
                str(c.get("chapter_idx", "-")),
                c["predicate"],
                str(value),
                c.get("polarity", "-"),
                f"{c.get('confidence', 0):.2f}",
                evidence or "-",
            )
        console.print(table)
    else:
        console.print("[dim]No raw claims found for this entity yet — run wiki extract.[/dim]")

    # --- page files actually on disk -----------------------------------------------------------
    page_dir = paths.page_dir(entity_id)
    page_files = sorted(page_dir.glob("v*.json")) if page_dir.is_dir() else []
    if page_files:
        console.print("\n[bold]Page files:[/bold] " + ", ".join(paths.relative(p) for p in page_files))
    else:
        console.print("\n[dim]No page files written yet — run wiki synthesize.[/dim]")

    # --- live site presence ---------------------------------------------------------------------
    links_path = paths.bundle_links()
    if links_path.is_file():
        links = json.loads(links_path.read_text(encoding="utf-8"))
        if entity_id in links:
            console.print(f"[bold]Live URL:[/bold] http://127.0.0.1:8080/#{links[entity_id]['route']}")
        else:
            console.print("[dim]Not in the current site bundle yet — run wiki site build.[/dim]")

    # --- every LLM call, in every run, that mentions this entity's canonical name -------------
    hits = provenance.search_calls_all_runs(entity["canonical"])
    if hits:
        by_run: dict[str, list[dict]] = {}
        for run_id, rec in hits:
            by_run.setdefault(run_id, []).append(rec)
        console.print(
            f"\n[bold]LLM calls mentioning \"{entity['canonical']}\"[/bold] "
            f"({len(hits)} across {len(by_run)} run(s)):"
        )
        for run_id, recs in by_run.items():
            first = recs[0]
            console.print(
                f"  [cyan]{run_id}[/cyan] — {len(recs)} call(s), e.g. {first.get('ts', '?')} "
                f"[{first.get('stage', '?')}]"
            )
            if len(recs) > calls_limit:
                console.print(
                    f"    [dim]...see wiki calls --run {run_id} --grep "
                    f"\"{entity['canonical']}\" for all {len(recs)}[/dim]"
                )
    else:
        console.print("\n[dim]No LLM calls found mentioning this entity in any recorded run.[/dim]")


if __name__ == "__main__":  # pragma: no cover
    app()
