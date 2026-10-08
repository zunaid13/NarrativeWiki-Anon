"""Anthropic adapter — used only for final prose polish and contradiction arbitration.

Inputs:     Profile with provider=anthropic and ANTHROPIC_API_KEY set.
Outputs:    Completion with exact usage counts from the API.
Invariants: - The SDK is imported lazily, so a fully local run never needs it installed.
            - JSON mode is prompt-instructed rather than schema-constrained; the client
              validates and repairs, identically for every provider.
Contract:   llm/providers/base.py
"""

from __future__ import annotations

from typing import Any

from ...config import Profile
from .base import Completion, Health, ProviderError

_JSON_SUFFIX = (
    "\n\nRespond with a single valid JSON object and nothing else. "
    "No prose, no markdown fences."
)


def _client(profile: Profile):
    try:
        import anthropic
    except ImportError as exc:
        raise ProviderError(
            "The `anthropic` package is not installed. Either run "
            "`pip install narrativewiki[anthropic]`, or point the routing entry in "
            "config/models.yaml at a local profile."
        ) from exc
    if not profile.api_key:
        raise ProviderError("ANTHROPIC_API_KEY is not set (see .env.example).")
    return anthropic.Anthropic(api_key=profile.api_key)


def complete(
    profile: Profile,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    timeout: float = 300.0,
    options: dict[str, Any] | None = None,
) -> Completion:
    client = _client(profile)
    merged = {**profile.options, **(options or {})}

    # `num_predict` (Ollama's own vocabulary) is what extract/claims.py and extract/scenes.py
    # actually pass as per-call sizing -- see google.py's identical fallback (Phase 23 D1) for the
    # live evidence this silently truncates a call that gets routed here without it. Only the
    # CALL SITE'S num_predict overrides the profile default, and only when the call site did not
    # also pass max_tokens itself (an intentional per-provider override always wins).
    call_options = options or {}
    if "num_predict" in call_options and "max_tokens" not in call_options:
        max_tokens = int(call_options["num_predict"])
    else:
        max_tokens = int(merged.get("max_tokens", 2048))

    kwargs: dict[str, Any] = {
        "model": profile.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt + (_JSON_SUFFIX if json_mode else "")}],
    }
    if system:
        kwargs["system"] = system
    if "temperature" in merged:
        kwargs["temperature"] = float(merged["temperature"])

    try:
        response = client.with_options(timeout=timeout).messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user with context
        raise ProviderError(f"Anthropic call failed ({type(exc).__name__}): {exc}") from exc

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    usage = getattr(response, "usage", None)
    return Completion(
        text=text,
        in_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        out_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def health(profile: Profile, timeout: float = 5.0) -> Health:
    """Key-presence check only — deliberately does not spend a token to verify."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return Health(False, "package not installed (pip install narrativewiki[anthropic])")
    if not profile.api_key:
        return Health(False, "ANTHROPIC_API_KEY not set — will fall back to local")
    return Health(True, f"{profile.model} key present")
