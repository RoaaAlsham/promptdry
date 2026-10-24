"""Offline checks of the smoke script's credential and request boundaries."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import smoke


def test_missing_key_does_not_call_api(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_dotenv", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = MagicMock()
    monkeypatch.setattr(smoke.genai, "Client", client)
    assert smoke.main() == 1
    client.assert_not_called()
    assert "Set GEMINI_API_KEY" in capsys.readouterr().err


def test_smoke_uses_user_message_and_filters_models(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_dotenv", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.list.return_value = [
        SimpleNamespace(name="models/gemma-test"),
        SimpleNamespace(name="models/other"),
    ]
    client.models.generate_content.return_value = SimpleNamespace(text="OK")
    monkeypatch.setattr(smoke.genai, "Client", MagicMock(return_value=client))
    assert smoke.main() == 0
    args = client.models.generate_content.call_args.kwargs
    assert args["contents"] == "Reply with OK"
    assert args["model"] == "gemma-4-26b-a4b-it"
    assert args["config"].system_instruction is None
    assert args["config"].response_schema is None
    output = capsys.readouterr().out
    assert "models/gemma-test" in output and "OK" in output
    assert "models/other" not in output and "test-secret" not in output


def test_api_error_does_not_expose_details(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_dotenv", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setattr(smoke.genai, "Client", MagicMock(side_effect=RuntimeError("test-secret")))
    assert smoke.main() == 1
    captured = capsys.readouterr()
    assert "test-secret" not in captured.out + captured.err
