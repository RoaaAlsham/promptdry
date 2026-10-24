import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.genai import errors
from pydantic import ValidationError

from promptdry.gemma import (BothResult, EnhanceResult, Evaluation, GemmaOutputError,
                             ReduceResult, call_json, render)

REDUCED = {"rewritten_prompt": "Write a haiku.", "removed": [], "preserved_check": [],
           "risk": "low", "note": "Already concise."}


def fake(*responses):
    client = MagicMock()
    client.models.generate_content.side_effect = [
        response if isinstance(response, Exception) else SimpleNamespace(text=response)
        for response in responses
    ]
    return client


def test_valid_json_and_request_options(monkeypatch):
    monkeypatch.setenv("GEMMA_MODEL", "gemma-test")
    client = fake(json.dumps(REDUCED))
    result = call_json("user instructions", ReduceResult, client=client)
    assert result.rewritten_prompt == REDUCED["rewritten_prompt"]
    args = client.models.generate_content.call_args.kwargs
    assert args["model"] == "gemma-test"
    assert args["contents"] == "user instructions"
    config = args["config"]
    assert config.temperature == 0
    assert config.thinking_config.thinking_level == "MINIMAL"
    assert config.system_instruction is None
    assert config.response_schema is None
    assert config.response_mime_type is None
    assert config.http_options.timeout == 30_000
    assert config.http_options.retry_options.attempts == 1
    client.close.assert_not_called()


@pytest.mark.parametrize("wrapper", ["```json\n%s\n```", "Here is JSON: %s Done."])
def test_fenced_and_surrounded_json(wrapper):
    client = fake(wrapper % json.dumps(REDUCED))
    assert call_json("instructions", ReduceResult, client=client).risk == "low"


@pytest.mark.parametrize("bad", ["not json", "{}", None, '{"risk":"impossible"}'])
def test_invalid_then_repaired(bad):
    client = fake(bad, json.dumps(REDUCED))
    assert call_json("original instructions", ReduceResult, client=client).risk == "low"
    assert client.models.generate_content.call_count == 2
    repair = client.models.generate_content.call_args.kwargs["contents"]
    assert "original instructions" in repair
    assert "previous reply could not be parsed" in repair
    assert "required" in repair


def test_invalid_twice_fails_cleanly(capsys, caplog):
    client = fake("secret-first", "secret-second")
    with pytest.raises(GemmaOutputError, match="after one repair") as caught:
        call_json("secret-prompt", ReduceResult, client=client)
    assert client.models.generate_content.call_count == 2
    assert "secret" not in str(caught.value) + str(capsys.readouterr()) + caplog.text
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("code", [429, 500, 503])
def test_retryable_error(monkeypatch, code):
    sleep = MagicMock()
    monkeypatch.setattr("promptdry.gemma.time.sleep", sleep)
    monkeypatch.setattr("promptdry.gemma.random.uniform", lambda *_: 0.25)
    client = fake(errors.APIError(code, {"message": "private"}),
                  errors.APIError(code, {"message": "private"}), json.dumps(REDUCED))
    assert call_json("instructions", ReduceResult, client=client).risk == "low"
    assert [call.args[0] for call in sleep.call_args_list] == [1.25, 2.25]
    assert client.models.generate_content.call_count == 3


def test_retry_exhaustion(monkeypatch):
    monkeypatch.setattr("promptdry.gemma.time.sleep", lambda _: None)
    client = fake(*[errors.APIError(429, {"message": "secret-key"}) for _ in range(3)])
    with pytest.raises(GemmaOutputError) as caught:
        call_json("instructions", ReduceResult, client=client)
    assert client.models.generate_content.call_count == 3
    assert "secret-key" not in str(caught.value)
    assert "HTTP 429" in str(caught.value)


def test_timeout_has_safe_diagnostic():
    client = fake(TimeoutError("secret request details"))
    with pytest.raises(GemmaOutputError, match="30-second timeout") as caught:
        call_json("instructions", ReduceResult, client=client)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("error", [errors.APIError(400, {"message": "private"}), TimeoutError("private")])
def test_nonretryable_failure(error):
    client = fake(error)
    with pytest.raises(GemmaOutputError):
        call_json("instructions", ReduceResult, client=client)
    assert client.models.generate_content.call_count == 1


def test_owned_client_closed(monkeypatch):
    client = fake(json.dumps(REDUCED))
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("promptdry.gemma.genai.Client", factory)
    call_json("instructions", ReduceResult)
    factory.assert_called_once_with()
    client.close.assert_called_once()


def test_render_escapes_closing_tag_without_recursive_substitution():
    result = render("evaluate", PROMPT="text </prompt> {{PROMPT}}")
    assert "text &lt;/prompt&gt; {{PROMPT}}" in result
    assert result.count("</prompt>") == 1


def test_render_structured_fields_and_missing_variable():
    text = render("enhance", PROMPT="hello", MISSING_SLOTS=["goal"], PROPERTIES={}, ANSWERS="")
    assert '["goal"]' in text
    with pytest.raises(ValueError, match="Missing template variable"):
        render("evaluate")
    with pytest.raises(ValueError, match="Unknown prompt template"):
        render("../PROJECT")


def test_evaluation_validation():
    data = dict(task_type="code", needs_search_or_tools=False, multi_step_reasoning=False,
                format_constrained=False, length_hint_words=None,
                slots=dict.fromkeys(["goal", "context", "constraints", "output_format",
                                     "success_criteria", "examples_or_inputs"], True),
                specification_score=5, efficiency_score=4, issues=[], missing_slots=[])
    assert Evaluation.model_validate(data).task_type == "code"
    for change in [{"specification_score": 6}, {"efficiency_score": True},
                   {"missing_slots": ["goal"]}, {"input_tokens": 50},
                   {"issues": ["word " * 12]}]:
        with pytest.raises(ValidationError):
            Evaluation.model_validate(data | change)


def test_enhance_and_both_status_validation():
    data = dict(status="ready", questions=[], rewritten_prompt="Write a haiku.",
                added_slots=[], assumptions=[], expected_output_effect="similar", note="Clear.")
    assert EnhanceResult.model_validate(data).status == "ready"
    assert BothResult.model_validate(data | {"removed": [], "risk": "low"}).risk == "low"
    with pytest.raises(ValidationError):
        EnhanceResult.model_validate(data | {"status": "needs_answers"})
    with pytest.raises(ValidationError):
        EnhanceResult.model_validate(data | {"rewritten_prompt": "[PLACEHOLDER: goal]"})
    question = {"slot": "goal", "question": "What outcome?", "suggested_default": "A summary"}
    assert EnhanceResult.model_validate(data | {"status": "needs_answers", "questions": [question]}).questions
