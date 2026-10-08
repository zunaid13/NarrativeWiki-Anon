"""One module per leak channel (docs/vision/PHASE_24.md). Each exposes a `measure(...)` function
returning a JSON-serializable dict with at least `{"channel", "series", "upto_vol", ...}` --
`probe/report.py` writes whatever these return, unchanged, as one JSONL row.
"""
