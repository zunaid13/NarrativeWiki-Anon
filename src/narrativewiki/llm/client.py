"""The single LLM interface. Nothing outside this package calls a model directly.

Inputs:     Settings (for routing + client config), a stage name, and a prompt.
Outputs:    Text, or a validated Pydantic model via `complete_json`.
Invariants: - Callers name a STAGE, never a model. Routing, key-fallback, cost and caching are
              all resolved here, so swapping providers is a config edit.
            - Every call is cached on the exact request, so reruns after a crash are free and
              a prompt edit to a late stage costs nothing for stages already done.
            - `complete_json` never returns unvalidated data. On a schema failure it re-asks
              with the validation error attached, up to `json_repair_attempts`, then raises.
            - `embed` (Phase 17) is a separate, lighter path: per-text disk-cached, no budget or
              frontier-scope check (the only role ever routed there is the local `embed` profile),
              one compact run-log line per batch call instead of one per text.
            - When constructed with `run=<a provenance.RunContext>`, every call (cache hit or
              miss) is appended to that run's `calls.jsonl` with its full prompt, response, and a
              timestamp (Phase 10 -- see provenance.py). Without a `run`, behaviour is unchanged
              from before Phase 10; this keeps every existing direct-construction test working.
            - A `tier: frontier` profile (config/models.yaml) is refused outright once the run's
              `volume_scope` exceeds the profile's `max_volumes`, or once the run has already
              made `max_calls_per_run` frontier calls -- enforced on every miss, before the
              provider is ever contacted, in `_enforce_frontier_scope`.
Contract:   docs/PROMPTS.md documents the prompts; this module documents the mechanism.
"""

from __future__ import annotations

import json
import os
import re
import random
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from .. import paths
from ..config import Profile, Settings
from .budget import Budget, lifetime_spent, estimate_tokens
from .cache import CachedResponse, LLMCache, make_key
from .providers import anthropic as anthropic_provider
from .providers import google as google_provider
from .providers import ollama as ollama_provider
from .providers import openai as openai_provider
from .providers import minicheck as minicheck_provider
from .providers.base import Completion, ContentBlocked, Health, ProviderError

if TYPE_CHECKING:
    from ..provenance import RunContext

_PROVIDERS = {
    "ollama": ollama_provider,
    "anthropic": anthropic_provider,
    "google": google_provider,
    "openai": openai_provider,
    "minicheck": minicheck_provider,
}

T = TypeVar("T", bound=BaseModel)

# Models wrap JSON in fences more often than they should, especially at 14B.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    """A call failed after every retry and repair attempt."""


class ContentRefused(LLMError):
    """[33] The provider's content filter refused the prompt, and the same-model rule forbids
    answering it with a different model. `complete_json` returns the schema's empty answer for it
    when the schema has one (e.g. `ExtractionResult()` = no facts); otherwise the stage sees this."""


class FrontierScopeExceeded(LLMError):
    """A tier: frontier profile was called outside its configured scope (config/models.yaml
    `max_volumes`/`max_calls_per_run`). Subclasses LLMError on purpose -- every existing
    `except LLMError` call site in cli.py already prints a clear error and exits 1, which is
    exactly the right behaviour here too, with no new exception handling needed anywhere."""


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        budget: Budget | None = None,
        cache: LLMCache | None = None,
        run: "RunContext | None" = None,
        volume_scope: int | None = None,
    ) -> None:
        self.settings = settings
        cfg = settings.client_config
        bcfg = settings.budget_config

        # `hard_stop_usd` is per-run and resets on every process start, so it cannot bound a
        # SEQUENCE of runs -- the realistic way a bill runs away is many re-runs, each individually
        # under the ceiling. `lifetime_cap_usd` is seeded with everything already recorded in
        # data/cache/llm/budget.json, so it survives re-running and process restarts.
        lifetime_cap = bcfg.get("lifetime_cap_usd")
        already = 0.0
        if lifetime_cap is not None:
            try:
                # [33] + what the provider billed that no ledger saw (killed runs never reach
                # finish(); pricing drift). Set from the maintainer's billing console.
                already = lifetime_spent() + float(bcfg.get("billed_offset_usd", 0.0))
            except (OSError, ValueError, KeyError):
                already = 0.0   # an unreadable ledger must not silently disable the cap below
        self.budget = budget or Budget(
            warn_usd=float(bcfg.get("warn_usd", 5.0)),
            hard_stop_usd=float(bcfg.get("hard_stop_usd", 25.0)),
            lifetime_cap_usd=None if lifetime_cap is None else float(lifetime_cap),
            already_spent_usd=already,
        )
        self.cache = cache or LLMCache(
            directory=paths.PROJECT_ROOT / cfg.get("cache_dir", "data/cache/llm"),
            enabled=bool(cfg.get("cache_enabled", True)),
        )
        self.max_retries = int(cfg.get("max_retries", 3))
        self.backoff = float(cfg.get("retry_backoff_seconds", 2.0))
        # A provider rate limit is not the same failure as a flaky connection: the fix is to wait
        # long enough for the window to reopen, not to retry in 2 seconds. Vertex's shared quota
        # answers 429 under a burst of parallel calls and recovers within a minute (measured
        # 2026-09-23 at concurrency 8), so throttled attempts get their own, much longer, ladder.
        self.rate_limit_backoff = float(cfg.get("rate_limit_backoff_seconds", 20.0))
        self.rate_limit_retries = int(cfg.get("rate_limit_retries", 8))
        self.timeout = float(cfg.get("request_timeout_seconds", 300))
        self.repair_attempts = int(cfg.get("json_repair_attempts", 2))
        self.log_prompts = bool(cfg.get("log_prompts", False))
        # Phase 10: the run this client's calls belong to (per-call provenance log), and the
        # highest volume number in play for this invocation (the frontier scope interlock).
        # Both optional and None by default, so every pre-Phase-10 direct construction (tests
        # included) is unaffected.
        self.run = run
        self.volume_scope = volume_scope
        if run is not None:
            run.attach_budget(self.budget)

    # -- public API --------------------------------------------------------

    def score_support(self, documents: list[str], claim: str, stage: str = "support") -> dict[str, Any]:
        """Cached, logged classifier probabilities; never accepts chat-generated scores."""
        profile = self.settings.resolve_role(stage)
        if profile.provider != "minicheck":
            raise LLMError(f"[{stage}] support scoring requires a MiniCheck classifier profile")
        prompt = json.dumps({"documents": documents, "claim": claim}, ensure_ascii=False, sort_keys=True)
        result = self._call(stage, profile, prompt, None, True, {"scoring_version": 1})
        try:
            data = json.loads(result.text)
            import math
            scores = [data["score"], *[c["score"] for c in data["chunks"]]]
            if not data["chunks"] or any(isinstance(s, bool) or not isinstance(s, (float, int))
                                         or not math.isfinite(s) or not 0 <= s <= 1 for s in scores):
                raise ValueError("invalid support probabilities")
            if data["score"] != max(c["score"] for c in data["chunks"]):
                raise ValueError("support score does not match its chunks")
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError(f"Invalid cached/provider support score: {exc}") from exc
        return {**data, "model": str(profile), "revision": profile.options.get("revision")}

    def complete(
        self,
        stage: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Run one completion for a pipeline stage and return its text."""
        profile = self.settings.resolve_role(stage)
        return self._call(stage, profile, prompt, system, json_mode, options).text

    def complete_json(
        self,
        stage: str,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> T:
        """Run a completion and validate it against a Pydantic model.

        On a parse or validation failure the model is re-asked with the specific error, which
        recovers the large majority of 14B JSON mistakes without a bigger model.
        """
        profile = self.settings.resolve_role(stage)
        attempt_prompt = prompt
        last_error = ""

        for attempt in range(self.repair_attempts + 1):
            try:
                completion = self._call(
                    stage, profile, attempt_prompt, system, json_mode=True, options=options
                )
            except ContentRefused as refused:
                try:  # [33] a refused passage yields no facts, never another model's facts
                    return schema.model_validate({})
                except ValidationError:
                    raise refused from None

            try:
                return schema.model_validate(_parse_json(completion.text))
            except (ValueError, ValidationError) as exc:
                last_error = _short_error(exc)
                if attempt == self.repair_attempts:
                    break
                attempt_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous response was rejected:\n{completion.text[:1200]}\n\n"
                    f"Error: {last_error}\n\n"
                    f"Return corrected JSON matching the schema exactly. JSON only."
                )

        raise LLMError(
            f"[{stage}] {profile} did not produce valid {schema.__name__} after "
            f"{self.repair_attempts + 1} attempts. Last error: {last_error}"
        )

    def embed(self, stage: str, texts: list[str]) -> list[list[float]]:
        """Batch embedding, cached per-text (Phase 17 — value canonicalization). Unlike
        `complete`/`complete_json`, this never touches budget or the frontier-scope interlock:
        the only role ever routed here is the local, free `embed` profile (bge-m3), by
        construction, so there is nothing to meter. A resolved profile whose provider has no
        `embed()` function, or whose call fails (e.g. Ollama unreachable), raises LLMError naming
        the stage — same `ProviderError -> LLMError` translation `_call` does, so a caller only
        ever needs to catch one exception type regardless of provider. Never a silent
        empty-vector fallback; graph/contradictions.py is what degrades gracefully on catching it.
        """
        if not texts:
            return []
        profile = self.settings.resolve_role(stage)
        provider = _PROVIDERS.get(profile.provider)
        if provider is None or not hasattr(provider, "embed"):
            raise LLMError(
                f"[{stage}] {profile}: provider {profile.provider!r} has no embedding support."
            )

        keys = [make_key(profile.provider, profile.model, None, text, {"_embed": True}) for text in texts]
        vectors: list[list[float] | None] = [None] * len(texts)
        hits = 0
        misses: list[int] = []
        for i, key in enumerate(keys):
            if hit := self.cache.get(key):
                vectors[i] = json.loads(hit.text)
                hits += 1
            else:
                misses.append(i)

        new_tokens = 0
        if misses:
            try:
                results = [(misses, provider.embed(profile, [texts[i] for i in misses], timeout=self.timeout))]
            except ProviderError as exc:
                # [30] One text can make bge-m3 emit NaN ("failed to encode response"), failing
                # its whole batch; the caller then switched canonicalization off for the rest of
                # the run (Bookworm: 3 embed batches where Overlord made 76). Retry one text at a
                # time; a text that NaNs on its own keeps a None vector (clustered alone). Any
                # other failure -- the model unreachable -- still raises.
                if "NaN" not in str(exc):
                    raise LLMError(f"[{stage}] {profile}: {exc}") from exc
                results = []
                for i in misses:
                    try:
                        results.append(([i], provider.embed(profile, [texts[i]], timeout=self.timeout)))
                    except ProviderError as one:
                        if "NaN" not in str(one):
                            raise LLMError(f"[{stage}] {profile}: {one}") from one
            new_tokens = sum(result.in_tokens for _, result in results)
            per_text_tokens = new_tokens // len(misses) if new_tokens else 0
            for pos, vector in ((p, v) for idxs, result in results for p, v in zip(idxs, result.vectors)):
                vectors[pos] = vector
                self.cache.put(
                    keys[pos],
                    CachedResponse(
                        json.dumps(vector), per_text_tokens, 0,
                        stage=stage, provider=profile.provider, model=profile.model,
                        system="", prompt=texts[pos],
                    ),
                )

        if self.run is not None:
            self.run.log_call(
                {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stage": stage,
                    "role": profile.name,
                    "provider": profile.provider,
                    "model": profile.model,
                    "tier": profile.tier,
                    "cache_key": f"batch:{len(texts)}",
                    "cache_hit": not misses,
                    "system": "",
                    "prompt": f"<{len(texts)} text(s) to embed, {hits} cache hit(s)>",
                    "response": f"<{len(texts)} vector(s)>",
                    "in_tokens": new_tokens,
                    "out_tokens": 0,
                    "cost_usd": 0.0,
                    "attempt": 0,
                }
            )
        return vectors  # type: ignore[return-value]

    def health(self, role: str) -> Health:
        """Reachability probe for `wiki doctor`. Never raises."""
        try:
            profile = self.settings._build_profile(role)  # noqa: SLF001 - doctor is in-family
        except Exception as exc:  # noqa: BLE001
            return Health(False, str(exc))
        return self.health_profile(profile)

    def health_profile(self, profile: Profile) -> Health:
        """Same probe as `health`, but for an already-resolved Profile -- the path a `--model`
        ad-hoc override needs, since it has no role name `_build_profile` could look up."""
        provider = _PROVIDERS.get(profile.provider)
        if provider is None:
            return Health(False, f"unknown provider {profile.provider!r}")
        return provider.health(profile)

    def profile_for(self, stage: str) -> Profile:
        """Which model a stage would actually use right now, after key fallback."""
        return self.settings.resolve_role(stage)

    # -- internals ---------------------------------------------------------

    def _call(
        self,
        stage: str,
        profile: Profile,
        prompt: str,
        system: str | None,
        json_mode: bool,
        options: dict[str, Any] | None,
    ) -> Completion:
        merged_options = {**profile.options, **(options or {}), "_json": json_mode}
        key = make_key(profile.provider, profile.model, system, prompt, merged_options)
        call_ts = time.time()

        if hit := self.cache.get(key):
            self.budget.record(stage, profile, hit.in_tokens, hit.out_tokens, cached=True)
            self._log_call(
                stage, profile, key, system, prompt, hit.text,
                hit.in_tokens, hit.out_tokens, cached=True, ts=call_ts, attempt=0,
            )
            return Completion(hit.text, hit.in_tokens, hit.out_tokens)

        # [30] Replay mode: re-run a stage over stored responses (a parser or gate change) with a
        # guarantee that nothing bills -- one drifted prompt would otherwise re-roll that call's
        # facts silently, which is how MEASUREMENTS section 30 lost two gold facts.
        if os.environ.get("NARRATIVEWIKI_CACHE_ONLY"):
            raise LLMError(f"[{stage}] {profile}: cache miss with NARRATIVEWIKI_CACHE_ONLY set; nothing was sent.")

        if profile.tier == "frontier":
            self._enforce_frontier_scope(profile)

        if not profile.is_local:
            self.budget.check_before_call(stage)

        provider = _PROVIDERS.get(profile.provider)
        if provider is None:
            raise LLMError(f"Unknown provider {profile.provider!r} for profile {profile.name!r}")

        # Two separate retry budgets. `max_retries` covers real failures, where more attempts
        # mostly waste time. A provider rate limit is not a failure -- it is "not yet" -- and a
        # busy hour can outlast three tries, so throttled attempts get their own allowance and
        # their own (much longer) waits. Without this, a stage crashed mid-volume and had to be
        # restarted by hand, which the cache makes cheap but nobody enjoys at 2am.
        attempt = 0
        failures = 0
        throttles = 0
        while True:
            try:
                completion = provider.complete(
                    profile,
                    prompt,
                    system=system,
                    json_mode=json_mode,
                    timeout=self.timeout,
                    options=options,
                )
                break
            except ProviderError as exc:
                throttled = _is_rate_limited(exc)
                if throttled:
                    throttles += 1
                    # [33] Exponential with jitter (Google's advice for 429): 60s, 120s, 240s, 480s.
                    delay = self.rate_limit_backoff * 2 ** (throttles - 1) * random.uniform(0.8, 1.2)
                    spent = throttles > self.rate_limit_retries
                else:
                    failures += 1
                    delay = self.backoff * (2 ** (failures - 1))
                    spent = failures > self.max_retries
                if spent or _is_fatal(exc) or isinstance(exc, ContentBlocked):
                    self._log_call(
                        stage, profile, key, system, prompt, "", 0, 0,
                        cached=False, ts=call_ts, attempt=attempt, error=str(exc),
                    )
                    if isinstance(exc, ContentBlocked):
                        # [33] Same model for every compared number (maintainer, 2026-09-28): a
                        # refusal is NOT answered by another model unless
                        # client.content_block_fallback is true; complete_json turns it into the
                        # schema's empty answer. [30]'s next-rung fallback stays behind the flag.
                        if not self.settings.client_config.get("content_block_fallback", False):
                            raise ContentRefused(f"[{stage}] {profile}: {exc}") from exc
                        for fallback in self.settings.fallbacks_after(stage, profile):
                            try:
                                return self._call(stage, fallback, prompt, system, json_mode, options)
                            except LLMError:
                                continue
                    raise LLMError(f"[{stage}] {profile}: {exc}") from exc
                attempt += 1
                time.sleep(delay)

        # Some hosts return no usage; estimate so budget reports stay meaningful.
        if not completion.in_tokens:
            completion.in_tokens = estimate_tokens((system or "") + prompt)
        if not completion.out_tokens:
            completion.out_tokens = estimate_tokens(completion.text)

        self.cache.put(
            key,
            CachedResponse(
                completion.text, completion.in_tokens, completion.out_tokens,
                stage=stage, provider=profile.provider, model=profile.model,
                system=system or "", prompt=prompt,
            ),
        )
        self.budget.record(stage, profile, completion.in_tokens, completion.out_tokens)
        self._log_call(
            stage, profile, key, system, prompt, completion.text,
            completion.in_tokens, completion.out_tokens, cached=False, ts=call_ts, attempt=attempt,
        )
        if self.log_prompts:
            self._log(stage, profile, prompt, completion.text)
        return completion

    def _enforce_frontier_scope(self, profile: Profile) -> None:
        """The user's operating rule: a frontier-tier model must never be reachable outside a
        narrow, explicitly-scoped run. Checked on every cache MISS only -- a cache hit costs no
        money and reveals no new spend, so it is never blocked (blocking it would just make a
        previously-legitimate, already-cached answer unreadable later for no safety benefit)."""
        if (
            profile.max_volumes is not None
            and self.volume_scope is not None
            and self.volume_scope > profile.max_volumes
        ):
            raise FrontierScopeExceeded(
                f"{profile} is a frontier-tier model capped at {profile.max_volumes} volume(s) "
                f"(config/models.yaml `max_volumes`). This run's scope reaches volume "
                f"{self.volume_scope}. Narrow --volumes/--upto, or route this stage at a "
                f"non-frontier role."
            )
        if profile.max_calls_per_run is not None and self.run is not None:
            made = self.run.frontier_call_count()
            if made >= profile.max_calls_per_run:
                raise FrontierScopeExceeded(
                    f"{profile} has already made {made} frontier-tier call(s) this run, at its "
                    f"configured limit of {profile.max_calls_per_run} (config/models.yaml "
                    f"`max_calls_per_run`). Start a new run, or route this stage at a "
                    f"non-frontier role."
                )

    def _log_call(
        self,
        stage: str,
        profile: Profile,
        key: str,
        system: str | None,
        prompt: str,
        response: str,
        in_tokens: int,
        out_tokens: int,
        *,
        cached: bool,
        ts: float,
        attempt: int,
        error: str | None = None,
    ) -> None:
        """Append one call to the active run's calls.jsonl (Phase 10). A no-op when this client
        was constructed without a `run` -- every pre-Phase-10 direct construction still works."""
        if self.run is None:
            return
        record = {
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds"),
            "stage": stage,
            "role": profile.name,
            "provider": profile.provider,
            "model": profile.model,
            "tier": profile.tier,
            "cache_key": key,
            "cache_hit": cached,
            "system": system or "",
            "prompt": prompt,
            "response": response,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cost_usd": 0.0 if cached else profile.cost(in_tokens, out_tokens),
            "attempt": attempt,
        }
        if error:
            record["error"] = error
        self.run.log_call(record)

    def _log(self, stage: str, profile: Profile, prompt: str, response: str) -> None:
        log_path = self.cache.dir / "prompts.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n{'=' * 78}\n[{stage}] {profile}\n{'-' * 78}\n"
                f"{prompt}\n{'-' * 78}\n{response}\n"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response.

    Handles the three things small models actually do wrong: markdown fences, a leading
    apology sentence, and trailing commentary after the closing brace.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty response")

    if match := _FENCE.search(text):
        text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost balanced {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    if (repaired := _escape_stray_quotes(text)) is not None:
        return repaired

    if (salvaged := _salvage_truncated_array(text)) is not None:
        return salvaged

    raise ValueError(f"no parsable JSON in response ({_json_error_hint(text)}): {text[:200]!r}")


def _escape_stray_quotes(text: str, limit: int = 20) -> Any | None:
    """[33] Repair an unescaped double quote copied into a string value from the source text.

    3.6 Flash quoted Anne v3's `"...And, oh, Anne"—"I don't want to die..."` verbatim, inner quotes
    and all, and a deterministic re-ask reproduced it twice (MEASUREMENTS 2026-09-28). The decoder
    then stops with "Expecting ',' / ':' delimiter" just past the stray quote: escape the last quote
    before that position and try again, up to `limit` times. Whatever comes out must still pass the
    caller's schema validation, so a wrong repair cannot slip through."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    body = text[start : end + 1]
    for _ in range(limit):
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            if not exc.msg.startswith(("Expecting ',' delimiter", "Expecting ':' delimiter")):
                return None
            q = body.rfind('"', 0, exc.pos)
            if q <= 0 or body[q - 1] == "\\":
                return None
            body = body[:q] + '\\"' + body[q + 1 :]
    return None


def _json_error_hint(text: str) -> str:
    """Where and why the JSON is invalid, for the repair prompt (a bare "no parsable JSON" let
    the model resend the same answer)."""
    start = text.find("{")
    try:
        json.loads(text[start:] if start != -1 else text)
    except json.JSONDecodeError as exc:
        near = exc.doc[max(0, exc.pos - 60) : exc.pos + 20]
        return (f"invalid JSON at character {exc.pos}: {exc.msg}, near {near!r}; "
                "a double quote inside a string value must be written as \\\"")
    return "no JSON object found"


def _salvage_truncated_array(text: str) -> Any | None:
    """Recover `{"key": [ {...}, {...}, <cut off mid-element>` by trimming back to the last
    complete array element and closing the structure, instead of losing the whole response.

    A response can be cut off mid-object two ways: it genuinely hit `num_predict`, or (observed
    against real Ollama output on a fact-dense window) the model fell into a degenerate loop
    re-emitting the same handful of elements until the token budget ran out mid-repeat. Either
    way the elements *before* the cut are usually still complete and worth keeping rather than
    discarding the entire call and burning a repair-retry on a prompt likely to loop again.
    Returns None (never a guess) unless the trimmed candidate itself parses as valid JSON.
    """
    obj_start = text.find("{")
    if obj_start == -1:
        return None
    arr_start = text.find("[", obj_start)
    if arr_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    last_complete_end = -1
    for i in range(arr_start + 1, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0 and ch == "}":
                last_complete_end = i

    if last_complete_end == -1:
        return None

    candidate = text[obj_start:arr_start] + text[arr_start : last_complete_end + 1] + "]}"
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    global _TRUNCATION_SALVAGE_COUNT
    _TRUNCATION_SALVAGE_COUNT += 1  # yield-loss counter (plan Stage 0.2) -- see pop_truncation_salvage_count
    return parsed


# Single-threaded pipeline (no concurrent callers), so a module-level counter is safe here the
# same way extract/schema.py's `_INCOMPLETE_FACT_DROPS` is. This fires whenever a response was
# genuinely truncated (hit `num_predict` or looped) and everything after the cut was discarded --
# previously invisible; a caller reads it with `pop_truncation_salvage_count()` per stage run.
_TRUNCATION_SALVAGE_COUNT = 0


def pop_truncation_salvage_count() -> int:
    global _TRUNCATION_SALVAGE_COUNT
    count, _TRUNCATION_SALVAGE_COUNT = _TRUNCATION_SALVAGE_COUNT, 0
    return count


def _short_error(exc: Exception) -> str:
    """A compact, model-readable description of what was wrong."""
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()[:5]
        ]
        return "; ".join(parts)
    return str(exc)[:300]


def _is_rate_limited(exc: ProviderError) -> bool:
    """A provider saying "too fast" or "out of quota for now", as opposed to a real failure.
    Worth waiting out: every one of these seen in practice cleared within a minute."""
    message = str(exc).lower()
    return any(
        token in message
        for token in ("429", "rate limit", "resource_exhausted", "resource has been exhausted",
                      "too many requests", "quota")
    )


def _is_fatal(exc: ProviderError) -> bool:
    """Retrying a missing model or a missing API key only wastes the user's time."""
    message = str(exc).lower()
    return any(
        token in message
        for token in ("not pulled", "api key", "not installed", "does not have the model")
    )
