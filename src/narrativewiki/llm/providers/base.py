"""Provider interface. Every adapter in this package implements exactly these two functions.

Inputs:     A resolved Profile, a system prompt, a user prompt, and a json_mode flag.
Outputs:    Completion(text, in_tokens, out_tokens).
Invariants: - Adapters do not retry, cache, validate JSON, or account for cost. The client owns
              all of that, so behaviour is identical across providers.
            - Adapters raise ProviderError with a message a user can act on. Nothing else.
            - Imports of optional SDKs happen inside the function, so a missing `anthropic`
              package never breaks a fully local run.
Contract:   Internal to llm/.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderError(RuntimeError):
    """A provider call failed. The message is shown to the user, so make it actionable."""


class ContentBlocked(ProviderError):
    """[30] The provider's safety filter refused the prompt. Deterministic: never retried."""


@dataclass
class Completion:
    text: str
    in_tokens: int = 0
    out_tokens: int = 0


@dataclass
class Embedding:
    """Result of a batch embedding call — one vector per input text, same order (Phase 17)."""

    vectors: list[list[float]]
    in_tokens: int = 0


@dataclass
class Health:
    """Result of a reachability probe, for `wiki doctor`."""

    ok: bool
    detail: str
