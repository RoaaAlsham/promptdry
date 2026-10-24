"""Resolve model prices without guessing; all rates are USD per million tokens."""

import json
import math
import re
from importlib.resources import files
from pathlib import Path
from typing import Literal, Protocol, TypedDict

import yaml
from llm_costs import get_model_pricing
from llm_costs.calculator import list_providers

ROOT = Path(__file__).resolve().parent.parent


class Prices(TypedDict):
    input_per_m: float
    output_per_m: float
    source: Literal["llm_costs", "override"]
    as_of: str | None


class ModelProfile(TypedDict):
    requested: str
    resolved: str | None
    status: Literal["resolved", "needs_model"]
    pricing_source: Literal["llm_costs", "override"] | None
    as_of: str | None
    prices: Prices | None


class PricingProvider(Protocol):
    def get(self, provider: str, model: str) -> Prices | None: ...


def _rate(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Prices must be numeric")
    rate = float(value)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("Prices must be finite and nonnegative")
    return rate


def _read_object(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError(f"Cannot read {path.name} as a JSON object") from None
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


class LlmCostsProvider:
    """Use llm-costs' standard, uncached, non-batch base rates.

    The inspected 0.1.6 API returns {input, output, aliases, ...}; its bundled
    YAML declares USD/per_million_tokens and version (not a retrieval date).
    Long-context and cache discounts are outside this two-rate interface.
    """

    def __init__(self) -> None:
        self._metadata: dict[str, dict] = {}
        for provider in list_providers():
            resource = files("llm_costs").joinpath("pricing", f"{provider}.yaml")
            self._metadata[provider] = yaml.safe_load(resource.read_text(encoding="utf-8"))

    def canonical_model(self, provider: str, model: str) -> str:
        models = self._metadata.get(provider, {}).get("models", {})
        if model in models:
            return model
        for name, config in models.items():
            if model in config.get("aliases", []):
                return name
        return model

    def get(self, provider: str, model: str) -> Prices | None:
        metadata = self._metadata.get(provider)
        if not metadata or metadata.get("currency") != "USD":
            return None
        # Do not silently interpret prices if the dependency changes units.
        if metadata.get("unit") != "per_million_tokens":
            return None
        raw = get_model_pricing(provider, model)
        if raw is None:
            return None
        try:
            return Prices(
                input_per_m=_rate(raw["input"]),
                output_per_m=_rate(raw["output"]),
                source="llm_costs",
                as_of=str(metadata["version"]) if metadata.get("version") else None,
            )
        except (KeyError, ValueError, TypeError, OverflowError):
            return None


class OverrideProvider:
    """Read {provider/model: {input_per_m, output_per_m, source, as_of}}.

    Missing files mean no overrides. Invalid configuration fails explicitly,
    rather than silently falling back to a price the user intended to replace.
    """

    def __init__(self, path: str | Path = ROOT / "prices_override.json") -> None:
        self._prices: dict[str, Prices] = {}
        for key, value in _read_object(Path(path)).items():
            if not isinstance(value, dict) or "/" not in key:
                raise ValueError("Override entries must use provider/model keys and price objects")
            try:
                date = value.get("as_of")
                if date is not None and not isinstance(date, str):
                    raise ValueError("as_of must be a string or null")
                if value.get("source", "override") != "override":
                    raise ValueError("Override source must be override")
                prices = Prices(input_per_m=_rate(value["input_per_m"]),
                                output_per_m=_rate(value["output_per_m"]),
                                source="override", as_of=date)
            except (KeyError, ValueError, TypeError, OverflowError):
                raise ValueError("Invalid override pricing entry") from None
            normalized = key.strip().lower()
            if normalized in self._prices:
                raise ValueError("Duplicate normalized override key")
            self._prices[normalized] = prices

    def get(self, provider: str, model: str) -> Prices | None:
        value = self._prices.get(f"{provider}/{model}")
        return value.copy() if value is not None else None


def _split(identifier: str) -> tuple[str, str] | None:
    if "/" in identifier:
        provider, model = identifier.split("/", 1)
        return (provider, model) if provider and model and "/" not in model else None
    for prefixes, provider in [
        (("claude",), "anthropic"),
        (("gpt", "o1", "o3", "o4"), "openai"),
        (("gemini", "gemma"), "google"),
    ]:
        if identifier.startswith(prefixes):
            return provider, identifier
    return None


def resolve_model(
    model_id: str,
    *,
    pricing_provider: PricingProvider | None = None,
    override_provider: PricingProvider | None = None,
    aliases_path: str | Path = ROOT / "aliases.json",
) -> ModelProfile:
    """Try exact ID, explicit alias, then date-stripped ID; never fuzzy-match.

    Alias targets must be provider/model IDs or inferable model names. Alias
    chains are intentionally unsupported. Providers are injectable for tests.
    """
    requested = model_id.strip().lower()
    unresolved = ModelProfile(requested=requested, resolved=None, status="needs_model",
                              pricing_source=None, as_of=None, prices=None)
    if requested in ("", "unknown"):
        return unresolved
    library = pricing_provider if pricing_provider is not None else LlmCostsProvider()
    overrides = override_provider if override_provider is not None else OverrideProvider()

    def lookup(identifier: str) -> ModelProfile | None:
        split = _split(identifier)
        if split is None:
            return None
        provider, model = split
        prices = overrides.get(provider, model)
        # A package alias can point to a canonical model with an override.
        canonical = library.canonical_model(provider, model) if isinstance(library, LlmCostsProvider) else model
        if prices is None and canonical != model:
            prices = overrides.get(provider, canonical)
            if prices is not None:
                model = canonical
        if prices is None:
            prices = library.get(provider, model)
            model = canonical
        if prices is None:
            return None
        return ModelProfile(requested=requested, resolved=f"{provider}/{model}",
                            status="resolved", pricing_source=prices["source"],
                            as_of=prices["as_of"], prices=prices)

    exact = lookup(requested)
    if exact is not None:
        return exact
    aliases = _read_object(Path(aliases_path))
    if not all(isinstance(value, str) for value in aliases.values()):
        raise ValueError("Alias targets must be strings")
    aliases = {key.strip().lower(): value.strip().lower() for key, value in aliases.items()}
    split = _split(requested)
    qualified = "/".join(split) if split else requested
    target = aliases.get(qualified, aliases.get(requested))
    if target:
        result = lookup(target)
        if result is not None:
            return result
    for candidate in dict.fromkeys([requested, target] if target else [requested]):
        stripped = re.sub(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$", "", candidate)
        if stripped != candidate:
            result = lookup(stripped)
            if result is not None:
                return result
    return unresolved
