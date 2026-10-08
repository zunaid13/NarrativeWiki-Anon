"""Run a stage's independent LLM calls a few at a time instead of one after another.

Inputs:     a per-item function, the items, and how many may be in flight at once
            (`Profile.concurrency` from config/models.yaml).
Outputs:    the results, in the order the items were given, exactly as a plain loop produces.
Invariants: - Order in == order out. Every caller here builds a list whose order is part of the
              output (claims per character, scenes per span), so results are never interleaved.
            - `workers <= 1` runs the plain loop, with no threads created at all. Local Ollama
              profiles declare concurrency 1 and behave exactly as before.
            - The first exception propagates, as it would from a serial loop. Calls already in
              flight are allowed to finish (their answers land in the LLM cache, so a rerun
              after fixing the cause does not re-spend them).
            - Nothing here knows about models. Thread-safety lives where the shared state does:
              Budget has its own lock, RunContext.log_call has its own, and the disk cache
              writes through a per-thread temp file.
Contract:   the only thing callers may rely on is "same results, same order, less waiting".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def map_calls(
    fn: Callable[[T], R],
    items: Sequence[T],
    workers: int,
    on_result: Callable[[int, T, R], None] | None = None,
) -> list[R]:
    """`[fn(i) for i in items]`, with up to `workers` calls in flight.

    `on_result(index, item, result)` is called in item order as results become available, which
    is what lets a CLI print a running count that still reads 1, 2, 3 with parallel calls
    underneath.
    """
    if workers <= 1 or len(items) <= 1:
        results = []
        for i, item in enumerate(items):
            result = fn(item)
            results.append(result)
            if on_result is not None:
                on_result(i, item, result)
        return results

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        results = []
        # `.map` yields in submission order, so a slow early item holds back the progress line
        # but never the calls themselves -- which is the trade that keeps output deterministic.
        for i, (item, result) in enumerate(zip(items, pool.map(fn, items))):
            results.append(result)
            if on_result is not None:
                on_result(i, item, result)
        return results
    finally:
        # [33] `.map` submits every item up front, and a `with` block's exit waits for all of
        # them: during a Vertex 429 outage every queued call still ran its own retry ladder
        # after the first had failed. Queued calls are cancelled; only those in flight finish.
        pool.shutdown(wait=True, cancel_futures=True)


def workers_for(client, stage: str, cap: int | None = None) -> int:
    """How many calls this stage's routed profile allows in flight. Never guesses: a profile
    without an explicit `concurrency:` in config/models.yaml stays serial."""
    try:
        workers = int(getattr(client.profile_for(stage), "concurrency", 1) or 1)
    except Exception:  # noqa: BLE001 - a stage with no resolvable profile stays serial
        return 1
    return min(workers, cap) if cap else workers
