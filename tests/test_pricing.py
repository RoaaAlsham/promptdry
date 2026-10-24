import json

import pytest

from promptdry.pricing import LlmCostsProvider, OverrideProvider, resolve_model


class FakeProvider:
    def __init__(self, entries=None):
        self.entries = entries or {}
        self.calls = []

    def get(self, provider, model):
        self.calls.append((provider, model))
        return self.entries.get(f"{provider}/{model}")


def price(input_rate=2.0, source="llm_costs"):
    return {"input_per_m": input_rate, "output_per_m": 8.0,
            "source": source, "as_of": "2026-10-24"}


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "aliases.json"
    path.write_text("{}", encoding="utf-8")
    return {"aliases_path": path, "override_provider": FakeProvider()}


def test_installed_library_normalizes_per_million_and_version(config):
    provider = LlmCostsProvider()
    prices = provider.get("openai", "gpt-4o")
    assert prices == {"input_per_m": 2.5, "output_per_m": 10.0,
                      "source": "llm_costs", "as_of": "2026-08"}
    result = resolve_model("  OPENAI/GPT-4O  ", **config)
    assert result["requested"] == "openai/gpt-4o"
    assert result["resolved"] == "openai/gpt-4o"
    assert result["status"] == "resolved"
    assert result["prices"] == prices


@pytest.mark.parametrize("name", ["", "  ", "unknown", "UNKNOWN", "nonsense", "gpt-nonexistent"])
def test_unknown_has_no_prices(name, config):
    result = resolve_model(name, pricing_provider=FakeProvider(), **config)
    assert result["status"] == "needs_model"
    assert result["prices"] is None
    assert result["resolved"] is None
    assert result["pricing_source"] is None
    assert result["as_of"] is None


@pytest.mark.parametrize(("name", "provider"), [
    ("claude-test", "anthropic"), ("gpt-test", "openai"),
    ("o1", "openai"), ("o3-test", "openai"), ("o4-mini", "openai"),
    ("gemini-test", "google"), ("gemma-test", "google"),
])
def test_provider_inference(name, provider, config):
    result = resolve_model(name, pricing_provider=FakeProvider({f"{provider}/{name}": price()}), **config)
    assert result["resolved"] == f"{provider}/{name}"


def test_override_wins_and_keeps_zero_rate(tmp_path, config):
    path = tmp_path / "prices_override.json"
    path.write_text(json.dumps({"openai/gpt-4o": price(0, "override")}), encoding="utf-8")
    library = FakeProvider({"openai/gpt-4o": price()})
    config["override_provider"] = OverrideProvider(path)
    result = resolve_model("gpt-4o", pricing_provider=library, **config)
    assert result["prices"]["input_per_m"] == 0
    assert result["pricing_source"] == "override"
    assert library.calls == []


def test_missing_pricing_is_none_without_raising(tmp_path):
    assert OverrideProvider(tmp_path / "absent.json").get("openai", "gpt-4o") is None
    provider = LlmCostsProvider()
    assert provider.get("missing", "missing") is None
    assert provider.get("openai", "missing") is None
    # The later cost estimator receives None, never fabricated or zero prices.
    assert resolve_model("google/gemma-unlisted")["prices"] is None


def test_alias_and_exact_precedence(config):
    config["aliases_path"].write_text(json.dumps({"my-model": "openai/gpt-target",
                                                "gpt-exact": "openai/gpt-target"}))
    library = FakeProvider({"openai/gpt-target": price(), "openai/gpt-exact": price(3)})
    assert resolve_model(" MY-MODEL ", pricing_provider=library, **config)["resolved"] == "openai/gpt-target"
    assert resolve_model("gpt-exact", pricing_provider=library, **config)["prices"]["input_per_m"] == 3


@pytest.mark.parametrize("suffix", ["2025-01-31", "20250131"])
def test_date_suffix_fallback(suffix, config):
    library = FakeProvider({"openai/gpt-test": price()})
    assert resolve_model(f"gpt-test-{suffix}", pricing_provider=library, **config)["resolved"] == "openai/gpt-test"


def test_alias_precedes_date_stripping(config):
    config["aliases_path"].write_text(json.dumps({"gpt-test-20250131": "openai/gpt-other"}))
    library = FakeProvider({"openai/gpt-test": price(), "openai/gpt-other": price(4)})
    assert resolve_model("gpt-test-20250131", pricing_provider=library, **config)["resolved"] == "openai/gpt-other"


def test_library_alias_returns_canonical_and_honors_override(config):
    config["override_provider"] = FakeProvider({"openai/gpt-4o": price(1, "override")})
    result = resolve_model("gpt-4o-2024-08-06", **config)
    assert result["resolved"] == "openai/gpt-4o"
    assert result["pricing_source"] == "override"


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True, "2.5"])
def test_invalid_override_rejected(tmp_path, bad):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"openai/gpt-4o": price(bad, "override")}))
    with pytest.raises(ValueError, match="Invalid override"):
        OverrideProvider(path)


def test_missing_or_invalid_library_rates_are_unavailable(monkeypatch):
    provider = LlmCostsProvider()
    monkeypatch.setattr("promptdry.pricing.get_model_pricing", lambda *_: {"input": 1})
    assert provider.get("openai", "gpt-4o") is None


def test_malformed_config_has_clear_error(tmp_path):
    path = tmp_path / "prices_override.json"
    path.write_text("not json")
    with pytest.raises(ValueError, match="Cannot read prices_override.json"):
        OverrideProvider(path)
