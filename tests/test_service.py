from unittest.mock import MagicMock

import pytest

from promptdry import service
from promptdry.estimate import count_tokens, cost_range, output_tiers
from promptdry.gemma import BothResult, EnhanceResult, Evaluation, GemmaOutputError, ReduceResult

PROMPT = "Please write a short poem about rain. " * 15


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("PROMPTDRY_MIN_TOKENS", "40")
    profile = dict(requested="gpt-test", resolved="openai/gpt-test", status="resolved",
                   pricing_source="override", as_of="2026-10-24",
                   prices=dict(input_per_m=2, output_per_m=8, source="override", as_of="2026-10-24"))
    resolver = MagicMock(return_value=profile)
    monkeypatch.setattr(service, "resolve_model", resolver)
    evaluation = Evaluation(task_type="open_ended", needs_search_or_tools=False,
        multi_step_reasoning=False, format_constrained=False, length_hint_words=None,
        slots=dict(goal=True, context=True, constraints=True, output_format=False,
                   success_criteria=True, examples_or_inputs=True),
        specification_score=4, efficiency_score=2, issues=["Specify output length."],
        missing_slots=["output_format"])
    api = MagicMock(return_value=evaluation)
    monkeypatch.setattr(service, "call_json", api)
    now = [0.0]
    instance = service.PromptService(clock=lambda: now[0])
    return instance, api, resolver, now


def test_gate_zero_calls_and_profiling_first(setup, monkeypatch):
    instance, api, resolver, _ = setup
    def counter(text):
        resolver.assert_called_once_with("gpt-test")
        return 1
    monkeypatch.setattr(service, "count_tokens", counter)
    result = instance.evaluate("hi", "gpt-test")
    assert result["gate"]["skipped"]
    assert result["evaluation_id"] is None
    assert result["scores"] is None
    assert result["input_tokens"] == 1
    api.assert_not_called()
    assert not instance._cache


def test_evaluation_contract_and_server_numbers(setup):
    instance, api, resolver, _ = setup
    result = instance.evaluate(PROMPT, "gpt-test")
    assert result["evaluation_id"].startswith("ev_")
    assert result["input_tokens"] == count_tokens(PROMPT)
    assert result["output_tokens"] == output_tiers(result["properties"])
    assert result["cost_usd"] == cost_range(resolver.return_value["prices"], count_tokens(PROMPT), result["output_tokens"])
    assert "prices" not in result["model_profile"]
    assert result["scores"] == {"efficiency": 2, "specification": 4}
    result["output_tokens"]["high"] = 999999
    assert instance._cache[result["evaluation_id"]].tiers["high"] == 2000


def test_unknown_model_still_analyzed(setup):
    instance, api, resolver, _ = setup
    resolver.return_value.update(status="needs_model", resolved=None, prices=None)
    result = instance.evaluate(PROMPT, "unknown")
    assert result["model_profile"]["status"] == "needs_model"
    assert result["cost_usd"] is None
    assert result["input_tokens"] > 0
    assert result["output_tokens"]["medium"] > 0
    api.assert_called_once()


def reduction(text):
    return ReduceResult(rewritten_prompt=text, removed=["Filler"], preserved_check=[], risk="low", note="Shortened.")


@pytest.mark.parametrize("rewrite", [PROMPT, PROMPT * 2, "Write a poem about rain."])
def test_reduce_never_longer(setup, rewrite):
    instance, api, _, _ = setup
    original = instance.evaluate(PROMPT, "gpt-test")
    api.return_value = reduction(rewrite)
    result = instance.improve(original["evaluation_id"], "reduce")
    assert result["after"]["input_tokens"] <= result["before"]["input_tokens"]
    assert result["after"]["input_tokens"] == count_tokens(result["rewritten_prompt"])
    assert result["net"]["input_delta"] <= 0
    if count_tokens(rewrite) >= count_tokens(PROMPT):
        assert result["rewritten_prompt"] == PROMPT
        assert result["changes"]["removed"] == []
        assert "Original retained" in result["net"]["note"]


def test_expiry_boundary_and_no_refresh(setup):
    instance, api, _, now = setup
    evaluation_id = instance.evaluate(PROMPT, "gpt-test")["evaluation_id"]
    api.return_value = reduction("Write a poem.")
    now[0] = 3599
    instance.improve(evaluation_id, "reduce")
    now[0] = 3600
    api.reset_mock()
    with pytest.raises(service.EvaluationExpiredError, match="evaluation_expired"):
        instance.improve(evaluation_id, "reduce")
    api.assert_not_called()
    assert not instance._cache


def test_missing_id(setup):
    instance, api, _, _ = setup
    with pytest.raises(service.EvaluationExpiredError, match="evaluation_expired"):
        instance.improve("missing", "both")
    api.assert_not_called()


@pytest.mark.parametrize("mode, cls", [("enhance", EnhanceResult), ("both", BothResult)])
def test_answers_flow_keeps_properties_and_original(setup, mode, cls):
    instance, api, _, _ = setup
    original = instance.evaluate(PROMPT, "gpt-test")
    extra = {"removed": [], "risk": "low"} if mode == "both" else {}
    api.return_value = cls(status="needs_answers", questions=[dict(slot="output_format",
        question="How many lines?", suggested_default="Three")],
        rewritten_prompt="Rain poem [PLACEHOLDER: output_format]", added_slots=[], assumptions=[],
        expected_output_effect="shorter", note="Draft", **extra)
    pending = instance.improve(original["evaluation_id"], mode)
    assert pending["status"] == "needs_answers"
    assert pending["net"]["input_delta"] == 0
    assert pending["net"]["expected_output_effect"] == "n/a"
    assert "no savings claimed" in pending["net"]["note"]
    api.return_value = cls(status="ready", questions=[], rewritten_prompt="Write a three-line rain poem.",
        added_slots=["output_format"], assumptions=[], expected_output_effect="shorter", note="Length specified.", **extra)
    ready = instance.improve(original["evaluation_id"], mode, "Three lines")
    assert ready["status"] == "ready"
    assert ready["questions"] == []
    assert ready["before"]["input_tokens"] == count_tokens(PROMPT)
    assert ready["after"]["output_tokens"] == ready["before"]["output_tokens"] == original["output_tokens"]
    assert ready["net"]["expected_output_effect"] == "shorter"
    assert "Three lines" in api.call_args.args[0]
    assert PROMPT in api.call_args.args[0]
    assert api.call_args.args[1] is cls


def test_failure_not_cached_and_invalid_mode_no_api(setup):
    instance, api, _, _ = setup
    evaluation = api.return_value
    api.side_effect = GemmaOutputError("failure")
    with pytest.raises(GemmaOutputError):
        instance.evaluate(PROMPT, "gpt-test")
    assert not instance._cache
    api.side_effect = None
    api.return_value = evaluation
    evaluation_id = instance.evaluate(PROMPT, "gpt-test")["evaluation_id"]
    api.reset_mock()
    with pytest.raises(ValueError, match="mode"):
        instance.improve(evaluation_id, "invalid")
    api.assert_not_called()
