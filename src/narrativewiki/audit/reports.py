"""Per-stage audit reports backing `wiki audit <stage>`.

Inputs:     A stage name, Settings, and a rich Console.
Outputs:    Printed report. Some reports also write a file (the gazetteer roster HTML).
Invariants: - Every phase registers exactly one report here. A phase is not finished until
              its deliverable can be inspected without reading raw JSON by hand.
            - Reports never mutate stage output. They read and judge, nothing more.
            - A report FAILS loudly on the specific things known to go wrong silently — a
              volume with zero Para-RAID paragraphs, a quote that is not in its cited
              paragraph, a link that resolves nowhere.
            - Every report function returns `True` (OK) or `False` (FAIL) — Phase 12's
              `wiki audit <stage>` uses this to exit non-zero on a real FAIL instead of always
              exiting 0 regardless of what was printed.
Contract:   docs/CONTRACTS.md defines the files each report reads.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console

from ..config import Settings

# stage name -> (report function, the command that produces its input)
_REGISTRY: dict[str, tuple[Callable[[Settings, Console], bool], str]] = {}

# Stages that have a defined report but whose phase is not built yet. Moving an entry from
# here into _REGISTRY is part of completing that phase.
_PLANNED: dict[str, tuple[int, str]] = {}


class UnknownStage(ValueError):
    """The requested stage is not an audit target at all."""


class StageNotReady(RuntimeError):
    """A real stage whose phase is not implemented, or whose input has not been produced."""


def register(stage: str, produced_by: str):
    """Decorator used by each phase to add its report."""

    def wrapper(func: Callable[[Settings, Console], bool]):
        _REGISTRY[stage] = (func, produced_by)
        return func

    return wrapper


def run(stage: str, settings: Settings, console: Console) -> bool:
    """Run one stage's report. Returns the report's own OK/FAIL verdict (see module docstring)."""
    stage = stage.lower().strip()
    if stage in _REGISTRY:
        return _REGISTRY[stage][0](settings, console)

    if stage in _PLANNED:
        phase, description = _PLANNED[stage]
        raise StageNotReady(
            f"The '{stage}' report arrives in Phase {phase}: {description}.\n"
            f"See LOG.md for the current phase."
        )

    known = sorted(set(_REGISTRY) | set(_PLANNED))
    raise UnknownStage(f"Unknown audit stage {stage!r}. Known stages: {', '.join(known)}")


def available() -> list[str]:
    """Stages with a working report right now."""
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Phase 1 — ingest
# ---------------------------------------------------------------------------


@register("ingest", "wiki ingest")
def _ingest_report(settings: Settings, console: Console) -> bool:
    import json
    from collections import Counter

    from rich.table import Table

    from .. import paths

    manifest_path = paths.parsed_manifest()
    if not manifest_path.is_file():
        raise StageNotReady(
            f"No {paths.relative(manifest_path)} yet. Run: wiki ingest --volumes 1-13"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    volumes = sorted(manifest.get("volumes", []), key=lambda e: e["vol"])
    if not volumes:
        raise StageNotReady("Manifest has no volumes recorded. Run: wiki ingest --volumes 1-13")

    table = Table(title="Ingest report — per volume", header_style="bold")
    # "Channel": speech in a series-declared special channel (86's Para-RAID radio markup).
    for col in ("Vol", "Chapters", "Paragraphs", "Words", "Channel", "Machine", "Dialogue"):
        table.add_column(col, justify="right" if col != "Vol" else "left")

    zero_pararaid: list[int] = []
    collapsed: list[int] = []
    require_para_raid = bool((settings.series.get("speech") or {}).get("require_para_raid", False))
    total_words = 0
    total_paras = 0
    kind_totals: Counter[str] = Counter()

    for entry in volumes:
        vol = entry["vol"]
        jsonl_path = paths.parsed_volume(vol)
        if not jsonl_path.is_file():
            table.add_row(f"v{vol:02d}", "[red]missing file[/red]", "", "", "", "", "")
            continue

        speech: Counter[str] = Counter()
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            speech[record["speech"]] += 1
            kind_totals[record["chapter_kind"]] += 1

        total_words += entry["n_words"]
        total_paras += entry["n_paragraphs"]
        pararaid = speech.get("para_raid", 0)
        if pararaid == 0:
            zero_pararaid.append(vol)
        # [30] A volume of novel length read as one or two chapters is the signature of a heading
        # tag that matched nothing (`epub.heading_tags`, ingest/epub.py::_detect_heading_tags).
        if entry["n_chapters"] <= 2 and entry["n_words"] > 20_000:
            collapsed.append(vol)

        table.add_row(
            f"v{vol:02d}",
            str(entry["n_chapters"]),
            str(entry["n_paragraphs"]),
            f"{entry['n_words']:,}",
            str(pararaid) if pararaid or not require_para_raid else "[red]0[/red]",
            str(speech.get("machine", 0)),
            str(speech.get("dialogue", 0)),
        )

    console.print(table)
    expected_words = settings.series.get("series", {}).get("expected_words")
    expected_note = f" (~{expected_words:,} expected in the reference corpus)" if expected_words else ""
    console.print(
        f"\nTotal: {len(volumes)} volume(s), {total_paras:,} paragraphs, "
        f"{total_words:,} words{expected_note}."
    )
    console.print(f"Chapter kinds across corpus: {dict(sorted(kind_totals.items()))}")
    # [31] Two Anne books parsed to 0 paragraphs and this audit still said OK.
    empty = [e["vol"] for e in volumes if not e.get("n_paragraphs")]
    if empty:
        console.print(
            f"\n[red]FAIL[/red] — volume(s) {empty} parsed to 0 paragraphs. The EPUB's chapters "
            "were probably not found; see `epub.heading_tags` and `epub.split_within_documents`."
        )
        return False
    if collapsed:
        console.print(
            f"\n[yellow]WARN[/yellow] — volume(s) {collapsed} parsed as <= 2 chapters over 20,000 "
            "words: the chapter headings were probably not recognised. Set `epub.heading_tags` "
            "(or `epub.chapter_heading_selectors`) in config/series.<id>.yaml and re-ingest."
        )

    # Not every series has a radio/telepathic-comms channel at all (Spice and Wolf: ordinary
    # prose dialogue only) -- `speech.require_para_raid` (series.86.yaml) opts a series INTO this
    # check rather than the check assuming every series has such a channel. Absent/false is not
    # "not checked yet", it is "this series genuinely has none, by design" -- see CONTRACTS §1.1.
    if zero_pararaid and require_para_raid:
        console.print(
            f"\n[red]FAIL[/red] — zero Para-RAID paragraphs in volume(s) {zero_pararaid}. "
            f"The class regex in config/series.<id>.yaml `speech.para_raid_class_pattern` "
            f"likely regressed for these volumes."
        )
        return False
    elif require_para_raid:
        console.print("\n[green]OK[/green] — every volume has at least one Para-RAID paragraph.")
        return True
    else:
        console.print(
            "\n[green]OK[/green] — this series declares no special speech channel "
            "(`speech.require_para_raid`), so none is required."
        )
        return True


# ---------------------------------------------------------------------------
# Phase 2 — gazetteer
# ---------------------------------------------------------------------------


@register("gazetteer", "wiki gazetteer")
def _gazetteer_report(settings: Settings, console: Console) -> bool:
    import html
    import json
    from collections import Counter

    from rich.table import Table

    from .. import paths

    gaz_path = paths.gazetteer()
    if not gaz_path.is_file():
        raise StageNotReady(f"No {paths.relative(gaz_path)} yet. Run: wiki gazetteer --volumes 1-13")

    gaz = json.loads(gaz_path.read_text(encoding="utf-8"))
    entities = gaz.get("entities", [])
    if not entities:
        raise StageNotReady("gazetteer.json has no entities. Run: wiki gazetteer --volumes 1-13")

    by_type: Counter[str] = Counter(e["type"] for e in entities)
    low_confidence = [e for e in entities if e["confidence"] < 0.7]
    ambiguous = [e for e in entities if any(sf["ambiguous"] for sf in e["surface_forms"])]

    mentions_path = paths.mentions()
    n_mentions = 0
    if mentions_path.is_file():
        with mentions_path.open(encoding="utf-8") as fh:
            n_mentions = sum(1 for line in fh if line.strip())

    table = Table(title="Gazetteer report — by type", header_style="bold")
    table.add_column("Type")
    table.add_column("Count", justify="right")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        table.add_row(t, str(n))
    console.print(table)

    console.print(
        f"\n{len(entities)} entities, {n_mentions:,} mentions indexed "
        f"({paths.relative(mentions_path)}), volumes covered: {gaz.get('volumes_covered')}."
    )

    # [24] Build-time provenance (docs/vision/plans/0008-pre-full-scale-audit.md B1/B3) -- absent on
    # a gazetteer built before this field existed, not an error.
    prov = gaz.get("provenance")
    if prov:
        console.print(
            f"Built with min_mentions={prov.get('min_mentions')}, "
            f"max_candidates_per_volume={prov.get('max_candidates_per_volume')}, "
            f"max_llm_alias_pairs={prov.get('max_llm_alias_pairs')}."
        )
        dropped_pairs = prov.get("alias_pairs_dropped") or 0
        if dropped_pairs:
            console.print(
                f"[yellow]{dropped_pairs}/{prov.get('alias_pairs_total')} alias candidate pair(s) "
                f"were past the cap and never sent to the model[/yellow] -- consider raising "
                f"`entities.max_llm_alias_pairs` in config/series.<id>.yaml."
            )
    else:
        console.print(
            "[dim]No build provenance recorded on this gazetteer (built before this field existed "
            "-- rebuild with --force to get it).[/dim]"
        )

    # Ambiguous-surface collisions: cheaply re-derivable from the entities already loaded, so not
    # persisted in gazetteer.json itself -- see entities/automaton.py::build_automaton.
    from ..entities import automaton as automaton_mod

    automaton_mod.write_surface_inventory(entities)
    console.print(f"Surface vocabulary exported to {paths.relative(paths.surface_forms())}.")
    dropped_surfaces = automaton_mod.build_automaton(entities).dropped_ambiguous_surfaces
    if dropped_surfaces:
        example = dropped_surfaces[0]
        console.print(
            f"[yellow]{len(dropped_surfaces)} ambiguous surface(s) dropped[/yellow] by the mention "
            f"index (claimed by more than one entity; only the alphabetically-first entity_id keeps "
            f"it) -- e.g. {example['surface']!r}: kept by {example['kept_entity_id']!r}, "
            f"dropped from {example['dropped_entity_id']!r}."
        )

    # An entity that loses EVERY surface form to a collision indexes nothing, so it can never
    # gather a mention, a claim or a page -- it survives only as a phantom in the roster, with a
    # stale `mention_count` that now belongs to whoever won the tie-break. Found on v1-2 by
    # `scripts/eval/character_evidence_coverage.py`: `wisewolf` is Holo, mined as its own candidate
    # and then subsumed when epithet merging gave Holo the same literal. Harmless there only
    # because "holo" sorts first; the same collision with the ids reversed silently moves a real
    # character's whole evidence base onto the phantom. Reported, never auto-deleted: which of the
    # two is spurious is a judgement about the corpus, not something this pass can decide.
    starved = sorted(
        {r["entity_id"] for r in automaton_mod.surface_inventory(entities)}
        - {r["entity_id"] for r in automaton_mod.surface_inventory(entities) if r["indexed"]}
    )
    if starved:
        console.print(
            f"[yellow]{len(starved)} entit(y/ies) index no surface form at all[/yellow] -- "
            f"unreachable by the mention index, so they can never gain evidence: "
            f"{', '.join(repr(e) for e in starved)}."
        )

    if low_confidence:
        console.print(f"[yellow]{len(low_confidence)} entities below 0.7 confidence[/yellow] — review these first.")
    if ambiguous:
        console.print(f"[yellow]{len(ambiguous)} entities have an ambiguous surface form[/yellow] shared with another entity.")

    ok = by_type.get("CHARACTER", 0) > 0
    if not ok:
        console.print(
            "\n[red]FAIL[/red] — zero CHARACTER entities. The wiki has nothing to build pages "
            "for; check the ingest output (`wiki audit ingest`), or "
            "whether the classify stage is reachable (`wiki doctor`)."
        )
    else:
        console.print("\n[green]OK[/green] — CHARACTER entities present.")

    # [30] A page is titled by its canonical name at EVERY cutoff, so a canonical name the text
    # reveals later than the entity first appears is a spoiler in titles, links and the roster.
    # Overlord: the "Wise King of the Forest" is a v1 legend; "Hamusuke" is the name Ainz gives
    # her in v2. `alias._build_clusters` names a cluster only from its first volume's surfaces [32].
    late_names = [
        (e["entity_id"], e["canonical"], first, e["first_vol"])
        for e in entities
        if (first := next((sf.get("first_vol") for sf in e.get("surface_forms", [])
                           if sf["text"] == e["canonical"]), None)) is not None
        and first > e["first_vol"]
    ]
    if late_names:
        ok = False
        console.print(f"\n[red]FAIL[/red] — {len(late_names)} canonical name(s) revealed after the entity appears:")
        for entity_id, canonical, first, entity_first in late_names:
            console.print(f"  {entity_id}: {canonical!r} first in v{first}, entity from v{entity_first}")

    # --- roster.html: the actual hand-audit artifact -----------------------
    rows = sorted(entities, key=lambda e: -e["mention_count"])

    def esc(s) -> str:
        return html.escape(str(s))

    row_html = []
    for e in rows:
        flag = ""
        if e["confidence"] < 0.7:
            flag += ' <span class="flag">low-confidence</span>'
        if any(sf["ambiguous"] for sf in e["surface_forms"]):
            flag += ' <span class="flag">ambiguous</span>'
        aliases = ", ".join(esc(a) for a in e["aliases"]) or "<span class=\"dim\">—</span>"
        row_html.append(
            "<tr>"
            f"<td><code>{esc(e['entity_id'])}</code></td>"
            f"<td>{esc(e['canonical'])}{flag}</td>"
            f"<td>{esc(e['type'])}</td>"
            f"<td>{aliases}</td>"
            f"<td>{e['first_vol']}</td>"
            f"<td>{e['mention_count']:,}</td>"
            f"<td>{e['importance']:.2f}</td>"
            f"<td>{e['confidence']:.2f}</td>"
            f"<td>{esc(e.get('notes', ''))}</td>"
            "</tr>"
        )

    html_doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Entity roster — {esc(settings.series_title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #ddd; }}
th {{ position: sticky; top: 0; background: #fff; cursor: pointer; }}
tr:hover {{ background: #f6f6f6; }}
.flag {{ color: #b00; font-size: 0.75rem; border: 1px solid #b00; border-radius: 3px; padding: 0 4px; }}
.dim {{ color: #999; }}
h1 {{ margin-bottom: 0.2rem; }}
.sub {{ color: #666; margin-top: 0; }}
</style></head>
<body>
<h1>Entity roster — {esc(settings.series_title)}</h1>
<p class="sub">{len(entities)} entities · {n_mentions:,} mentions · volumes {esc(gaz.get('volumes_covered'))} ·
built {esc(gaz.get('built_at'))}. Sorted by mention count. A wrong entry is fixed in the general
pipeline, never per series; record the right answer in <code>docs/eval/roster/{esc(settings.series_id)}.yaml</code>.</p>
<table id="roster">
<thead><tr>
<th>entity_id</th><th>canonical</th><th>type</th><th>aliases</th><th>first_vol</th>
<th>mentions</th><th>importance</th><th>confidence</th><th>notes</th>
</tr></thead>
<tbody>
{''.join(row_html)}
</tbody>
</table>
<script>
document.querySelectorAll("th").forEach((th, i) => {{
  th.addEventListener("click", () => {{
    const tbody = document.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));
    const numeric = i >= 4;
    rows.sort((a, b) => {{
      const av = a.children[i].innerText, bv = b.children[i].innerText;
      return numeric ? parseFloat(bv) - parseFloat(av) : av.localeCompare(bv);
    }});
    rows.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body></html>
"""
    roster_path = paths.roster_html()
    roster_path.write_text(html_doc, encoding="utf-8")
    console.print(f"\nRoster written to [cyan]{paths.relative(roster_path)}[/cyan] — open it and check the top ~50.")
    return ok


# ---------------------------------------------------------------------------
# Phase 18 — scenes (chapter-major second pass + epithet mining)
# ---------------------------------------------------------------------------


@register("scenes", "wiki scenes")
def _scenes_report(settings: Settings, console: Console) -> bool:
    import json

    from rich.table import Table

    from .. import paths, provenance

    scene_files = sorted(paths.SCENES_DIR.glob("v*.jsonl")) if paths.SCENES_DIR.is_dir() else []
    if not scene_files:
        raise StageNotReady(f"No scene records found in {paths.relative(paths.SCENES_DIR)}. Run: wiki scenes --volumes 1-13")

    gaz_path = paths.gazetteer()
    canonical_by_id: dict[str, str] = {}
    if gaz_path.is_file():
        gaz = json.loads(gaz_path.read_text(encoding="utf-8"))
        canonical_by_id = {e["entity_id"]: e["canonical"] for e in gaz.get("entities", [])}

    total_scenes = 0
    total_epithets = 0
    scenes_with_structure = 0  # Phase 22 A6 (S7): location and/or state_changes and/or quotes
    bad_quotes: list[tuple[str, str, str]] = []  # (scene_id, vol, para_id)
    coverage_gaps: list[tuple[int, int]] = []  # (vol, chapter_idx)
    epithet_rows: list[tuple[str, str, float, str]] = []  # (entity, text, confidence, quote)

    for path in scene_files:
        vol = int(path.stem[1:])  # "v03" -> 3
        scenes = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        total_scenes += len(scenes)

        para_path = paths.parsed_volume(vol)
        paragraphs_by_id: dict[str, str] = {}
        all_para_ids_by_chapter: dict[int, set[str]] = {}
        if para_path.is_file():
            for pline in para_path.read_text(encoding="utf-8").splitlines():
                if pline.strip():
                    r = json.loads(pline)
                    paragraphs_by_id[r["para_id"]] = r["text"]
                    all_para_ids_by_chapter.setdefault(r["chapter_idx"], set()).add(r["para_id"])

        covered_by_chapter: dict[int, set[str]] = {}
        for scene in scenes:
            covered_by_chapter.setdefault(scene["chapter_idx"], set()).update(scene["core_para_ids"])
            if scene.get("location") or scene["state_changes"] or scene["quotes"]:
                scenes_with_structure += 1
            for q in scene["quotes"]:
                text = paragraphs_by_id.get(q["para_id"])
                if text is None or q["quote"] not in text:
                    bad_quotes.append((scene["scene_id"], f"v{vol:02d}", q["para_id"]))
            for sc in scene["state_changes"]:
                for ev in sc["evidence"]:
                    text = paragraphs_by_id.get(ev["para_id"])
                    if text is None or ev["quote"] not in text:
                        bad_quotes.append((scene["scene_id"], f"v{vol:02d}", ev["para_id"]))

        # Every chapter that has AT LEAST ONE scene record must have full coverage -- a chapter
        # with none simply hasn't been processed yet (not audited here, `wiki status` covers
        # staleness). This is the literal "100% coverage by construction" guarantee Phase 18
        # exists to provide.
        for chapter_idx, covered in covered_by_chapter.items():
            expected = all_para_ids_by_chapter.get(chapter_idx, set())
            if expected and covered != expected:
                coverage_gaps.append((vol, chapter_idx))

        ep_path = paths.scene_epithets(vol)
        if ep_path.is_file():
            for line in ep_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                total_epithets += 1
                epithet_rows.append(
                    (
                        canonical_by_id.get(rec["entity_id"], rec["entity_id"]),
                        rec["text"],
                        rec["confidence"],
                        rec["quote"],
                    )
                )

    console.print(
        f"{total_scenes:,} scene record(s) across {len(scene_files)} volume(s); "
        f"{total_epithets:,} mined epithet mention(s)."
    )
    if total_scenes:
        # Phase 22 A6 (S7): the audit's "56 scenes produced 0 state changes and 0 locations"
        # finding as a printed yield number rather than a one-off hand count.
        console.print(
            f"Scenes with >=1 structured field (location/state_changes/quotes): "
            f"{scenes_with_structure}/{total_scenes} "
            f"({100 * scenes_with_structure / total_scenes:.0f}%)."
        )

    if epithet_rows:
        table = Table(title="Mined epithets — review before `wiki gazetteer --merge-epithets`", header_style="bold")
        table.add_column("Entity")
        table.add_column("Epithet")
        table.add_column("Confidence", justify="right")
        table.add_column("Evidence")
        for entity, text, confidence, quote in epithet_rows[:30]:
            table.add_row(entity, text, f"{confidence:.2f}", quote[:80])
        console.print(table)

    ok = True
    # Phase 22 A6: same closed hole as `_claims_report` -- this report must not vouch for a
    # crashed `wiki scenes` run's partial output either.
    failed = provenance.failed_stage_artifacts(["scenes"])
    if failed:
        ok = False
        console.print(
            f"\n[red]FAIL[/red] — the most recent run that wrote scenes "
            f"([red]{failed[0]['run_id']}[/red]) recorded outcome: failed "
            f"({failed[0].get('error', 'no error recorded')}). Roll back with "
            f"[cyan]wiki rollback {failed[0]['run_id']} --yes[/cyan] and re-run wiki scenes."
        )
    if coverage_gaps:
        ok = False
        shown = ", ".join(f"v{v:02d}c{c:02d}" for v, c in coverage_gaps[:10])
        console.print(
            f"\n[red]FAIL[/red] — {len(coverage_gaps)} chapter(s) do not have full paragraph "
            f"coverage across their scene spans (this should never happen — it is the whole "
            f"guarantee this phase exists to provide): {shown}"
        )
    if bad_quotes:
        ok = False
        console.print(
            f"\n[red]FAIL[/red] — {len(bad_quotes)} scene fact(s) cite a quote that does not "
            f"appear verbatim in its paragraph. First few: "
            + ", ".join(f"{sid} ({vol}, {pid})" for sid, vol, pid in bad_quotes[:5])
        )
    if ok:
        console.print(
            "\n[green]OK[/green] — every processed chapter has full paragraph coverage and "
            "every cited quote is verbatim."
        )
    return ok


# ---------------------------------------------------------------------------
# Phase 19 — events (event layer from scene records)
# ---------------------------------------------------------------------------


@register("events", "wiki events build")
def _events_report(settings: Settings, console: Console) -> bool:
    import json
    import sqlite3
    from collections import Counter

    from rich.table import Table

    from .. import paths

    db_path = paths.events_db()
    if not db_path.is_file():
        raise StageNotReady(
            f"No {paths.relative(db_path)} yet. Run: wiki events build --volumes 1-13"
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        n_claims = conn.execute("SELECT COUNT(*) FROM event_claims").fetchone()[0]
        vols_covered = [
            row[0] for row in conn.execute("SELECT DISTINCT vol FROM events ORDER BY vol").fetchall()
        ]
    except sqlite3.OperationalError as exc:
        conn.close()
        raise StageNotReady(
            f"events.db schema missing (is this a pre-Phase-19 file?): {exc}"
        ) from exc

    console.print(
        f"{n_events:,} event(s), {n_claims:,} event claim(s); "
        f"volumes covered: {vols_covered or '(none)'}."
    )

    if n_events == 0:
        conn.close()
        console.print("\n[yellow]No events yet — run: wiki events build --volumes 1-13[/yellow]")
        return True  # not a FAIL: db exists but is empty (e.g. first scene file was all-empty spans)

    # Kind distribution
    kind_rows = conn.execute(
        "SELECT kind, COUNT(*) as n FROM event_claims GROUP BY kind ORDER BY n DESC"
    ).fetchall()
    kind_counts: Counter[str] = Counter({r["kind"]: r["n"] for r in kind_rows})

    console.print(f"Event claim kinds: {dict(kind_counts)}")

    # Schema guard: every event_claim must have kind in the allowed set
    bad_kinds = [k for k in kind_counts if k not in ("state_change", "quote")]
    if bad_kinds:
        conn.close()
        console.print(
            f"\n[red]FAIL[/red] — {len(bad_kinds)} unknown event claim kind(s): "
            + ", ".join(repr(k) for k in bad_kinds)
        )
        return False

    # Top characters by event count
    gaz_path = paths.gazetteer()
    canonical_by_id: dict[str, str] = {}
    if gaz_path.is_file():
        gaz_data = json.loads(gaz_path.read_text(encoding="utf-8"))
        canonical_by_id = {e["entity_id"]: e["canonical"] for e in gaz_data.get("entities", [])}

    # participants_json is a JSON list; count appearances via a Python loop (small dataset)
    char_counter: Counter[str] = Counter()
    for row in conn.execute("SELECT participants_json FROM events").fetchall():
        try:
            ids: list[str] = json.loads(row["participants_json"])
        except (ValueError, TypeError):
            ids = []
        for eid in ids:
            char_counter[eid] += 1

    if char_counter:
        table = Table(title="Top characters by event count", header_style="bold")
        table.add_column("Character")
        table.add_column("Events", justify="right")
        for eid, count in char_counter.most_common(20):
            table.add_row(canonical_by_id.get(eid, eid), str(count))
        console.print(table)

    conn.close()
    console.print("\n[green]OK[/green] — events built and all claim kinds valid.")
    return True


# ---------------------------------------------------------------------------
# Phase 20 — outline (per-section page validation)
# ---------------------------------------------------------------------------


@register("outline", "wiki synthesize")
def _outline_report(settings: Settings, console: Console) -> bool:
    """Validate character page prose sections and quotes against the page_outline config.

    For each character page JSON visible in data/05_pages/:
      - For every `kind: prose` section that is NOT source: events: WARN if the section
        is None but the page has at least one evidence para_id (means evidence existed but
        prose was not generated — typically a cache issue or an unexpected None return).
      - For every `kind: quotes` section: if page["quotes"] is not None, confirm len <= max_quotes.

    FAIL: any quotes section exceeds max_quotes (config violation).
    WARN-only (not FAIL): a prose section is None with evidence (may indicate events.db absent
    for chronology, which is expected pre-events-build, so only warn).

    Returns True (OK) unless a hard config violation is found.
    """
    import json

    from .. import paths

    pages_dir = paths.PAGES_DIR
    if not pages_dir.is_dir():
        raise StageNotReady(
            f"No {paths.relative(pages_dir)} yet. Run: wiki synthesize --upto N"
        )

    page_files = sorted(pages_dir.glob("**/v*.json"))
    if not page_files:
        raise StageNotReady(
            f"No page JSON files in {paths.relative(pages_dir)} yet. Run: wiki synthesize --upto N"
        )

    prose_sections = [s for s in settings.page_outline if s["kind"] == "prose" and s.get("source") != "events"]
    quotes_sections = [s for s in settings.page_outline if s["kind"] == "quotes"]
    max_quotes_cfg = {s["key"]: int(s.get("max_quotes", 3)) for s in quotes_sections}

    warnings: list[str] = []
    failures: list[str] = []
    checked = 0

    for page_path in page_files:
        try:
            page = json.loads(page_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            warnings.append(f"{page_path.name}: could not parse JSON")
            continue
        checked += 1

        prose = page.get("prose", {})
        for sec in prose_sections:
            key = sec["key"]
            entry = prose.get(key)
            if entry is None:
                # Phase 22 A6: this branch used to compute a condition and then do nothing with
                # it regardless of the result -- a dead loop. A page where EVERY prose section is
                # None is a legitimate zero-evidence character (no warning); one where THIS
                # section alone is None while a sibling section has content is a real generation
                # gap (cache issue, an unexpected None return) worth a human looking at.
                siblings_with_content = [
                    s["key"] for s in prose_sections
                    if s["key"] != key and prose.get(s["key"]) is not None
                ]
                if siblings_with_content:
                    warnings.append(
                        f"{page_path.name}: {key} prose is None but {siblings_with_content} "
                        f"has content — possible generation gap, not a zero-evidence character"
                    )

        quotes = page.get("quotes")
        if quotes is not None:
            for qkey, max_n in max_quotes_cfg.items():
                n_quotes = len(quotes)
                if n_quotes > max_n:
                    failures.append(
                        f"{page_path.name}: quotes section has {n_quotes} items, "
                        f"max_quotes={max_n}"
                    )

    console.print(
        f"Checked {checked} page JSON file(s) against {len(prose_sections)} prose + "
        f"{len(quotes_sections)} quotes section(s) in page_outline."
    )

    if warnings:
        console.print(f"\n[yellow]{len(warnings)} warning(s):[/yellow]")
        for w in warnings[:10]:
            console.print(f"  {w}")

    if failures:
        console.print(f"\n[red]FAIL[/red] — {len(failures)} violation(s):")
        for f in failures[:10]:
            console.print(f"  {f}")
        return False

    console.print("\n[green]OK[/green] — page outline structure is valid.")
    return True


_TOP_UP_RECALL_THRESHOLD = 0.5
"""Phase 22 C2: below this fact-recall fraction, `_eval_report` suggests a targeted
`wiki extract --entities` top-up for that character instead of leaving the gap silent."""


@register("eval", "wiki synthesize")
def _eval_report(settings: Settings, console: Console) -> bool:
    """Phase 21 part 4 — the evaluation harness the vision doc calls for: heading recall, fact
    recall (both against hand-transcribed `docs/eval/gold/<series>/*.yaml`), and citation
    entailment (self-check, no gold data needed) for every gold-covered character.

    This is a REPORTING tool, not a spoiler-safety gate — CLAUDE.md §1's one hard invariant is
    covered elsewhere (`test_spoiler_leak.py`, `wiki audit pages`/`links`/`relationships`). Depth
    and hallucination-avoidance quality are a continuum a human reads and judges from this
    report's printed detail, not a boolean this function could correctly fail on. Returns False
    for a structural problem (no gold data at all, or a gold entity_id with no generated page
    on disk); a fact-recall REGRESSION against `docs/eval/gold/<series>/baseline.json` (Phase 22
    C4), when one is recorded for that character; a precision REGRESSION against that same
    file's sibling `precision` map (Phase 23 C4); or — unconditionally, no baseline needed —
    ANY gold `negative_fact` the page actually asserts (Phase 23 C1/C4): this is the ONE check in
    this report that does not wait for a recorded floor, because a negative fact is not "a lower
    score than before," it is a specific, named claim gold says the wiki must never make. Never
    for a merely low absolute recall/precision score with no baseline on record. The baseline is
    hand-maintained, never written by this report; a genuine improvement is locked in by raising
    it deliberately, the same way a gold fact itself is added or corrected by hand.
    """
    import sqlite3

    from .. import paths
    from ..eval import gold
    from ..graph import events as events_db
    from ..graph import store

    entries = gold.load_gold(settings.series_id)
    if not entries:
        raise StageNotReady(
            f"No gold data at docs/eval/gold/{settings.series_id}/*.yaml yet. "
            "Hand-transcribe a reference wiki page's outline + fact list first "
            "(see docs/eval/gold/spice-and-wolf/*.yaml for the format)."
        )

    section_keys = [s["key"] for s in settings.page_outline]
    events_source_keys = {s["key"] for s in settings.page_outline if s.get("source") == "events"}
    missing_pages: list[str] = []
    baseline = gold.load_baseline(settings.series_id)
    precision_baseline = gold.load_precision_baseline(settings.series_id)  # Phase 23 C4
    regressions: list[tuple[str, float, float]] = []  # (canonical, current, baseline)
    precision_regressions: list[tuple[str, float, float]] = []  # (canonical, current, baseline)
    negative_fact_violations: list[tuple[str, str]] = []  # (canonical, negative_fact text)

    events_path = paths.events_db()
    events_conn: sqlite3.Connection | None = events_db.connect(events_path) if events_path.is_file() else None
    # Phase 27: fact recall gains a LOCAL entailment pass (CLAUDE.md §2 -- the protected budget
    # is the OpenRouter quota, and MiniCheck-FT5 is unmetered). Hard-refuses any non-local
    # profile: an eval competing with extraction for the same 1000 requests/day would be back to
    # trading the product against its own yardstick, and `wiki audit eval` must keep working
    # with OPENROUTER_API_KEY unset. Unavailable model => lexical-only, reported, never fatal:
    # this is a reporting tool and a missing classifier must not block the audit.
    semantic_scorer = None
    try:
        from ..llm.client import LLMClient

        profile = settings.resolve_role("support")
        if not profile.is_local:
            console.print(f"  [dim]semantic recall off: '{profile}' is not a local profile[/dim]")
        else:
            _client = LLMClient(settings)
            semantic_scorer = lambda docs, claim: _client.score_support(docs, claim)  # noqa: E731
    except Exception as exc:  # noqa: BLE001 -- any classifier problem degrades, never blocks
        console.print(f"  [dim]semantic recall off ({type(exc).__name__}: {exc})[/dim]")

    graph_path = paths.graph_db()
    graph_conn: sqlite3.Connection | None = (
        store.connect(graph_path, read_only=True) if graph_path.is_file() else None
    )

    for entry in entries:
        page = gold.nearest_page_at_or_below(entry.entity_id, entry.eval_vol)
        console.print(f"\n[bold]{entry.canonical}[/bold] (eval_vol={entry.eval_vol}, {entry.source_url})")
        if page is None:
            missing_pages.append(entry.entity_id)
            console.print(f"  [red]No generated page found at or below volume {entry.eval_vol}.[/red] "
                          f"Run: wiki synthesize --series {settings.series_id} --upto {entry.eval_vol}")
            continue

        heading = gold.score_heading_recall(entry.outline, section_keys)
        fact = gold.score_fact_recall(entry.facts, page)
        entailment = gold.score_citation_entailment(
            page,
            events_conn=events_conn,
            entity_id=entry.entity_id,
            vol=page.get("upto_vol", entry.eval_vol),
            events_source_keys=events_source_keys,
        )

        h_recall = heading["recall"]
        f_recall = fact["recall"]
        console.print(
            f"  heading recall: {h_recall:.0%}" if h_recall is not None else "  heading recall: n/a (no gold outline)"
        )
        for h in heading["headings"]:
            if not h["matched"]:
                console.print(f"    [dim]no equivalent section for gold heading {h['heading']!r}[/dim]")

        if semantic_scorer is not None:
            gold.augment_recall_semantically(fact, page, semantic_scorer)
            f_recall = fact["recall"]
        upgrades = fact.get("semantic_upgrades") or 0
        console.print(
            f"  fact recall: {f_recall:.0%} ({sum(f['found'] for f in fact['facts'])}/{len(fact['facts'])} in-scope facts"
            + (f", {upgrades} via local entailment)" if upgrades else ")")
            if f_recall is not None
            else "  fact recall: n/a (no in-scope gold facts)"
        )
        if graph_conn is not None:
            gold.annotate_missed_facts(
                fact, gold.claim_keywords(graph_conn, entry.entity_id, entry.eval_vol)
            )
        for f in fact["facts"]:
            if not f["found"]:
                # Asymmetric on purpose: "in graph" is reliable, its negation is only "no
                # lexical match" and over-reports on synonyms and long gold sentences -- so it
                # is worded as a lead, not a verdict (gold.annotate_missed_facts, PHASE_26.md).
                origin = ("[dim](in graph — page omitted it, likely the prose cap)[/dim]"
                          if f.get("in_graph") else "[dim](no lexical match in graph — check for an extraction gap)[/dim]")
                console.print(f"    [yellow]not recalled:[/yellow] {f['text']} {origin}")
        # Only suggest re-extraction when at least one miss has no graph match at all. A page
        # whose misses are ALL `in_graph` is bounded by the prose cap, and re-extracting it would
        # spend a full character's calls to change nothing (PHASE_26.md, 2026-09-21). The
        # converse is a lead rather than proof (see annotate_missed_facts), which is the right
        # bias here: this only offers a command for a human to run, it never spends anything.
        extraction_gaps = [f for f in fact["facts"] if not f["found"] and not f.get("in_graph")]
        if f_recall is not None and f_recall < _TOP_UP_RECALL_THRESHOLD and extraction_gaps:
            console.print(
                f"    [dim]low recall — targeted top-up: wiki extract --series {settings.series_id} "
                f'--volumes {entry.eval_vol} --entities "{entry.canonical}"[/dim]'
            )

        baseline_value = baseline.get(entry.entity_id)
        if baseline_value is not None and f_recall is not None and f_recall < baseline_value - 1e-9:
            regressions.append((entry.canonical, f_recall, baseline_value))
            console.print(
                f"    [red]REGRESSION — fact recall {f_recall:.0%} is below the recorded baseline "
                f"{baseline_value:.0%} (docs/eval/gold/{settings.series_id}/baseline.json)[/red]"
            )

        # Phase 23 C1/C4: precision + negative-fact check.
        precision = gold.score_fact_precision(entry.facts, entry.negative_facts, page)
        p_value = precision["precision"]
        console.print(
            f"  fact precision: {p_value:.0%} ({precision['supported']} supported, "
            f"{precision['contradicted']} contradicted)"
            if p_value is not None
            else "  fact precision: n/a (no positive or negative gold facts matched either way)"
        )
        for nf in precision["negative_facts"]:
            if nf["contradicted"]:
                negative_fact_violations.append((entry.canonical, nf["text"]))
                console.print(
                    f"    [red]VIOLATION — the page asserts something gold says must not be "
                    f"true: {nf['text']!r}[/red]"
                )

        precision_baseline_value = precision_baseline.get(entry.entity_id)
        if (
            precision_baseline_value is not None
            and p_value is not None
            and p_value < precision_baseline_value - 1e-9
        ):
            precision_regressions.append((entry.canonical, p_value, precision_baseline_value))
            console.print(
                f"    [red]REGRESSION — fact precision {p_value:.0%} is below the recorded "
                f"baseline {precision_baseline_value:.0%} "
                f"(docs/eval/gold/{settings.series_id}/baseline.json)[/red]"
            )

        if entailment["sections"]:
            console.print("  citation entailment:")
            for s in entailment["sections"]:
                if s["dangling_citations"]:
                    console.print(
                        f"    [red]{s['section']}: dangling citation(s) {s['dangling_citations']}[/red]"
                    )
                else:
                    verdict = "grounded" if s["grounded"] else "[yellow]weak overlap[/yellow]"
                    detail = f"{s['evidence_count']} cited paragraph(s)"
                    if s["beat_summary_checked"]:
                        beat_verdict = "grounded" if s["beat_summary_grounded"] else "weak overlap"
                        detail += (
                            f"; beat_summary check: {beat_verdict} "
                            f"({s['beat_summary_para_count']} paragraph(s))"
                        )
                    console.print(f"    {s['section']}: {verdict} ({detail})")

    if events_conn is not None:
        events_conn.close()
    if graph_conn is not None:
        graph_conn.close()

    console.print(f"\nEvaluated {len(entries)} gold-covered character(s).")
    if missing_pages:
        console.print(f"\n[red]FAIL[/red] — no generated page for: {', '.join(missing_pages)}")
        return False
    if negative_fact_violations:
        console.print(f"\n[red]FAIL[/red] — {len(negative_fact_violations)} negative-fact violation(s):")
        for canonical, text in negative_fact_violations:
            console.print(f"  {canonical}: {text!r}")
        console.print(
            "The page asserts something the gold data explicitly says must not be true — this is "
            "not a regression check, it never passes just because a baseline hasn't caught up."
        )
        return False
    # Phase 27: recall WARNS, precision FAILS. They are not the same kind of number.
    # Precision and negative-facts measure whether the page says something UNTRUE -- unbounded,
    # always actionable, a real defect every time. Recall measures how much of a full Fandom
    # page got reproduced, and is structurally capped by the deliberate 2-4 sentence prose limit
    # (CLAUDE.md §2): norah-arendt has 68 claims and room for about six. Gating on it left
    # `audit eval` red for days over a ceiling no correct work could lift, and a permanently-red
    # gate is one nobody reads. See docs/vision/PHASE_27.md.
    if regressions:
        console.print(f"\n[yellow]WARN[/yellow] — {len(regressions)} fact-recall regression(s) against the recorded baseline:")
        for canonical, current, base in regressions:
            console.print(f"  {canonical}: {current:.0%} (was {base:.0%})")
        console.print(
            "  [dim]Recall does not gate (Phase 27): it is bounded by the prose cap, so a drop "
            "may be correct behaviour. Read the per-fact 'in graph' markers above — those "
            "separate a real extraction gap from a fact the page simply had no room for.[/dim]"
        )
    if precision_regressions:
        console.print(f"\n[red]FAIL[/red] — {len(precision_regressions)} fact-precision regression(s) against the recorded baseline:")
        for canonical, current, base in precision_regressions:
            console.print(f"  {canonical}: {current:.0%} (was {base:.0%})")
        console.print(
            "Precision gates: a page asserting something gold contradicts is a defect, not a "
            f"ceiling. If the drop is genuinely expected, re-record docs/eval/gold/"
            f"{settings.series_id}/baseline.json deliberately (see its `_comment`) rather than "
            "re-running until it passes."
        )
        return False
    console.print("\n[green]OK[/green] — every gold-covered character has a page, no negative-fact "
                  "violations, no fact-precision regression. Recall is reported above and does "
                  "not gate; see the WARN block if any dropped.")
    return True


# ---------------------------------------------------------------------------
# Phase 3 — claims
# ---------------------------------------------------------------------------


@register("claims", "wiki extract")
def _claims_report(settings: Settings, console: Console) -> bool:
    import json
    from collections import Counter

    from rich.table import Table

    from .. import paths, provenance

    claim_files = sorted(paths.CLAIMS_DIR.glob("v*.jsonl")) if paths.CLAIMS_DIR.is_dir() else []
    if not claim_files:
        raise StageNotReady(f"No claims found in {paths.relative(paths.CLAIMS_DIR)}. Run: wiki extract --volumes 1-13")

    gaz_path = paths.gazetteer()
    canonical_by_id: dict[str, str] = {}
    characters: list[dict] = []
    if gaz_path.is_file():
        gaz = json.loads(gaz_path.read_text(encoding="utf-8"))
        canonical_by_id = {e["entity_id"]: e["canonical"] for e in gaz.get("entities", [])}
        characters = [e for e in gaz.get("entities", []) if e["type"] == "CHARACTER"]

    claims_per_char: Counter[str] = Counter()
    predicate_counts: Counter[str] = Counter()
    polarity_counts: Counter[str] = Counter()
    confidence_buckets: Counter[str] = Counter()
    total = 0
    bad_quotes: list[tuple[str, str, str]] = []  # (claim_id, vol, para_id)

    for path in claim_files:
        vol_str = path.stem  # "v03"
        paragraphs_by_id: dict[str, str] | None = None  # loaded lazily, only if this volume has claims

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            claim = json.loads(line)
            total += 1
            claims_per_char[claim["subject"]] += 1
            predicate_counts[claim["predicate"]] += 1
            polarity_counts[claim["polarity"]] += 1
            bucket_lo = min(int(claim["confidence"] * 10) * 10, 90)
            confidence_buckets[f"{bucket_lo}-{bucket_lo + 10}%"] += 1

            if paragraphs_by_id is None:
                vol = claim["first_vol"]
                para_path = paths.parsed_volume(vol)
                paragraphs_by_id = {}
                if para_path.is_file():
                    for pline in para_path.read_text(encoding="utf-8").splitlines():
                        if pline.strip():
                            record = json.loads(pline)
                            paragraphs_by_id[record["para_id"]] = record["text"]

            for ev in claim["evidence"]:
                text = paragraphs_by_id.get(ev["para_id"])
                if text is None or ev["quote"] not in text:
                    bad_quotes.append((claim["claim_id"], vol_str, ev["para_id"]))

    console.print(f"{total:,} claim(s) across {len(claim_files)} volume(s).")

    table = Table(title="Claims report — top characters by claim count", header_style="bold")
    table.add_column("Character")
    table.add_column("Claims", justify="right")
    for subject, n in claims_per_char.most_common(20):
        table.add_row(canonical_by_id.get(subject, subject), str(n))
    console.print(table)

    pred_table = Table(title="Predicate distribution", header_style="bold")
    pred_table.add_column("Predicate")
    pred_table.add_column("Count", justify="right")
    for predicate, n in predicate_counts.most_common(15):
        pred_table.add_row(predicate, str(n))
    console.print(pred_table)

    console.print(f"\nPolarity: {dict(polarity_counts)}")
    console.print(f"Confidence spread: {dict(sorted(confidence_buckets.items()))}")

    # Phase 22 A6 (S7): yield assertions -- the two numbers the 2026-09-09 audit had to measure
    # by hand off disk (5 of 14 characters with zero claims; 24% of the graph APPEARANCE noise)
    # now print on every run instead of waiting for a one-off audit to notice.
    if characters:
        min_claims = int(settings.page_gate_config.get("min_claims", 1))
        with_min = sum(1 for e in characters if claims_per_char.get(e["entity_id"], 0) >= min_claims)
        console.print(
            f"Roster yield: {with_min}/{len(characters)} "
            f"({100 * with_min / len(characters):.0f}%) CHARACTER entities have "
            f">= {min_claims} claim(s) (page_gate.min_claims)."
        )
    if total:
        appearance_n = predicate_counts.get("APPEARANCE", 0)
        console.print(
            f"APPEARANCE share of all claims: {appearance_n:,}/{total:,} "
            f"({100 * appearance_n / total:.0f}%)."
        )

    # Phase 22 A6: a report reading a stage's output must not silently vouch for a crashed run's
    # partial claims -- the exact blind spot the 2026-09-09 audit found (the crash was caught only
    # by an independent hand-audit, not by this report). `_guard_upstream_failures` (cli.py)
    # already stops downstream COMMANDS from building on this; this closes the same hole in the
    # DIAGNOSTIC that is supposed to catch it first.
    failed = provenance.failed_stage_artifacts(["claims"])
    if failed:
        console.print(
            f"\n[red]FAIL[/red] — the most recent run that wrote claims "
            f"([red]{failed[0]['run_id']}[/red]) recorded outcome: failed "
            f"({failed[0].get('error', 'no error recorded')}). These claims may be a crashed "
            f"run's partial output. Roll back with "
            f"[cyan]wiki rollback {failed[0]['run_id']} --yes[/cyan] and re-run wiki extract."
        )
    if bad_quotes:
        console.print(
            f"\n[red]FAIL[/red] — {len(bad_quotes)} claim(s) cite a quote that does not appear "
            f"verbatim in its paragraph. First few: "
            + ", ".join(f"{cid} ({vol}, {pid})" for cid, vol, pid in bad_quotes[:5])
        )
    if bad_quotes or failed:
        return False
    console.print("\n[green]OK[/green] — every cited quote appears verbatim in its paragraph.")
    return True


# ---------------------------------------------------------------------------
# Phase 4 — graph / contradictions
# ---------------------------------------------------------------------------


@register("contradictions", "wiki graph build")
def _contradictions_report(settings: Settings, console: Console) -> bool:
    import json
    import sqlite3
    from collections import Counter

    from rich.table import Table

    from .. import paths

    doc_path = paths.contradictions()
    if not doc_path.is_file():
        raise StageNotReady(f"No {paths.relative(doc_path)} yet. Run: wiki graph build")

    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    conflicts = doc.get("conflicts", [])
    summary = doc.get("summary", {"total": len(conflicts), "narrative_change": 0, "extraction_error": 0, "flagged": 0})

    console.print(
        f"{summary['total']} conflict(s) — {summary['extraction_error']} extraction_error, "
        f"{summary['narrative_change']} narrative_change, {summary['flagged']} flagged."
    )

    by_predicate: Counter[str] = Counter(c["predicate"] for c in conflicts)
    if by_predicate:
        table = Table(title="Conflicts by predicate", header_style="bold")
        table.add_column("Predicate")
        table.add_column("Count", justify="right")
        for predicate, n in by_predicate.most_common(20):
            table.add_row(predicate, str(n))
        console.print(table)

    by_resolution: Counter[str] = Counter(c["resolution"] for c in conflicts)
    console.print(f"\nResolutions: {dict(by_resolution)}")

    by_arbiter: Counter[str] = Counter(c["arbiter"] for c in conflicts)
    console.print(f"Arbiters: {dict(by_arbiter)}")

    flagged = [c for c in conflicts if c["resolution"] == "flag"]
    if flagged:
        console.print(f"\n[yellow]{len(flagged)} flagged for human review:[/yellow]")
        for c in flagged[:10]:
            console.print(
                f"  {c['subject']} {c['predicate']} @ v{c['a']['vol']}: "
                f"\"{c['a']['value']}\" vs \"{c['b']['value']}\" — {c['rationale']}"
            )

    # Phase 17: value canonicalization -- absent entirely for a contradictions.json written
    # before this phase (nothing to print then, not a crash).
    canon = doc.get("canonicalization", {})
    merges = canon.get("merges", [])
    if merges:
        canon_summary = canon.get("summary", {})
        console.print(
            f"\n{canon_summary.get('groups_merged', len(merges))} near-duplicate value group(s) "
            f"canonicalized, absorbing {canon_summary.get('variants_absorbed', 0)} variant(s):"
        )
        by_canon_predicate: Counter[str] = Counter(m["predicate"] for m in merges)
        table = Table(title="Canonicalized by predicate", header_style="bold")
        table.add_column("Predicate")
        table.add_column("Groups merged", justify="right")
        for predicate, n in by_canon_predicate.most_common(20):
            table.add_row(predicate, str(n))
        console.print(table)
        for m in merges[:10]:
            variant_text = ", ".join(f'"{v["value"]}"' for v in m["variants"])
            console.print(f"  {m['subject']} {m['predicate']}: \"{m['canonical']}\" absorbs {variant_text}")

    db_path = paths.graph_db()
    if not db_path.is_file():
        console.print(f"\n[red]FAIL[/red] — {paths.relative(doc_path)} exists but {paths.relative(db_path)} does not.")
        return False

    conn = sqlite3.connect(db_path)
    n_intervals = conn.execute("SELECT COUNT(*) FROM intervals").fetchone()[0]
    n_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    n_open = conn.execute("SELECT COUNT(*) FROM intervals WHERE vol_end IS NULL").fetchone()[0]
    conn.close()

    console.print(
        f"\n{n_intervals:,} interval(s) ({n_open:,} still open), {n_edges:,} edge(s) in "
        f"{paths.relative(db_path)}."
    )

    if n_intervals == 0:
        console.print("\n[red]FAIL[/red] — graph.db has zero intervals. Run: wiki graph build")
        return False
    console.print("\n[green]OK[/green] — contradictions resolved and graph built.")
    return True


# ---------------------------------------------------------------------------
# Phase 22 C3 — verify
# ---------------------------------------------------------------------------


@register("verify", "wiki verify")
def _verify_report(settings: Settings, console: Console) -> bool:
    """Read-only over `data/04_graph/verification.json` (`wiki verify`'s deliverable) — this
    report never calls the LLM itself, same rule every report in this file follows (module
    docstring). A single flagged fact is not itself a FAIL (a flag is a pointer for a human to
    look at, same "never silently pick a winner" principle `_contradictions_report`'s `flagged`
    count already applies) — but Phase 23 B5 makes the report FAIL when the FRACTION of checked
    facts flagged unsupported crosses `config/extraction.yaml`'s `extraction.verify.
    max_unsupported_rate`: a precision problem that severe should stop a run, not just print
    yellow text a human might not read."""
    import json

    from .. import paths

    doc_path = paths.verification()
    if not doc_path.is_file():
        raise StageNotReady(f"No {paths.relative(doc_path)} yet. Run: wiki verify --upto <N>")

    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    summary = doc.get(
        "summary",
        {"characters_checked": 0, "characters_flagged": 0, "facts_flagged": 0, "facts_checked": 0},
    )
    results = doc.get("results", [])

    console.print(
        f"{summary['characters_checked']} character(s) checked, "
        f"{summary['characters_flagged']} with a flagged fact "
        f"({summary['facts_flagged']} fact(s) total)."
    )

    for entry in results:
        console.print(f"\n[yellow]{entry['canonical']}[/yellow] (v{entry['upto_vol']:02d}):")
        for fact in entry["flagged"]:
            console.print(f"  {fact['predicate']} = \"{fact['value']}\" — {fact['rationale']}")

    facts_checked = summary.get("facts_checked", 0)
    facts_flagged = summary.get("facts_flagged", 0)
    max_rate = float(
        settings.extraction_behaviour.get("verify", {}).get("max_unsupported_rate", 0.15)
    )
    if facts_checked > 0:
        rate = facts_flagged / facts_checked
        console.print(f"\nUnsupported rate: {rate:.1%} of {facts_checked} fact(s) checked.")
        if rate > max_rate:
            console.print(
                f"[red]FAIL[/red] — unsupported rate {rate:.1%} exceeds the "
                f"{max_rate:.0%} ceiling (extraction.verify.max_unsupported_rate)."
            )
            return False

    console.print("\n[green]OK[/green] — verification report read.")
    return True


# ---------------------------------------------------------------------------
# Phase 6 — pages
# ---------------------------------------------------------------------------


@register("pages", "wiki synthesize")
def _pages_report(settings: Settings, console: Console) -> bool:
    import json
    import re
    from collections import defaultdict

    from .. import paths

    if not paths.PAGES_DIR.is_dir() or not any(paths.PAGES_DIR.iterdir()):
        raise StageNotReady(f"No pages found in {paths.relative(paths.PAGES_DIR)}. Run: wiki synthesize --upto 1")

    # entity_id -> [(vol, page_dict), ...], one entry per FILE actually written. A gap in the
    # volume sequence (e.g. v01, v03 but no v02) means v02's claim set was unchanged from v01's
    # and synth/cache.py skipped it (CONTRACTS §5.1) -- the only way to reconstruct "how much
    # was cached" without a separate manifest nothing else needs.
    by_entity: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for entity_dir in sorted(p for p in paths.PAGES_DIR.iterdir() if p.is_dir()):
        for f in sorted(entity_dir.glob("v*.json")):
            m = re.match(r"v(\d+)\.json$", f.name)
            if not m:
                continue
            by_entity[entity_dir.name].append((int(m.group(1)), json.loads(f.read_text(encoding="utf-8"))))

    if not by_entity:
        raise StageNotReady(f"No page files found under {paths.relative(paths.PAGES_DIR)}. Run: wiki synthesize --upto 1")

    def _sentence_count(text: str) -> int:
        return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])

    n_attrs = len(settings.attributes)
    prose_sections = {s["key"]: s for s in settings.page_outline if s["kind"] == "prose"}
    total_written = 0
    total_cached = 0
    field_coverage: list[float] = []
    lengths_by_key: dict[str, list[int]] = {key: [] for key in prose_sections}
    out_of_bounds: list[tuple[str, int, str, int]] = []
    sparse: list[str] = []

    for entity_id, pages in by_entity.items():
        pages.sort(key=lambda t: t[0])
        vols = [v for v, _ in pages]
        total_written += len(vols)
        total_cached += (vols[-1] - vols[0] + 1) - len(vols)  # gap size in the written sequence

        _, latest_page = pages[-1]
        # Phase 22 A5 (U6): a `default:`-materialized field carries no real evidence -- counting
        # it here would make every character look non-sparse (STATUS now always exists) and
        # blind this exact detector to the S1-type "5 of 14 pages have no facts" symptom it
        # exists to catch.
        n_present = sum(
            1 for k, v in latest_page["fields"].items()
            if k not in ("affiliations", "relationships") and not (isinstance(v, dict) and v.get("inferred"))
        )
        coverage = n_present / n_attrs if n_attrs else 0.0
        field_coverage.append(coverage)
        if coverage == 0:
            sparse.append(entity_id)

        for _, page in pages:
            for key, section in prose_sections.items():
                entry = page["prose"].get(key)
                if not entry:
                    continue
                n = _sentence_count(entry["text"])
                lengths_by_key[key].append(n)
                if n < section.get("min_sentences", 2) or n > section.get("max_sentences", 4):
                    out_of_bounds.append((entity_id, page["upto_vol"], key, n))

    console.print(f"{len(by_entity)} character(s), {total_written} page file(s) written.")
    denom = total_cached + total_written
    console.print(
        f"Cache reuse: {total_cached} cutoff(s) skipped (unchanged claim set) — "
        f"{100 * total_cached / denom if denom else 0:.0f}% of all cutoffs attempted."
    )
    if field_coverage:
        console.print(
            f"Average field coverage (latest cutoff): "
            f"{100 * sum(field_coverage) / len(field_coverage):.0f}% of {n_attrs} configured attributes."
        )
    for key, lengths in lengths_by_key.items():
        if lengths:
            title = prose_sections[key].get("title", key)
            console.print(f"{title} prose: avg {sum(lengths) / len(lengths):.1f} sentence(s) over {len(lengths)} page(s).")

    if out_of_bounds:
        console.print(f"\n[yellow]{len(out_of_bounds)} prose field(s) outside configured sentence bounds:[/yellow]")
        for entity_id, vol, kind, n in out_of_bounds[:10]:
            console.print(f"  {entity_id} v{vol:02d} {kind}: {n} sentence(s)")

    if sparse:
        console.print(
            f"\n[yellow]{len(sparse)} character(s) with zero structured fields on their latest page:[/yellow] "
            f"{', '.join(sparse[:10])}"
        )

    if total_written == 0:
        console.print("\n[red]FAIL[/red] — no pages written.")
        return False
    console.print("\n[green]OK[/green] — pages generated.")
    return True


# ---------------------------------------------------------------------------
# Phase 7 — links (the site bundle)
# ---------------------------------------------------------------------------


@register("links", "wiki site build")
def _links_report(settings: Settings, console: Console) -> bool:
    """Every entity_id and every wikified Markdown link (`[text](route)`) anywhere in
    `data/06_bundle/` must resolve against `links.json` — the one file CONTRACTS §6 says every
    other bundle file's references are checked against. Reads bundle files directly rather than
    reusing site/bundle.py, matching every other report's "read and judge, nothing more" rule
    (module docstring): a bug that made bundle.py write a broken reference must not also be
    invisible to the report that is supposed to catch it. Also applies `_relationships_report`'s
    vol<=cutoff spoiler fence to a FACTION codex entry's `key_events` (Phase 21 part 2) and to
    `timeline/v{NN}.json` (Phase 21 part 3, including a same-volume sanity check on each event's
    own `vol`, and an `index.json` `timeline_volumes` disk-agreement check mirroring
    `_relationships_report`'s `relationship_pairs` one) — there is no separate registered report
    for codex/timeline entries the way relationships gets one, so those checks live here alongside
    the link-resolution check they share a loop with. Phase 22 A4: `check_links_in_text` now also
    covers every `beat_summary` — a codex entry's `key_events`, a relationship pair's
    `shared_scenes`, and a timeline event — now that `site/bundle.py` wikifies that field too.
    Phase 22 A5: also covers each page's `lead` string (S6, same wikify pass); `mentions_of` is
    now `[{"entity_id", "note"}, ...]` (U5) rather than a bare id list, so its `check_id` reads
    `mention["entity_id"]`. Phase 22 A6 (S7): closes the hole this docstring used to document as
    deliberate -- every quote list (page `quotes`, a codex `key_events` entry, a relationship
    pair's `shared_scenes`, a timeline event) is now checked for a null `speaker`, which used to
    ship silently (the audit found 9 such quotes already on the live Spice & Wolf bundle). This
    does not fix extraction (that is Phase B2's job — suppress an unresolved-speaker quote at the
    source) — it only stops the report from vouching for a bundle that already has them."""
    import json
    import re

    from .. import paths

    links_path = paths.bundle_links()
    if not links_path.is_file():
        raise StageNotReady(f"No {paths.relative(links_path)} yet. Run: wiki site build")

    links = json.loads(links_path.read_text(encoding="utf-8"))
    valid_ids = set(links)
    valid_routes = {entry["route"] for entry in links.values()}

    broken: list[tuple[str, str, str]] = []  # (file, kind, ref)
    null_speaker_quotes: list[str] = []  # Phase 22 A6 (S7)
    md_link_re = re.compile(r"\]\(([^)]+)\)")

    def check_id(file: str, ref: str | None) -> None:
        if ref is not None and ref not in valid_ids:
            broken.append((file, "entity_id", ref))

    def check_links_in_text(file: str, text: str | None) -> None:
        for href in md_link_re.findall(text or ""):
            if href not in valid_routes:
                broken.append((file, "route", href))

    def check_quote_speakers(file: str, quotes: list[dict] | None) -> None:
        for q in quotes or []:
            if not q.get("speaker"):
                null_speaker_quotes.append(f"{file}: quote {q.get('quote', '')[:40]!r} has no resolved speaker")

    pages_dir = paths.BUNDLE_DIR / "pages"
    page_files = sorted(pages_dir.glob("*.json")) if pages_dir.is_dir() else []
    page_ids_on_disk = {f.stem for f in page_files}

    index_path = paths.bundle_index()
    index_doc: dict = {}
    missing_page_files: list[str] = []  # Phase 23 E6
    if index_path.is_file():
        index_doc = json.loads(index_path.read_text(encoding="utf-8"))
        for c in index_doc.get("characters", []):
            check_id("index.json", c["entity_id"])
            # Phase 23 E6: a roster id resolving against links.json is not the same guarantee as
            # a page actually existing on disk -- site/bundle.py::build_index now filters the
            # roster to written_ids at build time, but this check catches a regression of that
            # fix (or a bundle built by an older wiki site build before E6) independently, the
            # same "read and judge, nothing more" discipline every other check in this report
            # follows rather than trusting the fix that produced the data it's auditing.
            if c["entity_id"] not in page_ids_on_disk:
                missing_page_files.append(c["entity_id"])
    for f in page_files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for page in doc.get("cutoffs", {}).values():
            for aff in page["fields"].get("affiliations", []):
                check_id(f.name, aff["entity_id"])
            for rel in page["fields"].get("relationships", []):
                check_id(f.name, rel["entity_id"])
            for mention in page.get("mentions_of", []):
                check_id(f.name, mention["entity_id"])
            for entry in page.get("prose", {}).values():
                if entry:
                    check_links_in_text(f.name, entry["text"])
            check_links_in_text(f.name, page.get("lead"))  # Phase 22 A5 (S6)
            check_quote_speakers(f.name, page.get("quotes"))  # Phase 22 A6 (S7)

    n_codex = 0
    spoiler_broken: list[str] = []  # "<file>@<cutoff>: <what> is vol <v>, exceeds the cutoff"
    for kind in ("factions", "places", "tech"):
        cpath = paths.bundle_codex(kind)
        if not cpath.is_file():
            continue
        doc = json.loads(cpath.read_text(encoding="utf-8"))
        n_codex += len(doc)
        for entity_id, entry in doc.items():
            for cutoff_key, snap in entry.get("cutoffs", {}).items():
                cutoff = int(cutoff_key)
                for member_id in snap.get("members", []):
                    check_id(f"codex/{kind}.json", member_id)
                for adversary_id in snap.get("adversaries", []):
                    check_id(f"codex/{kind}.json", adversary_id)
                for related_id in snap.get("related", []):
                    check_id(f"codex/{kind}.json", related_id)
                check_links_in_text(f"codex/{kind}.json", snap.get("summary"))
                # key_events (Phase 21 part 2, FACTION kind only) — link-check participants and
                # apply the same vol<=cutoff spoiler fence _relationships_report applies to
                # relationships/*.json's shared_scenes (CONTRACTS §6.1/§6.4).
                for event in snap.get("key_events", []):
                    for participant_id in event.get("participants", []):
                        check_id(f"codex/{kind}.json", participant_id)
                    check_links_in_text(f"codex/{kind}.json", event.get("beat_summary"))
                    if event["vol"] > cutoff:
                        spoiler_broken.append(
                            f"codex/{kind}.json@{cutoff_key}: key_event {event['event_id']} is "
                            f"vol {event['vol']}, exceeds the cutoff"
                        )
                    for q in event.get("quotes", []):
                        if q["vol"] > cutoff:
                            spoiler_broken.append(
                                f"codex/{kind}.json@{cutoff_key}: quote in {event['event_id']} "
                                f"is vol {q['vol']}, exceeds the cutoff"
                            )
                    check_quote_speakers(f"codex/{kind}.json", event.get("quotes"))  # Phase 22 A6 (S7)

    n_relationships = 0
    rel_dir = paths.BUNDLE_DIR / "relationships"
    rel_files = sorted(rel_dir.glob("*.json")) if rel_dir.is_dir() else []
    for f in rel_files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        n_relationships += 1
        for pid in doc.get("pair", []):
            check_id(f.name, pid)
        for snap in doc.get("cutoffs", {}).values():
            for rel in [*snap.get("relations", []), *snap.get("history", [])]:
                check_id(f.name, rel["subject"])
                check_id(f.name, rel["object"])
            for scene in snap.get("shared_scenes", []):
                check_links_in_text(f.name, scene.get("beat_summary"))
                check_quote_speakers(f.name, scene.get("quotes"))  # Phase 22 A6 (S7)
            # shared_scenes[].quotes[].speaker/addressee may carry a surface form rather than a
            # resolved entity_id (build_quotes_section's own docstring notes the same for page
            # quotes) -- not link-checked against entity_id here, only checked for null (above).

    n_timeline = 0
    timeline_dir = paths.BUNDLE_DIR / "timeline"
    timeline_files = sorted(timeline_dir.glob("*.json")) if timeline_dir.is_dir() else []
    on_disk_volumes: set[int] = set()
    for f in timeline_files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        n_timeline += 1
        vol = doc.get("vol")
        on_disk_volumes.add(vol)
        for chapter in doc.get("chapters", []):
            for event in chapter.get("events", []):
                for participant_id in event.get("participants", []):
                    check_id(f.name, participant_id)
                if event.get("location"):
                    check_id(f.name, event["location"])
                check_links_in_text(f.name, event.get("beat_summary"))
                if event["vol"] != vol:
                    spoiler_broken.append(
                        f"{f.name}: event {event['event_id']} is vol {event['vol']}, "
                        f"does not match its own file's volume {vol}"
                    )
                for sc in event.get("state_changes", []):
                    check_id(f.name, sc["subject"])
                    if sc.get("object"):
                        check_id(f.name, sc["object"])
                for q in event.get("quotes", []):
                    if q["vol"] > vol:
                        spoiler_broken.append(
                            f"{f.name}: quote in {event['event_id']} is vol {q['vol']}, exceeds "
                            f"the file's volume {vol}"
                        )
                check_quote_speakers(f.name, event.get("quotes"))  # Phase 22 A6 (S7)
    indexed_volumes = set(index_doc.get("timeline_volumes", []))
    if indexed_volumes != on_disk_volumes:
        spoiler_broken.append(
            f"index.json timeline_volumes disagrees with disk: "
            f"{len(on_disk_volumes - indexed_volumes)} missing, {len(indexed_volumes - on_disk_volumes)} extra"
        )

    # Phase 25: the emitted MkDocs wiki tree (dist/<series>/wiki/) is a separate artifact from
    # data/06_bundle/ above -- checked here too rather than in a new registered report, since a
    # broken markdown link and a broken JSON-bundle reference are the same class of problem this
    # stage is already the sanctioned place to catch (module docstring's own "wiki audit links"
    # framing). Two things get checked in one pass: every relative link resolves to a real file
    # on disk, and no link escapes its own cutoff directory -- the cross-cutoff-leak check `site/mkdocs_wiki.py`'s module docstring names as the
    # single highest-risk line in the emitter.
    import os
    import re as _re
    from pathlib import Path

    md_broken: list[str] = []  # "<file>: link '<target>' <problem>"
    md_link_re = _re.compile(r"\]\(([^)\s]+)\)")
    n_md_files = 0
    linked_to: set[Path] = set()
    if paths.SITE_DIR.is_dir():
        for md_file in paths.SITE_DIR.rglob("*.md"):
            n_md_files += 1
            rel_parts = md_file.relative_to(paths.SITE_DIR).parts
            # [30] A file at the site ROOT belongs to no cutoff -- `index.md` is the front door,
            # and routing readers into each cutoff is the whole of its job. Only a file INSIDE a
            # cutoff tree is held to the containment rule, which is where a cross-cutoff link
            # would actually be a spoiler leak. It still has to resolve and stay under SITE_DIR.
            cutoff_root = rel_parts[0] if len(rel_parts) > 1 else None
            for target in md_link_re.findall(md_file.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue  # a same-page anchor only, e.g. "](#section)"
                resolved = Path(os.path.normpath(md_file.parent / target_path))
                if not resolved.is_file():
                    md_broken.append(f"{md_file.relative_to(paths.SITE_DIR)}: link {target!r} does not resolve to a file")
                    continue
                if resolved != md_file:
                    linked_to.add(resolved)
                try:
                    top = resolved.relative_to(paths.SITE_DIR).parts[0]
                except ValueError:
                    md_broken.append(f"{md_file.relative_to(paths.SITE_DIR)}: link {target!r} escapes {paths.relative(paths.SITE_DIR)}")
                    continue
                if cutoff_root and top != cutoff_root:
                    md_broken.append(
                        f"{md_file.relative_to(paths.SITE_DIR)}: link {target!r} resolves into "
                        f"{top!r}, outside its own cutoff dir {cutoff_root!r}"
                    )
        # 2026-09-25: a pair page is not in the nav by design, so a link from a character page is
        # its only way in. All 30 shipped unreachable and this check could not see it: a page
        # nothing links to has no broken link. Scoped to pair pages; the nav covers the rest.
        for md_file in paths.SITE_DIR.glob("*/relationships/*.md"):
            if Path(os.path.normpath(md_file)) not in linked_to:
                md_broken.append(f"{md_file.relative_to(paths.SITE_DIR)}: orphan, no page links to it")

    console.print(
        f"{len(valid_ids)} route(s) in links.json; checked {len(page_files)} character bundle(s), "
        f"{n_codex} codex entr(y/ies), {n_relationships} relationship pair bundle(s), "
        f"{n_timeline} timeline page(s), {n_md_files} emitted Markdown page(s)."
    )
    if md_broken:
        console.print(f"\n[red]FAIL[/red] — {len(md_broken)} broken/leaking Markdown link(s):")
        for msg in md_broken[:15]:
            console.print(f"  {msg}")
    if broken or spoiler_broken or null_speaker_quotes or missing_page_files or md_broken:
        if broken:
            console.print(f"\n[red]FAIL[/red] — {len(broken)} broken reference(s):")
            for file, kind, ref in broken[:15]:
                console.print(f"  {file}: {kind} {ref!r} does not resolve")
        if spoiler_broken:
            console.print(f"\n[red]FAIL[/red] — {len(spoiler_broken)} spoiler-safety violation(s):")
            for msg in spoiler_broken[:15]:
                console.print(f"  {msg}")
        if null_speaker_quotes:
            console.print(f"\n[red]FAIL[/red] — {len(null_speaker_quotes)} quote(s) with no resolved speaker:")
            for msg in null_speaker_quotes[:15]:
                console.print(f"  {msg}")
        if missing_page_files:  # Phase 23 E6
            console.print(
                f"\n[red]FAIL[/red] — {len(missing_page_files)} roster id(s) with no pages/<id>.json on disk:"
            )
            for entity_id in missing_page_files[:15]:
                console.print(f"  {entity_id}: in index.json's roster but no page file exists")
        return False
    console.print("\n[green]OK[/green] — every reference resolves and every codex key_event respects its cutoff.")
    return True


@register("relationships", "wiki site build")
def _relationships_report(settings: Settings, console: Console) -> bool:
    """CLAUDE.md §1's actual safety net for Phase 21's relationship pages: every
    `relationships/<a>--<b>.json` cutoff entry may only contain relations/shared-scene evidence
    whose OWN volume is `<=` that cutoff key — the same check `wiki audit pages`/`test_spoiler_
    leak.py` apply to character pages, applied here to a page kind that is assembled entirely
    differently (pair-scoped, no `synth/cache.py` claim_set_hash). Also FAILs if a bundled pair
    is not CHARACTER/CHARACTER (population bug in `_relationship_pairs`) or if `index.json`'s
    `relationship_pairs` list disagrees with what is actually on disk (a client trusting that
    list to decide whether to render a link would otherwise 404 or silently omit one)."""
    import json

    from .. import paths
    from ..entities.gazetteer import load as load_gazetteer

    rel_dir = paths.BUNDLE_DIR / "relationships"
    if not rel_dir.is_dir() or not any(rel_dir.glob("*.json")):
        raise StageNotReady(f"No {paths.relative(rel_dir)} yet. Run: wiki site build --upto N")

    gaz = load_gazetteer()
    entity_types = {e["entity_id"]: e["type"] for e in gaz["entities"]}

    failures: list[str] = []
    on_disk_pairs: set[tuple[str, str]] = set()
    n_cutoffs = 0

    for f in sorted(rel_dir.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        pair = doc.get("pair", [])
        if len(pair) != 2:
            failures.append(f"{f.name}: 'pair' is not a 2-element list ({pair!r})")
            continue
        a, b = pair
        on_disk_pairs.add(tuple(sorted((a, b))))
        if entity_types.get(a) != "CHARACTER" or entity_types.get(b) != "CHARACTER":
            failures.append(f"{f.name}: pair ({a}, {b}) is not both CHARACTER type")

        for cutoff_key, snap in doc.get("cutoffs", {}).items():
            n_cutoffs += 1
            cutoff = int(cutoff_key)
            for rel in [*snap.get("relations", []), *snap.get("history", [])]:
                if rel["since_vol"] > cutoff:
                    failures.append(
                        f"{f.name}@{cutoff_key}: relation {rel['predicate']} since_vol="
                        f"{rel['since_vol']} exceeds the cutoff"
                    )
            for scene in snap.get("shared_scenes", []):
                if scene["vol"] > cutoff:
                    failures.append(
                        f"{f.name}@{cutoff_key}: shared scene {scene['event_id']} is vol "
                        f"{scene['vol']}, exceeds the cutoff"
                    )
                for q in scene.get("quotes", []):
                    if q["vol"] > cutoff:
                        failures.append(
                            f"{f.name}@{cutoff_key}: quote in {scene['event_id']} is vol "
                            f"{q['vol']}, exceeds the cutoff"
                        )

    index_path = paths.bundle_index()
    if index_path.is_file():
        index_doc = json.loads(index_path.read_text(encoding="utf-8"))
        indexed_pairs = {tuple(sorted(p)) for p in index_doc.get("relationship_pairs", [])}
        if indexed_pairs != on_disk_pairs:
            missing = on_disk_pairs - indexed_pairs
            extra = indexed_pairs - on_disk_pairs
            failures.append(
                f"index.json relationship_pairs disagrees with disk: "
                f"{len(missing)} missing, {len(extra)} extra"
            )

    console.print(f"Checked {len(on_disk_pairs)} relationship pair bundle(s), {n_cutoffs} cutoff snapshot(s).")
    if failures:
        console.print(f"\n[red]FAIL[/red] — {len(failures)} violation(s):")
        for msg in failures[:15]:
            console.print(f"  {msg}")
        return False
    console.print("\n[green]OK[/green] — relationship pages are spoiler-safe and index-consistent.")
    return True


# ---------------------------------------------------------------------------
# [24] probe — leak-channel measurements
# ---------------------------------------------------------------------------


@register("probe", "wiki probe run")
def _probe_report(settings: Settings, console: Console) -> bool:
    """Read-only over probe runs, keeping the newest row for each probe/cutoff pair
    (`data/<series>/_runs/<run_id>/probe/index.jsonl`) -- previously the one registered stage with
    NO audit report at all, despite `probe/report.py`'s own module docstring asserting one exists
    (docs/vision/plans/0008-pre-full-scale-audit.md S5). The probe subsystem is where this project's
    reportable Leak@t numbers come from, so an unreadable-without-opening-raw-JSON probe run is
    exactly the gap CLAUDE.md's "every phase registers exactly one report" rule exists to close.

    FAILs only on a row that itself carries an `error` (a cutoff the probe could not measure at
    all, e.g. a missing gazetteer) -- a probe measuring cleanly and reporting a nonzero leak_rate
    is not this report's job to judge; that interpretation belongs in docs/vision/PHASE_24.md, not
    a pass/fail gate (see 2026-09-13's "0.303 is a selection artifact" entry for exactly why an
    automated threshold on this number would be premature).
    """
    import json

    from rich.table import Table

    from .. import paths, provenance
    from ..probe.report import probe_report_path

    runs = [m for m in provenance.list_runs() if m.get("command") == "probe"]
    if not runs:
        raise StageNotReady("No probe run yet. Run: wiki probe run index --volumes <range>")

    by_key: dict[tuple[str, int], dict] = {}
    for run in runs:
        jsonl_path = probe_report_path(run["run_id"])
        if not jsonl_path.is_file():
            continue
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                by_key.setdefault((row["probe"], row["upto_vol"]), row)
    rows = list(by_key.values())
    if not rows:
        raise StageNotReady("No probe measurement rows available. Re-run wiki probe run.")
    console.print(
        f"Aggregated from {len(runs)} probe run(s), most recent {runs[0]['run_id']!r}, "
        f"{len(rows)} row(s) after dedup."
    )

    errored = [r for r in rows if "error" in r]
    by_probe: dict[str, list[dict]] = {}
    for r in rows:
        if "error" not in r:
            by_probe.setdefault(r["probe"], []).append(r)

    for probe_name, probe_rows in sorted(by_probe.items()):
        # A probe can report more than one "_rate" field (vocabulary_exposure has two) -- show
        # every one that appears on any row for this probe, in first-seen order, not just one.
        rate_keys: list[str] = []
        for r in probe_rows:
            for k in r:
                if k.endswith("_rate") and k not in rate_keys:
                    rate_keys.append(k)

        table = Table(title=f"probe: {probe_name}", header_style="bold")
        table.add_column("t", justify="right")
        for k in rate_keys:
            table.add_column(k, justify="right")
        for r in sorted(probe_rows, key=lambda r: r["upto_vol"]):
            table.add_row(str(r["upto_vol"]), *(f"{r[k]:.3f}" if k in r else "-" for k in rate_keys))
        console.print(table)

        if probe_name == "candidate_mining":
            for r in sorted(probe_rows, key=lambda r: r["upto_vol"]):
                parameters = r.get("mining_parameters")
                if parameters is not None:
                    sources = r["mining_parameter_sources"]
                    console.print(
                        f"  t={r['upto_vol']} mining parameters: "
                        + ", ".join(f"{k}={v} ({sources[k]})" for k, v in parameters.items())
                    )
                    if sources["min_mentions"] == "legacy_default":
                        console.print("  WARNING: min_mentions is assumed; this gazetteer did not record it.")

        if probe_name == "vocabulary_exposure":
            for r in sorted(probe_rows, key=lambda r: r["upto_vol"]):
                if "claim_files" in r:
                    console.print(
                        f"  t={r['upto_vol']} claim coverage: {r['cutoff_claims']} unique claims "
                        f"from {len(r['claim_files'])} file(s); "
                        f"{r['claims_resting_on_future_vocabulary_paragraph']} cite future-vocabulary paragraphs"
                    )

        if probe_name == "future_fact_leak":
            for r in sorted(probe_rows, key=lambda r: r["upto_vol"]):
                if "artifact_pages" in r:
                    console.print(
                        f"  t={r['upto_vol']} artifact coverage: "
                        f"{len(r['artifact_pages'])} page file(s) searched"
                    )
                counts = r.get("diagnostic_counts")
                if counts is not None:
                    console.print(
                        f"  t={r['upto_vol']} first-match diagnostics: "
                        f"prior fact={counts['prior_fact']}, "
                        f"within word={counts['within_word']}, "
                        f"other page={counts['other_page']}"
                    )
            console.print("  Diagnostics overlap; literal matches are not confirmed spoilers.")

    if errored:
        console.print(f"\n[red]FAIL[/red] — {len(errored)} cutoff(s) the probe could not measure:")
        for r in errored:
            console.print(f"  t={r.get('upto_vol')} {r.get('probe')}: {r.get('error')}")
        return False

    console.print("\n[green]OK[/green] — every requested cutoff measured cleanly.")
    return True
