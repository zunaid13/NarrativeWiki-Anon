"""Typed configuration: loads config/*.yaml plus .env into one Settings object.

Inputs:     config/series.<id>.yaml, config/models.yaml, config/extraction.yaml, .env
Outputs:    Settings — the only object the rest of the pipeline reads configuration from.
Invariants: - No module hard-codes a model name, a character name, or an EPUB quirk.
            - `Settings.resolve_role(stage)` is the ONLY way to pick a model. It applies the
              routing table and silently falls back to a local profile when an API key is
              absent, which is what lets the pipeline run with no keys at all.
            - Volume numbers are 1-indexed ints everywhere, derived here from filenames once.
Contract:   config/*.yaml are documented inline; see also STRUCTURE.md.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import paths

try:  # python-dotenv is a declared dependency, but never fail hard on a missing .env
    from dotenv import load_dotenv

    load_dotenv(paths.PROJECT_ROOT / ".env")
except Exception:  # pragma: no cover - environment-dependent
    pass


DEFAULT_SERIES = paths.DEFAULT_SERIES


class ConfigError(RuntimeError):
    """Raised for a configuration problem the user must fix, with an actionable message."""


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Volume:
    """One source book. `vol` is the 1-indexed number used everywhere downstream."""

    vol: int
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name


# ---------------------------------------------------------------------------
# Model profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A resolved model endpoint: provider, model id, options, and pricing."""

    name: str
    provider: str
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    api_key: str | None = None
    base_url: str | None = None
    price_in: float = 0.0
    price_out: float = 0.0
    concurrency: int = 1
    # google provider only: True routes through Vertex AI (a GCP project/location) instead of
    # the AI Studio api_key endpoint. See llm/providers/google.py and config/models.yaml's
    # commented-out `vertex_frontier` profile for the intended use.
    use_vertex: bool = False
    # "standard" (default) or "frontier". A frontier-tier profile is subject to the scope
    # interlock in llm/client.py::LLMClient._enforce_frontier_scope -- the user's operating rule
    # that a paid frontier model must never be reachable outside a narrow, explicitly-scoped run.
    # Local and free-tier profiles never set this; it costs nothing to leave at the default.
    tier: str = "standard"
    # Only enforced when tier == "frontier". `max_volumes`: the call is refused once the run's
    # own volume scope (the highest volume number in play for this invocation) exceeds this.
    # `max_calls_per_run`: the call is refused once this many frontier calls have already been
    # made in the current run (see provenance.py's calls.jsonl, which is what counts them).
    max_volumes: int | None = None
    max_calls_per_run: int | None = None

    @property
    def is_local(self) -> bool:
        return self.provider in {"ollama", "minicheck"}

    @property
    def is_callable(self) -> bool:
        """True when this profile has whatever credential it needs to be called right now.

        Vertex profiles authenticate with Application Default Credentials and a GCP project,
        never an api_key -- without this, `resolve_role`'s "no key, use the fallback" rule
        silently routed every Vertex stage to its free-tier fallback (2026-09-23).
        """
        if self.is_local:
            return True
        if self.use_vertex:
            return bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
        return bool(self.api_key)

    def cost(self, in_tokens: int, out_tokens: int) -> float:
        """USD for a call of this size. Local profiles price at zero."""
        return (in_tokens / 1_000_000) * self.price_in + (out_tokens / 1_000_000) * self.price_out

    def __str__(self) -> str:  # shown in reports and page provenance
        return f"{self.provider}:{self.model}"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    series_id: str
    series: dict[str, Any]
    models: dict[str, Any]
    extraction: dict[str, Any]
    # Stage -> role name (or "*" for every stage), applied on top of `routing` in resolve_role.
    # Set post-construction by the CLI's --model flag; never read from a YAML file. An explicit
    # override bypasses the normal key-absent fallback -- naming a model and having it silently
    # swapped for something else would be worse than a clear ConfigError.
    model_overrides: dict[str, str] = field(default_factory=dict)
    # Stage -> a Profile built ad-hoc from a "provider:model" CLI override, cloning a base
    # profile's options/pricing/api_key/base_url (see parse_model_override). Consulted before
    # `model_overrides`, since an ad-hoc profile is not a role name `_build_profile` can look up.
    adhoc_profiles: dict[str, "Profile"] = field(default_factory=dict)

    # -- CLI schema tuning (VISION.md 2026-09-04) ---------------------------
    # `--attr`/`--trait`/`--relation`: kind -> {NAME: predicate config}, added or overriding a
    # base/overlay predicate for THIS PROCESS only -- never written back to any YAML file. Same
    # "try it ad hoc, promote to config once it's proven" ergonomics as `adhoc_profiles` above.
    schema_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # `--only`/`--skip`: predicate names (upper-cased) and/or kind words ("attribute", "relation",
    # "trait", lower-cased) that restrict what `attributes`/`relations`/`traits` return. Empty
    # sets mean "no restriction" -- the common case, checked with a plain `if self.schema_only`.
    schema_only: set[str] = field(default_factory=set)
    schema_skip: set[str] = field(default_factory=set)

    # -- series ------------------------------------------------------------

    @property
    def series_title(self) -> str:
        return self.series.get("series", {}).get("title", self.series_id)

    def discover_volumes(self) -> list[Volume]:
        """Find source books by globbing `source.dir` and matching `source.volume_pattern`.

        Discovery by regex rather than an explicit list means adding volume 14 needs no config
        edit. Files that do not match the pattern are ignored, not an error — cover art and
        stray files live in the same folder.
        """
        src = self.series.get("source", {})
        directory = paths.PROJECT_ROOT / src.get("dir", f"corpus/{self.series_id}")
        pattern = re.compile(src.get("volume_pattern", r"v(?P<vol>\d+)"))
        overrides: dict[str, int] = src.get("overrides") or {}

        if not directory.is_dir():
            raise ConfigError(
                f"Source directory not found: {paths.relative(directory)}\n"
                f"Put the EPUBs there, or fix `source.dir` in "
                f"{paths.relative(paths.series_config(self.series_id))}."
            )

        # [30] A `part` group numbers books in (part, volume) reading order: Ascendance of a
        # Bookworm restarts at v01 in each of its five Parts, so "Part 2 v01" is volume 4.
        in_order: dict[tuple[int, int], Path] = {}
        if "part" in pattern.groupindex:
            for path in directory.glob(src.get("glob", "*.epub")):
                if (m := pattern.search(path.name)) and path.name not in overrides:
                    in_order[(int(m.group("part")), int(m.group("vol")))] = path
        global_vol = {path: n for n, (_, path) in enumerate(sorted(in_order.items()), start=1)}

        found: dict[int, Path] = {}
        for path in sorted(directory.glob(src.get("glob", "*.epub"))):
            if path.name in overrides:
                found[int(overrides[path.name])] = path
                continue
            match = pattern.search(path.name)
            if not match:
                continue
            vol = global_vol.get(path) or int(match.group("vol"))
            if vol in found:
                raise ConfigError(
                    f"Two files claim to be volume {vol}: "
                    f"{found[vol].name} and {path.name}. "
                    f"Disambiguate with `source.overrides` in the series config."
                )
            found[vol] = path

        books = sorted(directory.glob(src.get("glob", "*.epub")))
        if not found and len(books) == 1:
            # [30] A standalone novel is volume 1. Its filename carries no volume number, and
            # "No volumes matched" was the first thing a single-book series ever saw.
            found[1] = books[0]
            # [31] `source.volume_breaks` cuts that one book into reading volumes; each is
            # sliced out of the same file by `ingest.epub.select_reading_volume`.
            for n in range(2, len(src.get("volume_breaks") or []) + 2):
                found[n] = books[0]

        if not found:
            raise ConfigError(
                f"No volumes matched in {paths.relative(directory)}. "
                f"Check `source.glob` and `source.volume_pattern` in the series config."
            )
        return [Volume(vol=v, path=found[v]) for v in sorted(found)]

    def volume_numbers(self) -> list[int]:
        return [v.vol for v in self.discover_volumes()]

    # -- models ------------------------------------------------------------

    def resolve_role(self, stage: str) -> Profile:
        """Return the Profile for a pipeline stage, honouring CLI overrides, routing, and key
        fallbacks, in that order.

        This is the only supported way to choose a model. If the routed profile needs an API
        key that is not set, the declared `fallback` role is used instead — which is why a run
        with no keys at all works and simply produces less polished prose. A CLI `--model`
        override (`model_overrides`/`adhoc_profiles`) takes precedence over both, and does NOT
        fall back — the caller named a specific model, so a missing key there is a ConfigError,
        not a silent substitution.
        """
        if stage in self.adhoc_profiles:
            return self.adhoc_profiles[stage]
        if "*" in self.adhoc_profiles and stage not in self.model_overrides:
            return self.adhoc_profiles["*"]

        override_role = self.model_overrides.get(stage) or self.model_overrides.get("*")
        if override_role:
            return self._build_profile(override_role)

        routing = self.models.get("routing", {})
        if stage not in routing:
            raise ConfigError(
                f"No routing entry for stage {stage!r} in config/models.yaml. "
                f"Known stages: {', '.join(sorted(routing))}"
            )
        entry = routing[stage]
        role = entry.get("role") if isinstance(entry, dict) else str(entry)
        fallback = entry.get("fallback") if isinstance(entry, dict) else None

        profile = self._build_profile(role)
        if profile.is_callable or not fallback:
            return profile
        # `fallback` may be a list (2026-09-23): routing now has two credentialed tiers above
        # local -- Vertex, then the OpenRouter free tier -- and a single fallback could only
        # preserve one of "prefer the free tier over local" and "a machine with no keys at all
        # still runs". The first callable wins; the last entry is the answer when none are.
        chain = fallback if isinstance(fallback, list) else [fallback]
        for candidate in chain:
            fb = self._build_profile(candidate)
            if fb.is_callable:
                return fb
        return fb

    def fallbacks_after(self, stage: str, profile: Profile) -> list[Profile]:
        """[30] The callable rungs of `stage`'s fallback chain below `profile`, for a call the
        routed model refused on content grounds (Gemini's PROHIBITED_CONTENT filter blocked a
        Bookworm passage about toddlers at a babysitter's). Empty for a CLI `--model` override:
        the caller named that model."""
        if stage in self.adhoc_profiles or "*" in self.adhoc_profiles:
            return []
        if self.model_overrides.get(stage) or self.model_overrides.get("*"):
            return []
        entry = self.models.get("routing", {}).get(stage)
        fallback = entry.get("fallback") if isinstance(entry, dict) else None
        chain = fallback if isinstance(fallback, list) else [fallback] if fallback else []
        profiles = [self._build_profile(role) for role in chain]
        after = [p for p in profiles if p.name != profile.name]
        if profile.name in [p.name for p in profiles]:
            after = profiles[[p.name for p in profiles].index(profile.name) + 1:]
        return [p for p in after if p.is_callable]

    def profile_available(self, role: str) -> bool:
        """True when this profile could actually be called right now (key present, or local)."""
        try:
            profile = self._build_profile(role)
        except ConfigError:
            return False
        return profile.is_callable

    def _build_profile(self, role: str) -> Profile:
        profiles = self.models.get("profiles", {})
        if role not in profiles:
            raise ConfigError(
                f"Unknown model profile {role!r}. "
                f"Defined profiles: {', '.join(sorted(profiles))}"
            )
        raw = profiles[role]
        price = raw.get("price_per_mtok", {}) or {}

        api_key = None
        if key_env := raw.get("api_key_env"):
            api_key = os.environ.get(key_env) or None

        base_url = raw.get("base_url")
        if url_env := raw.get("base_url_env"):
            base_url = os.environ.get(url_env) or base_url
        if raw.get("provider") == "ollama" and not base_url:
            base_url = "http://localhost:11434"

        return Profile(
            name=role,
            provider=raw["provider"],
            model=raw["model"],
            options=dict(raw.get("options") or {}),
            api_key=api_key,
            base_url=base_url,
            price_in=float(price.get("input", 0.0)),
            price_out=float(price.get("output", 0.0)),
            concurrency=int(raw.get("concurrency", 1)),
            use_vertex=bool(raw.get("use_vertex", False)),
            tier=str(raw.get("tier", "standard")),
            max_volumes=raw.get("max_volumes"),
            max_calls_per_run=raw.get("max_calls_per_run"),
        )

    @property
    def client_config(self) -> dict[str, Any]:
        return self.models.get("client", {}) or {}

    @property
    def budget_config(self) -> dict[str, Any]:
        return self.models.get("budget", {}) or {}

    # -- extraction taxonomy ------------------------------------------------

    @property
    def entity_types(self) -> dict[str, Any]:
        return self.extraction.get("entity_types", {})

    @property
    def relations(self) -> dict[str, Any]:
        return self._effective_predicates("relation", self.extraction.get("relations", {}))

    @property
    def attributes(self) -> dict[str, Any]:
        return self._effective_predicates("attribute", self.extraction.get("attributes", {}))

    @property
    def traits(self) -> dict[str, Any]:
        return self._effective_predicates("trait", self.extraction.get("traits", {}))

    def _effective_predicates(self, kind: str, base: dict[str, Any]) -> dict[str, Any]:
        """Apply this run's `--attr`/`--trait`/`--relation` additions and `--only`/`--skip`
        restrictions to one predicate kind's config dict. Config-file predicates (base +
        per-series overlay, already merged into `self.extraction` by `load_settings`) are
        overlaid with `schema_overrides[kind]` first, then filtered -- an ad-hoc predicate can
        itself be excluded by a matching `--skip`, which is the whole point of `--skip` accepting
        a bare predicate name.
        """
        merged = {**base, **self.schema_overrides.get(kind, {})}
        if self.schema_only:
            merged = {p: cfg for p, cfg in merged.items() if p in self.schema_only or kind in self.schema_only}
        if self.schema_skip:
            merged = {p: cfg for p, cfg in merged.items() if p not in self.schema_skip and kind not in self.schema_skip}
        return merged

    @property
    def extraction_behaviour(self) -> dict[str, Any]:
        """The `extraction:` sub-block of extraction.yaml — window sizing, confidence floors,
        quote/entity-resolution policy. Named to avoid colliding with `self.extraction`, which is
        the whole parsed file."""
        return self.extraction.get("extraction", {}) or {}

    @property
    def conflicts_config(self) -> dict[str, Any]:
        """The `conflicts:` sub-block of extraction.yaml — CONTRACTS §4.2 classification rule,
        the escalate-to-arbitration confidence gap, and the quiet-change predicate list."""
        return self.extraction.get("conflicts", {}) or {}

    @property
    def canonicalize_config(self) -> dict[str, Any]:
        """The `canonicalize:` sub-block of extraction.yaml (Phase 17) — whether value
        canonicalization runs, the bge-m3 cosine similarity threshold, and the minimum distinct-
        value count before an embed call is worth making."""
        return self.extraction.get("canonicalize", {}) or {}

    @property
    def prose_config(self) -> dict[str, Any]:
        """The `prose:` sub-block of extraction.yaml — `codex_summary`'s sentence-count bound and
        `forbid_uncited_specifics`. Phase 6's `synth/prose.py` reads this instead of
        `self.extraction["prose"]` directly, matching every other taxonomy accessor here."""
        return self.extraction.get("prose", {}) or {}

    @property
    def page_gate_config(self) -> dict[str, Any]:
        """The `page_gate:` sub-block of extraction.yaml (Phase 22 A5, S5) -- `min_claims`, the
        floor `synth/assemble.py::has_min_evidence` checks before `wiki synthesize` writes a
        page, so a crash-truncated character with zero real evidence gets no page instead of a
        blank one."""
        return self.extraction.get("page_gate", {}) or {}

    @property
    def page_outline(self) -> list[dict[str, Any]]:
        """CONTRACTS §5.2's closed section vocabulary for CHARACTER pages (Phase 13), sorted by
        `order`. Each entry gains its own `key`. Base + per-series overlay only (`load_settings`'s
        `_deep_merge` already handles add/override/`null`-delete for this dict-of-dicts, same as
        `attributes`/`relations`/`traits`) — no CLI override exists for this yet."""
        raw = self.extraction.get("page_outline", {}) or {}
        sections = [{"key": key, **cfg} for key, cfg in raw.items()]
        sections.sort(key=lambda s: s.get("order", 0))
        return sections

    def route_for(self, entity_type: str, slug: str) -> str:
        """Site route for an entity. Characters get a unique page; everything else is an
        anchor on a shared codex page. See VISION.md 2026-09-01 (interlinked pages)."""
        spec = self.entity_types.get(entity_type)
        if not spec:
            raise ConfigError(f"Unknown entity type {entity_type!r} (config/extraction.yaml)")
        return str(spec["route"]).format(slug=slug)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Missing config file: {paths.relative(path)}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {paths.relative(path)}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{paths.relative(path)} must contain a YAML mapping at the top level")
    return data


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Recursively merge `overlay` over `base`. An overlay dict value of exactly `None` deletes
    that key from the merged result (a series overlay's way of saying "this series has no
    RANK") — every other overlay value replaces the base value outright, dict-merging one level
    deeper only when both sides are dicts. Lists are replaced wholesale, never concatenated:
    nothing in extraction.yaml's shape (predicate configs, `codex_pages.*.types`) needs
    list-merging, and silently appending would make an overlay's effect depend on base-file
    order.
    """
    if isinstance(overlay, dict) and isinstance(base, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if value is None:
                merged.pop(key, None)
            elif isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


@lru_cache(maxsize=8)
def load_settings(series_id: str | None = None) -> Settings:
    """Load all configuration for a series. Cached — call it freely.

    `extraction.yaml` is the one taxonomy every series uses. The per-series overlay
    (`extraction.<series>.yaml`, 2026-09-04) was removed in Phase 32: req. 8 forbids a schema
    tuned for one series. `--set`/`--attr` still adjust a single run.
    """
    sid = series_id or DEFAULT_SERIES
    extraction = _read_yaml(paths.extraction_config())
    series = _read_yaml(paths.series_config(sid))
    if (base := paths.variant_base(sid)) and not series.get("gold_from"):
        series = {**series, "gold_from": base}  # [33] a variant is scored against its base's gold
    return Settings(
        series_id=sid,
        series=series,
        models=_read_yaml(paths.models_config()),
        extraction=extraction,
    )


def available_series() -> list[str]:
    """Series ids with a config file present, for error messages and `--series` help."""
    return sorted(
        p.name[len("series.") : -len(".yaml")]
        for p in paths.CONFIG_DIR.glob("series.*.yaml")
    )


# ---------------------------------------------------------------------------
# --model CLI overrides
# ---------------------------------------------------------------------------

_KNOWN_PROVIDERS = {"ollama", "anthropic", "google", "openai", "minicheck"}


def _base_profile_for_clone(settings: Settings, stage: str, provider: str) -> Profile:
    """Pick a profile to clone options/pricing/api_key/base_url from for an ad-hoc
    `provider:model` override. A bare model id carries none of those -- they are
    provider-shaped (see llm/providers/*.py's option filtering) -- so guessing wrong would
    silently mis-price or mis-shape the request. Preference order: (1) the stage's own current
    profile, if it already uses this provider; (2) the first `models.yaml` profile using this
    provider; (3) `default_role`.
    """
    routing = settings.models.get("routing", {})
    if stage in routing:
        entry = routing[stage]
        role = entry.get("role") if isinstance(entry, dict) else str(entry)
        try:
            current = settings._build_profile(role)
        except ConfigError:
            current = None
        if current is not None and current.provider == provider:
            return current

    for name, raw in (settings.models.get("profiles", {}) or {}).items():
        if raw.get("provider") == provider:
            return settings._build_profile(name)

    default_role = settings.models.get("default_role")
    if default_role:
        try:
            return settings._build_profile(default_role)
        except ConfigError:
            pass

    raise ConfigError(
        f"No existing profile in config/models.yaml uses provider {provider!r}, so there is "
        f"nothing to clone options/pricing from for an ad-hoc override. Add a profile for it "
        f"first, or use a role name instead of provider:model."
    )


def parse_model_override(spec: str, settings: Settings) -> tuple[str, Profile | None, str | None]:
    """Parse one `--model` argument into `(stage, adhoc_profile_or_None, role_name_or_None)`.
    Exactly one of the last two is not None.

    Accepted forms:
      "<role>"                       every stage uses this role                  (stage = "*")
      "<stage>=<role>"                one stage uses this role
      "<provider>:<model>"           every stage clones a same-provider profile, ad hoc (stage = "*")
      "<stage>=<provider>:<model>"    one stage, ad hoc

    A role name is looked up in `config/models.yaml` `profiles:` and carries its own pricing,
    api_key_env, base_url and provider-shaped option keys -- the safe way to switch models. An
    ad-hoc `provider:model` is for trying an id that has no profile yet; see
    `_base_profile_for_clone` for what it inherits and why.
    """
    stage, sep, rhs = spec.partition("=")
    if not sep:
        stage, rhs = "*", spec
    stage = stage.strip()
    rhs = rhs.strip()
    if not rhs:
        raise ConfigError(f"Could not parse --model {spec!r}: nothing after '='.")

    routing = settings.models.get("routing", {})
    if stage != "*" and stage not in routing:
        raise ConfigError(
            f"Unknown stage {stage!r} in --model {spec!r}. "
            f"Known stages: {', '.join(sorted(routing))}, or '*' for every stage."
        )

    if ":" in rhs:
        provider, _, model_id = rhs.partition(":")
        provider = provider.strip()
        model_id = model_id.strip()
        if provider not in _KNOWN_PROVIDERS:
            raise ConfigError(
                f"Unknown provider {provider!r} in --model {spec!r}. "
                f"Known providers: {', '.join(sorted(_KNOWN_PROVIDERS))}."
            )
        if not model_id:
            raise ConfigError(f"Could not parse --model {spec!r}: no model id after '{provider}:'.")
        base = _base_profile_for_clone(settings, stage, provider)
        profile = replace(base, name=f"adhoc:{rhs}", model=model_id)
        return stage, profile, None

    profiles = settings.models.get("profiles", {})
    if rhs not in profiles:
        raise ConfigError(
            f"Unknown model role {rhs!r} in --model {spec!r}. "
            f"Defined profiles: {', '.join(sorted(profiles))}."
        )
    return stage, None, rhs


def apply_model_overrides(settings: Settings, specs: list[str] | None) -> list[str]:
    """Apply zero or more `--model` overrides to `settings` in place. Returns warning strings
    for the CLI to print (one per ad-hoc override, since its cost estimate is inherited from
    the cloned profile rather than measured)."""
    warnings: list[str] = []
    for spec in specs or []:
        stage, adhoc, role = parse_model_override(spec, settings)
        if adhoc is not None:
            # `run-all` re-applies the same --model specs once per stage command it calls
            # (each resolves the shared, lru_cache'd Settings) -- skip the warning on a repeat
            # application of the identical override rather than printing it once per stage.
            if settings.adhoc_profiles.get(stage) != adhoc:
                warnings.append(
                    f"--model {spec}: ad-hoc override with no config/models.yaml profile -- "
                    f"pricing (${adhoc.price_in:g}/${adhoc.price_out:g} per Mtok in/out) and "
                    f"request options are inherited from an existing {adhoc.provider} profile "
                    f"and may not match {adhoc.model} exactly. Add a real profile for accurate "
                    f"cost tracking."
                )
            settings.adhoc_profiles[stage] = adhoc
        else:
            settings.model_overrides[stage] = role
    return warnings


# ---------------------------------------------------------------------------
# --set CLI overrides (generic dotted-key config)
# ---------------------------------------------------------------------------

# The three config files Settings holds, and the only legal `--set` roots. Naming the root
# explicitly (rather than searching all three for the key) keeps an ambiguous key like
# `prose.max_sentences` from silently landing in whichever file happened to match first.
_SET_ROOTS = ("series", "models", "extraction")


def parse_set_override(spec: str) -> tuple[str, list[str], Any]:
    """Parse one `--set` argument into `(root, path, value)`.

    Form: `<root>.<key>[.<key>...]=<value>`, where `<root>` is one of `series` / `models` /
    `extraction`. The value goes through `yaml.safe_load`, so it arrives with the same type the
    YAML file itself would have given it: `6` is an int, `0.5` a float, `true` a bool,
    `[A, B]` a list, and `null` deletes the key (`_deep_merge`'s existing convention).
    """
    key, sep, raw_value = spec.partition("=")
    if not sep:
        raise ConfigError(
            f"--set {spec!r}: expected <root>.<key>=<value>, with an '='. "
            f"Example: --set extraction.page_gate.min_claims=2"
        )
    parts = [p for p in key.strip().split(".") if p]
    if len(parts) < 2:
        raise ConfigError(
            f"--set {spec!r}: the key needs a root and at least one field. "
            f"Example: --set extraction.page_gate.min_claims=2"
        )
    root, path = parts[0], parts[1:]
    if root not in _SET_ROOTS:
        raise ConfigError(
            f"--set {spec!r}: unknown config root {root!r}. Use one of "
            f"{', '.join(_SET_ROOTS)} -- config/series.<id>.yaml, config/models.yaml, "
            f"config/extraction.yaml respectively."
        )
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"--set {spec!r}: could not parse {raw_value!r} as a YAML value: {exc}"
        ) from exc
    return root, path, value


def apply_set_overrides(settings: Settings, specs: list[str] | None) -> list[str]:
    """Apply zero or more `--set` overrides to `settings` in place. Returns note strings for the
    CLI to print, so a run's effective configuration shows up in its own output.

    Same "mutate Settings, return notes" contract as `apply_model_overrides` and
    `apply_schema_overrides`. Reuses `_deep_merge`, so a nested key merges into its siblings
    rather than replacing the whole block, and an explicit `null` deletes -- byte-identical
    semantics to writing the same thing in a per-series overlay file, which is what makes
    "try it with --set, promote it to YAML once it's proven" safe.
    """
    notes: list[str] = []
    for spec in specs or []:
        root, path, value = parse_set_override(spec)
        overlay: Any = value
        for part in reversed(path):
            overlay = {part: overlay}
        current = getattr(settings, root)
        merged = _deep_merge(current, overlay)
        # `run-all` re-applies the same specs once per sub-command it calls, each resolving the
        # shared lru_cache'd Settings -- stay quiet on a repeat application of an identical
        # override, the same way apply_model_overrides checks before warning.
        if merged != current:
            notes.append(f"--set {root}.{'.'.join(path)} = {value!r}")
        setattr(settings, root, merged)
    return notes


# ---------------------------------------------------------------------------
# --attr / --trait / --relation / --only / --skip CLI schema tuning
# ---------------------------------------------------------------------------

_KINDS = {"attribute", "relation", "trait"}


def parse_attr_override(spec: str) -> tuple[str, dict[str, Any]]:
    """'NAME=format:"Display"[:multi]' -> (NAME, {single, format, display}). Single-valued
    (`single: true`) is the default, matching most of the base taxonomy (AGE/GENDER/STATUS/...)
    -- pass the literal trailing `:multi` for a TITLE/NICKNAME-shaped predicate that may hold
    more than one value at once."""
    name, sep, rhs = spec.partition("=")
    if not sep or not name.strip():
        raise ConfigError(f'Could not parse --attr {spec!r}: expected \'NAME=format:"Display"\'.')
    parts = rhs.split(":")
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise ConfigError(f'Could not parse --attr {spec!r}: expected \'NAME=format:"Display"\'.')
    single = True
    if len(parts) > 2:
        if parts[2].strip().lower() != "multi":
            raise ConfigError(f"Could not parse --attr {spec!r}: the third segment must be 'multi'.")
        single = False
    return name.strip().upper(), {
        "single": single,
        "format": parts[0].strip(),
        "display": parts[1].strip().strip('"'),
    }


def parse_trait_override(spec: str) -> tuple[str, dict[str, Any]]:
    """'NAME="Display"[:max_words]' -> (NAME, {display, max_words})."""
    name, sep, rhs = spec.partition("=")
    if not sep or not name.strip() or not rhs.strip():
        raise ConfigError(f'Could not parse --trait {spec!r}: expected \'NAME="Display"[:max_words]\'.')
    parts = rhs.split(":")
    display = parts[0].strip().strip('"')
    max_words = 10
    if len(parts) > 1 and parts[1].strip():
        try:
            max_words = int(parts[1].strip())
        except ValueError:
            raise ConfigError(f"Could not parse --trait {spec!r}: max_words must be an integer.") from None
    return name.strip().upper(), {"display": display, "max_words": max_words}


def parse_relation_override(spec: str) -> tuple[str, dict[str, Any]]:
    """'NAME="Display"[:symmetric|:inverse=OTHER_NAME]' -> (NAME, {display, symmetric?, inverse?})."""
    name, sep, rhs = spec.partition("=")
    if not sep or not name.strip() or not rhs.strip():
        raise ConfigError(
            f'Could not parse --relation {spec!r}: expected \'NAME="Display"[:symmetric|:inverse=OTHER]\'.'
        )
    parts = rhs.split(":")
    cfg: dict[str, Any] = {"display": parts[0].strip().strip('"')}
    for extra in parts[1:]:
        extra = extra.strip()
        if extra.lower() == "symmetric":
            cfg["symmetric"] = True
        elif extra.lower().startswith("inverse="):
            cfg["inverse"] = extra.split("=", 1)[1].strip().upper()
        elif extra:
            raise ConfigError(
                f"Could not parse --relation {spec!r}: {extra!r} is not 'symmetric' or 'inverse=OTHER'."
            )
    return name.strip().upper(), cfg


def _normalize_only_skip_token(token: str) -> str:
    """A `--only`/`--skip` token is either a kind word (lower-cased: attribute/relation/trait) or
    a predicate name (upper-cased, matching how every predicate is keyed in extraction.yaml)."""
    stripped = token.strip()
    lowered = stripped.lower()
    return lowered if lowered in _KINDS else stripped.upper()


def apply_schema_overrides(
    settings: Settings,
    *,
    attrs: list[str] | None = None,
    traits: list[str] | None = None,
    relations: list[str] | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
) -> list[str]:
    """Apply zero or more `--attr`/`--trait`/`--relation`/`--only`/`--skip` overrides to
    `settings` in place. Returns human-readable summary lines for the CLI to print — narrowing or
    widening a run's extraction vocabulary is exactly the kind of change that belongs in `wiki
    runs`'/`wiki doctor`'s output, not something that happens silently."""
    notes: list[str] = []
    for spec in attrs or []:
        name, cfg = parse_attr_override(spec)
        settings.schema_overrides.setdefault("attribute", {})[name] = cfg
        notes.append(f"--attr: {name} ({cfg['format']}, {'single' if cfg['single'] else 'multi'}-valued)")
    for spec in traits or []:
        name, cfg = parse_trait_override(spec)
        settings.schema_overrides.setdefault("trait", {})[name] = cfg
        notes.append(f"--trait: {name} (max {cfg['max_words']} words)")
    for spec in relations or []:
        name, cfg = parse_relation_override(spec)
        settings.schema_overrides.setdefault("relation", {})[name] = cfg
        notes.append(f"--relation: {name}")
    if only:
        tokens = {_normalize_only_skip_token(t) for t in only}
        settings.schema_only |= tokens
        notes.append(f"--only: {', '.join(sorted(tokens))} (everything else excluded)")
    if skip:
        tokens = {_normalize_only_skip_token(t) for t in skip}
        settings.schema_skip |= tokens
        notes.append(f"--skip: {', '.join(sorted(tokens))}")
    return notes


def _parse_range(spec: str | None, available: list[int], *, label: str, not_found_hint: str) -> list[int]:
    """Shared body for `parse_volume_range`/`parse_chapter_range`: '1-13', '1,3,5', '7', or None
    for all available.

    Raises ConfigError naming the offending item rather than silently processing fewer than the
    user asked for — a silent drop here would show up much later as a character mysteriously
    missing half their facts.
    """
    if not spec:
        return list(available)

    wanted: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:  # allow a leading '-' to be an error rather than a range
            start_s, _, end_s = part.partition("-")
            try:
                start, end = int(start_s), int(end_s)
            except ValueError:
                raise ConfigError(f"Could not parse {label} range {part!r}") from None
            if start > end:
                raise ConfigError(f"{label.capitalize()} range {part!r} runs backwards")
            wanted.extend(range(start, end + 1))
        else:
            try:
                wanted.append(int(part))
            except ValueError:
                raise ConfigError(f"Could not parse {label} {part!r}") from None

    missing = sorted(set(wanted) - set(available))
    if missing:
        raise ConfigError(
            f"Requested {label}(s) {missing} but only {available} were found {not_found_hint}."
        )
    # De-duplicate while preserving ascending order.
    return sorted(set(wanted))


def parse_volume_range(spec: str | None, available: list[int]) -> list[int]:
    """Parse a --volumes argument: '1-13', '1,3,5', '7', or None for all available."""
    return _parse_range(
        spec, available, label="volume",
        not_found_hint="in the source directory. Check the filenames or `source.volume_pattern`",
    )


def parse_chapter_range(spec: str | None, available: list[int]) -> list[int]:
    """Parse a --chapters argument: '1-4', '2,5', '3', or None for all available chapters in the
    single volume --volumes resolved to."""
    return _parse_range(spec, available, label="chapter", not_found_hint="in this volume")
