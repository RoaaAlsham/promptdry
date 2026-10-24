import itertools

import pytest

from promptdry.estimate import count_tokens, cost_range, gate, output_tiers, reasoning_overhead


@pytest.mark.parametrize("text, expected", [("", 0), ("Hello, world!", 4), ("hello", 1)])
def test_token_counts(text, expected):
    assert count_tokens(text) == expected


def test_special_token_text_is_literal():
    assert count_tokens("<|endoftext|>") > 1
    assert count_tokens("مرحبا بالعالم 🌍") > 0


@pytest.mark.parametrize("task, expected", [
    ("classification", (5, 20, 60)), ("extraction", (40, 150, 500)),
    ("summarization", (100, 300, 700)), ("code", (200, 600, 2000)),
    ("reasoning", (300, 900, 3000)), ("open_ended", (200, 600, 2000)),
])
def test_base_tiers(task, expected):
    assert tuple(output_tiers({"task_type": task}).values()) == expected


@pytest.mark.parametrize("search, reasoning, expected", [
    (True, False, (100, 390, 1400)),
    (False, True, (100, 390, 1050)),
    (True, True, (100, 507, 2100)),
])
def test_modifiers_compound(search, reasoning, expected):
    result = output_tiers({"task_type": "summarization", "needs_search_or_tools": search,
                           "multi_step_reasoning": reasoning})
    assert tuple(result.values()) == expected


@pytest.mark.parametrize("words, expected", [(0, (0, 0, 0)), (1, (1, 1, 1)),
    (100, (100, 140, 140)), (500, (100, 507, 700)), (10000, (100, 507, 2100))])
def test_length_clamp_after_modifiers(words, expected):
    result = output_tiers({"task_type": "summarization", "needs_search_or_tools": True,
                          "multi_step_reasoning": True, "format_constrained": True,
                          "length_hint_words": words})
    assert tuple(result.values()) == expected


def test_word_hint_requires_format_constraint():
    assert output_tiers({"length_hint_words": 1}) == {"low": 200, "medium": 600, "high": 2000}
    assert output_tiers({"format_constrained": True, "length_hint_words": None}) == output_tiers({})


def test_monotonicity_across_combinations():
    for task, search, reasoning, words in itertools.product(
        ["classification", "extraction", "summarization", "code", "reasoning", "open_ended"],
        [False, True], [False, True], [None, 0, 1, 20, 100, 5000],
    ):
        result = output_tiers({"task_type": task, "needs_search_or_tools": search,
                              "multi_step_reasoning": reasoning, "format_constrained": True,
                              "length_hint_words": words})
        assert 0 <= result["low"] <= result["medium"] <= result["high"]


@pytest.mark.parametrize("properties, expected", [({}, False), ({"task_type": "reasoning"}, True),
    ({"multi_step_reasoning": True}, True), ({"needs_search_or_tools": True}, False)])
def test_reasoning_flag(properties, expected):
    assert reasoning_overhead(properties) is expected


def test_cost_includes_input_in_every_tier():
    result = cost_range({"input_per_m": 2.5, "output_per_m": 10.0}, 1000,
                        {"low": 100, "medium": 300, "high": 700})
    assert result == pytest.approx({"low": .0035, "medium": .0055, "high": .0095})


def test_missing_prices_and_free_prices_are_distinct():
    tiers = {"low": 5, "medium": 20, "high": 60}
    assert cost_range(None, 100, tiers) is None
    assert cost_range({"input_per_m": 0, "output_per_m": 0}, 100, tiers) == dict.fromkeys(tiers, 0.0)


@pytest.mark.parametrize("count, skipped", [(0, True), (39, True), (40, False), (41, False)])
def test_gate_boundary(monkeypatch, count, skipped):
    monkeypatch.delenv("PROMPTDRY_MIN_TOKENS", raising=False)
    assert gate(count) == {"skipped": skipped, "reason": "below_min_tokens" if skipped else ""}


def test_gate_environment_and_explicit_override(monkeypatch):
    monkeypatch.setenv("PROMPTDRY_MIN_TOKENS", "100")
    assert gate(99)["skipped"]
    assert not gate(100)["skipped"]
    assert not gate(0, 0)["skipped"]
    monkeypatch.setenv("PROMPTDRY_MIN_TOKENS", "invalid")
    with pytest.raises(ValueError, match="PROMPTDRY_MIN_TOKENS"):
        gate(10)
    assert not gate(40, 40)["skipped"]


def test_invalid_estimates_rejected():
    with pytest.raises(ValueError):
        output_tiers({"task_type": "typo"})
    with pytest.raises(ValueError):
        output_tiers({"format_constrained": True, "length_hint_words": -1})
    with pytest.raises(ValueError):
        gate(-1)
    with pytest.raises(ValueError):
        gate(10, -1)
    with pytest.raises(ValueError):
        cost_range({"input_per_m": float("nan"), "output_per_m": 1}, 1,
                   {"low": 1, "medium": 2, "high": 3})
