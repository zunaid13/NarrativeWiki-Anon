"""Google Gemini adapter (google-genai SDK), Vertex AI only.

Inputs:     Profile with provider=google, use_vertex=True, GOOGLE_CLOUD_PROJECT and
            GOOGLE_CLOUD_LOCATION set (Application Default Credentials).
Outputs:    Completion with usage from response.usage_metadata.
Invariants: - Lazy import; a local-only run never needs the SDK.
            - json_mode uses response_mime_type=application/json, which Gemini enforces.
            - Never the AI Studio API (generativelanguage.googleapis.com, GOOGLE_API_KEY):
              retired 2026-09-28 by the maintainer; a profile without use_vertex is refused.
Contract:   llm/providers/base.py
"""

from __future__ import annotations

import os
from typing import Any

from ...config import Profile
from .base import Completion, Health, ContentBlocked, ProviderError

_FLEX_SERVER_TIMEOUT_S = 1800  # Flex PayGo's documented maximum queueing time
_BLOCK_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY", "RECITATION"}


# One SDK client per (endpoint, credential) rather than one per CALL: constructing it measured
# 0.6s, and the first request on a fresh client another ~1.5s of auth/TLS -- roughly a fifth of
# a short extraction call, paid 700 times a run. Keyed on everything that changes the endpoint.
_CLIENTS: dict[tuple, Any] = {}


def _client(profile: Profile, tier: str | None = None):
    key = (
        tier,
        profile.use_vertex,
        profile.model,
        profile.api_key,
        os.environ.get("GOOGLE_CLOUD_PROJECT"),
        os.environ.get("GOOGLE_CLOUD_LOCATION"),
    )
    if (cached := _CLIENTS.get(key)) is not None:
        return cached
    client = _build_client(profile, tier)
    _CLIENTS[key] = client
    return client


def _build_client(profile: Profile, tier: str | None = None):
    try:
        from google import genai
    except ImportError as exc:
        raise ProviderError(
            "The `google-genai` package is not installed. Either run "
            "`pip install narrativewiki[google]`, or point the routing entry in "
            "config/models.yaml at a local profile."
        ) from exc
    if not profile.use_vertex:
        raise ProviderError(
            f"Profile {profile.name!r} is not a Vertex profile. The AI Studio API "
            "(GOOGLE_API_KEY) is retired; set `use_vertex: true` in config/models.yaml."
        )
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if not project:
        raise ProviderError("GOOGLE_CLOUD_PROJECT is not set (required for use_vertex: true profiles).")
    # [33] `shared_request_type: flex` (profile options) selects Flex PayGo: the same model at
    # half the per-token price, and a request waits for shared capacity instead of failing with
    # 429 at once (docs.cloud.google.com/.../flex-paygo) -- but only for as long as
    # X-Server-Timeout allows (max 30 min); the client-side timeout (ms) must outlast it. Never
    # "priority": that buys capacity at a premium, which the budget rules forbid.
    if tier:
        from google.genai import types
        return genai.Client(vertexai=True, project=project, location=location, http_options=types.HttpOptions(
            headers={"X-Vertex-AI-LLM-Request-Type": "shared", "X-Vertex-AI-LLM-Shared-Request-Type": str(tier),
                     "X-Server-Timeout": str(_FLEX_SERVER_TIMEOUT_S)},
            timeout=(_FLEX_SERVER_TIMEOUT_S + 60) * 1000))
    return genai.Client(vertexai=True, project=project, location=location)


def complete(
    profile: Profile,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    timeout: float = 300.0,
    options: dict[str, Any] | None = None,
) -> Completion:
    merged = {**profile.options, **(options or {})}
    tier = merged.get("shared_request_type")
    client = _client(profile, tier)

    config: dict[str, Any] = {}
    if system:
        config["system_instruction"] = system
    if "temperature" in merged:
        config["temperature"] = float(merged["temperature"])
    if "max_output_tokens" in merged:
        config["max_output_tokens"] = int(merged["max_output_tokens"])
    elif "max_tokens" in merged:
        config["max_output_tokens"] = int(merged["max_tokens"])
    # `num_predict` (Ollama's own vocabulary) is what extract/claims.py and extract/scenes.py
    # actually pass as per-call sizing (Phase 23 D1: a chapter-span "detail" call asking for
    # num_predict=8192 was silently capped to this profile's max_output_tokens=1024 because this
    # adapter never recognized the key at all -- confirmed against a live run's calls.jsonl, 0 of
    # 112 scene_extract detail responses carried a non-empty participant_facts list). Only the
    # CALL SITE'S num_predict overrides the profile default this way, and only when the call site
    # did not also pass a provider-native key of its own (an intentional per-provider override
    # always wins) -- a value baked into profile.options must never be shadowed by this fallback.
    call_options = options or {}
    if (
        "num_predict" in call_options
        and "max_output_tokens" not in call_options
        and "max_tokens" not in call_options
    ):
        config["max_output_tokens"] = int(call_options["num_predict"])
    if json_mode:
        config["response_mime_type"] = "application/json"
    # Gemini 2.5+ "thinks" by default, and thinking tokens are (a) billed at the OUTPUT rate and
    # (b) drawn from the same max_output_tokens pool as the answer -- a 3.x flash model asked for
    # JSON under a 2048-token cap can spend the whole cap thinking and return an empty string.
    # `thinking_budget` in profile.options is therefore a cost AND correctness control: 0 disables
    # it, -1 is dynamic, a positive int caps it. Absent, we leave the model's default alone.
    if "thinking_budget" in merged:
        config["thinking_config"] = {"thinking_budget": int(merged["thinking_budget"])}

    try:
        response = client.models.generate_content(
            model=profile.model, contents=prompt, config=config or None
        )
    except Exception as exc:  # noqa: BLE001
        # [33] Maintainer 2026-10-01: Standard for speed, Flex when Standard answers 429 -- the
        # SAME model and prompt, only the queue changes (Flex waits up to 30 min instead of refusing).
        # ponytail: the ledger still prices this call at the profile's Standard rate, so it
        # over-counts by half (safe for the cap); pass the tier back if exact cost ever matters.
        if not (merged.get("flex_on_429") and not tier and _is_429(exc)):
            raise ProviderError(f"Gemini call failed ({type(exc).__name__}): {exc}") from exc
        try:
            response = _client(profile, "flex").models.generate_content(
                model=profile.model, contents=prompt, config=config or None
            )
        except Exception as exc2:  # noqa: BLE001
            raise ProviderError(f"Gemini call failed on Standard (429) and Flex "
                                f"({type(exc2).__name__}): {exc2}") from exc2

    usage = getattr(response, "usage_metadata", None)
    text = getattr(response, "text", "") or ""
    # `candidates_token_count` EXCLUDES thinking tokens, which Google bills at the output rate.
    # Leaving them out made llm/budget.py under-report spend on exactly the models that think
    # most -- so they are added here, where the number comes from.
    out = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    if not text:
        candidates = getattr(response, "candidates", None) or []
        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        feedback = getattr(response, "prompt_feedback", None)
        block = getattr(feedback, "block_reason", None)
        if block or str(reason).split(".")[-1] in _BLOCK_REASONS:
            # [30] A safety filter refused the prompt: no candidates, or a SAFETY-class finish.
            # Deterministic, so retrying is pointless; the message says what refused it.
            raise ContentBlocked(
                f"Gemini blocked the prompt (block_reason={block}, finish_reason={reason}, "
                f"ratings={getattr(feedback, 'safety_ratings', None)})"
            )
        raise ProviderError(
            f"Gemini returned no text (finish_reason={reason}). If this is MAX_TOKENS, the "
            f"answer was crowded out by thinking: raise max_output_tokens or set "
            f"`thinking_budget: 0` on profile {profile.name!r} in config/models.yaml."
        )
    return Completion(
        text=text,
        in_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
        out_tokens=out,
    )


def _is_429(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "resource has been exhausted" in text


def health(profile: Profile, timeout: float = 5.0) -> Health:
    try:
        from google import genai  # noqa: F401
    except ImportError:
        return Health(False, "package not installed (pip install narrativewiki[google])")
    if not profile.use_vertex:
        return Health(False, "not a Vertex profile — the AI Studio API is retired")
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return Health(False, "GOOGLE_CLOUD_PROJECT not set — Vertex profile unusable")
    return Health(True, f"{profile.model} (Vertex, project set)")
