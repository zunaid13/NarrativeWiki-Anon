"""Token and cost accounting, per stage.

Inputs:     Profile + token counts from each completed call.
Outputs:    A per-stage ledger, a printable summary, and a hard stop.
Invariants: - Local calls are recorded with zero cost but real token counts, so `wiki budget`
              shows where GPU time is going as well as where money is going.
            - The hard stop raises BEFORE a call is made, never after money is spent.
Contract:   Written to data/cache/llm/budget.json so `wiki budget` works after the run exits.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import paths
from ..config import Profile


class BudgetExceeded(RuntimeError):
    """Raised when a run would cross `budget.hard_stop_usd`. Stops the run cleanly."""


@dataclass
class StageLedger:
    stage: str
    calls: int = 0
    cached: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    cost_usd: float = 0.0
    models: dict[str, int] = field(default_factory=dict)


class Budget:
    """Thread-safe accumulator. One instance per run, shared by every stage."""

    def __init__(self, warn_usd: float = 5.0, hard_stop_usd: float = 25.0,
                 lifetime_cap_usd: float | None = None, already_spent_usd: float = 0.0) -> None:
        self.warn_usd = warn_usd
        self.hard_stop_usd = hard_stop_usd
        # `hard_stop_usd` is a PER-RUN ceiling and resets every time a process starts, so a
        # sequence of runs is unbounded by it -- 24 runs happened in one day on 2026-09-21, which
        # that ceiling alone would have allowed to cost 24x its value. `lifetime_cap_usd` is the
        # ceiling that does NOT reset: it is checked against this run's spend PLUS every dollar
        # already recorded in data/cache/llm/budget.json. It is the only limit that survives
        # "just re-run it once more". None disables it (the default, so nothing pre-existing
        # changes); set budget.lifetime_cap_usd in config/models.yaml to arm it.
        self.lifetime_cap_usd = lifetime_cap_usd
        self.already_spent_usd = already_spent_usd
        self.stages: dict[str, StageLedger] = {}
        self._lock = threading.Lock()
        self._warned = False

    # -- recording ---------------------------------------------------------

    def record(
        self,
        stage: str,
        profile: Profile,
        in_tokens: int,
        out_tokens: int,
        cached: bool = False,
    ) -> None:
        cost = 0.0 if cached else profile.cost(in_tokens, out_tokens)
        with self._lock:
            ledger = self.stages.setdefault(stage, StageLedger(stage=stage))
            ledger.calls += 1
            if cached:
                ledger.cached += 1
            ledger.in_tokens += in_tokens
            ledger.out_tokens += out_tokens
            ledger.cost_usd += cost
            ledger.models[str(profile)] = ledger.models.get(str(profile), 0) + 1

    @property
    def lifetime_cost(self) -> float:
        """This run's spend plus everything previously recorded. What a bill actually looks like."""
        return self.already_spent_usd + self.total_cost

    def check_before_call(self, stage: str) -> None:
        """Raise if this run, or the lifetime total, has crossed its ceiling. Before spending."""
        if self.lifetime_cap_usd and self.lifetime_cost >= self.lifetime_cap_usd:
            raise BudgetExceeded(
                f"Stopped: ${self.lifetime_cost:.2f} spent in total "
                f"(${self.already_spent_usd:.2f} before this run + ${self.total_cost:.2f} in it), "
                f"lifetime cap is ${self.lifetime_cap_usd:.2f} "
                f"(config/models.yaml -> budget.lifetime_cap_usd). This ceiling does NOT reset "
                f"between runs, which is the point. Completed work is cached, so raising the cap "
                f"and rerunning resumes rather than restarting."
            )
        total = self.total_cost
        if self.hard_stop_usd and total >= self.hard_stop_usd:
            raise BudgetExceeded(
                f"Run stopped: ${total:.2f} spent, hard stop is ${self.hard_stop_usd:.2f} "
                f"(config/models.yaml -> budget.hard_stop_usd). "
                f"Completed work is cached, so rerunning resumes rather than restarting."
            )

    def accumulate(self, other: "Budget") -> None:
        """Merge another run's ledger into this one. Used to keep the default
        data/cache/llm/budget.json a running cumulative total across every run (what `wiki
        budget` shows), while each run's own data/<series>/_runs/<id>/budget.json (see
        provenance.py) holds just that run's own numbers."""
        with self._lock:
            for stage, ledger in other.stages.items():
                acc = self.stages.setdefault(stage, StageLedger(stage=stage))
                acc.calls += ledger.calls
                acc.cached += ledger.cached
                acc.in_tokens += ledger.in_tokens
                acc.out_tokens += ledger.out_tokens
                acc.cost_usd += ledger.cost_usd
                for model, n in ledger.models.items():
                    acc.models[model] = acc.models.get(model, 0) + n

    def should_warn(self) -> bool:
        """True exactly once, the first time the warn threshold is crossed."""
        with self._lock:
            if not self._warned and self.warn_usd and self.total_cost >= self.warn_usd:
                self._warned = True
                return True
        return False

    # -- reading -----------------------------------------------------------

    @property
    def total_cost(self) -> float:
        return sum(s.cost_usd for s in self.stages.values())

    @property
    def total_tokens(self) -> tuple[int, int]:
        return (
            sum(s.in_tokens for s in self.stages.values()),
            sum(s.out_tokens for s in self.stages.values()),
        )

    def summary_rows(self) -> list[tuple[str, str, str, str, str, str]]:
        """Rows for a rich table: stage, calls, cached, in, out, cost."""
        rows = []
        for stage in sorted(self.stages):
            s = self.stages[stage]
            rows.append(
                (
                    stage,
                    f"{s.calls:,}",
                    f"{s.cached:,}",
                    f"{s.in_tokens:,}",
                    f"{s.out_tokens:,}",
                    "free" if s.cost_usd == 0 else f"${s.cost_usd:.2f}",
                )
            )
        return rows

    # -- persistence -------------------------------------------------------

    def save(self, path: Path | None = None) -> None:
        target = path or (paths.LLM_CACHE_DIR / "budget.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "total_cost_usd": round(self.total_cost, 4),
                    "stages": {k: asdict(v) for k, v in self.stages.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Budget":
        target = path or (paths.LLM_CACHE_DIR / "budget.json")
        budget = cls()
        if not target.is_file():
            return budget
        data = json.loads(target.read_text(encoding="utf-8"))
        for name, raw in data.get("stages", {}).items():
            budget.stages[name] = StageLedger(**raw)
        return budget


def lifetime_spent() -> float:
    """[30] Spend across every series: the sum of each `data/<series>/budget.json` (and the
    legacy unsegmented `data/budget.json`), which `provenance.RunContext.finish` accumulates.
    The lifetime cap used to seed from `data/cache/llm/budget.json`, a file nothing writes since
    ledgers went per-series -- so every process started the $20 cap at $0 (measured 2026-09-25:
    $12.79 actually spent)."""
    data = paths.PROJECT_ROOT / "data"
    ledgers = sum(Budget.load(f).total_cost for f in [data / "budget.json", *data.glob("*/budget.json")]
                  if f.is_file())
    # [33] 2026-09-30: budget.json only grows when a run FINISHES; a run that crashes, is killed or
    # hits hard_stop never adds its spend (anne@b1: $16.50 recorded vs $31.55 billed -- B1 t=5's
    # stopped attempts; $17.91 missing project-wide). Every billed call is in its run's calls.jsonl,
    # so the cap takes whichever record is larger. Regex, not json.loads: this runs per client.
    # ponytail: rescans every calls.jsonl (~7 s at 100k rows); cache per finished run if it grows.
    billed = sum(float(m) for f in data.glob("*/_runs/*/calls.jsonl")
                 for m in _COST_RE.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return max(ledgers, billed)


_COST_RE = re.compile(r'"cost_usd":\s*([0-9.eE+-]+)')


def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting and window sizing, with no tokenizer dependency.

    ~4 characters per token is close enough for English prose to size a request window; it is
    used for budget display and for `extraction.window.max_tokens_per_request`, never for
    anything that must be exact.
    """
    return max(1, len(text) // 4)
