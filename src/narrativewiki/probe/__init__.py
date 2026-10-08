"""[24] The measurement subsystem: is filter-before-generate (CLAUDE.md §1) sufficient?

`docs/vision/PHASE_24.md` decomposes spoiler leakage into four channels, named by *when the
future touched the system*:

    L_query     query-time / retrieval  -- which claims reach the generator (the published gate;
                                           enforced today, see tests/test_spoiler_leak.py)
    L_artifact  artifact / structure    -- page existence, pair pages, `vol_end` closures
    L_build     build-time / index      -- the gazetteer, alias clusters, automaton and any other
                                           corpus-wide statistic computed before the cutoff filter
                                           is ever applied
    L_param     parametric / weights    -- the generator's own pretraining on the source text

This package measures channels the render-time gate structurally cannot reach. It never writes
to `data/<series>/0N_*` — it reads pipeline output and reports on it, the same relationship
`audit/reports.py` has to every other stage.
"""
