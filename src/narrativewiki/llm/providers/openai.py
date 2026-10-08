"""OpenAI-compatible adapter — covers OpenAI, OpenRouter, and any /chat/completions endpoint.

Inputs:     Profile with provider=openai, a base_url, and the matching api_key_env set.
Outputs:    Completion with usage from the response body.
Invariants: - Raw HTTP via httpx rather than the `openai` SDK, so one adapter serves every
              OpenAI-compatible host and the package list stays small.
            - json_mode requests response_format=json_object; hosts that ignore it still work
              because the client validates and repairs afterwards.
Contract:   llm/providers/base.py
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import Profile
from .base import Completion, Health, ProviderError


def complete(
    profile: Profile,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    timeout: float = 300.0,
    options: dict[str, Any] | None = None,
) -> Completion:
    if not profile.api_key:
        raise ProviderError(
            f"No API key for profile {profile.name!r}. Set the key named by `api_key_env` "
            f"in config/models.yaml (see .env.example)."
        )
    if not profile.base_url:
        raise ProviderError(f"Profile {profile.name!r} has no base_url.")

    merged = {**profile.options, **(options or {})}
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {"model": profile.model, "messages": messages}
    if "temperature" in merged:
        payload["temperature"] = float(merged["temperature"])
    if "max_tokens" in merged:
        payload["max_tokens"] = int(merged["max_tokens"])
    # `num_predict` (Ollama's own vocabulary) is what extract/claims.py and extract/scenes.py
    # actually pass as per-call sizing -- see google.py's identical fallback (Phase 23 D1) for the
    # live evidence this silently truncates a call that gets routed here without it. Only the
    # CALL SITE'S num_predict overrides the profile default, and only when the call site did not
    # also pass max_tokens itself (an intentional per-provider override always wins).
    call_options = options or {}
    if "num_predict" in call_options and "max_tokens" not in call_options:
        payload["max_tokens"] = int(call_options["num_predict"])
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # OpenRouter's unified `reasoning` param (see profile.options on an openrouter_free-style
    # profile) suppresses chain-of-thought narration even on models with no native way to turn it
    # off -- without it, a reasoning-style free model spends its whole `max_tokens` narrating
    # ("We need to parse the passage...") and never emits the JSON payload the client is waiting
    # for (Phase 23 Part F, live 2026-09-11). Harmless passthrough for hosts that ignore it.
    if "reasoning" in merged:
        payload["reasoning"] = merged["reasoning"]

    url = f"{profile.base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {profile.api_key}",
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Request to {url} failed: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(f"{url} returned {response.status_code}: {response.text[:400]}")

    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Unexpected response shape from {url}: {str(data)[:400]}") from exc

    usage = data.get("usage") or {}
    return Completion(
        text=text,
        in_tokens=int(usage.get("prompt_tokens", 0) or 0),
        out_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


def health(profile: Profile, timeout: float = 5.0) -> Health:
    if not profile.api_key:
        return Health(False, "API key not set — will fall back to local")
    return Health(True, f"{profile.model} key present")
