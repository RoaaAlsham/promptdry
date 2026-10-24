"""Tool registration, actionable errors, and a stdio call of both tools."""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

import server
from promptdry.gemma import GemmaOutputError

ROOT = Path(__file__).resolve().parents[1]
GEMMA_MESSAGE = "Gemma returned invalid JSON after one repair; retry evaluation."
EXPIRED = "evaluation_expired: call evaluate_prompt again; this id is missing or older than one hour."


def call(name, arguments):
    return asyncio.run(server.mcp.call_tool(name, arguments))


def test_dev_dependencies_survive_windows_cmd():
    assert server.mcp.dependencies
    for package in server.mcp.dependencies:
        assert "<" not in package and ">" not in package


def test_server_exposes_two_short_tools():
    assert server.mcp.name == "promptdry"
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {"evaluate_prompt", "improve_prompt"}
    evaluate = tools["evaluate_prompt"]
    improve = tools["improve_prompt"]
    assert "new user task" in evaluate.description
    assert "follow-ups" in evaluate.description and "skip eval" in evaluate.description
    assert "reduce" in improve.description and "needs_answers" in improve.description
    assert len(evaluate.description) < 200 and len(improve.description) < 200
    assert evaluate.outputSchema is None and improve.outputSchema is None
    assert set(evaluate.inputSchema["required"]) == {"prompt", "model_id"}
    assert set(improve.inputSchema["required"]) == {"evaluation_id", "mode"}
    assert "answers" not in improve.inputSchema["required"]
    assert set(improve.inputSchema["properties"]["mode"]["enum"]) == {"reduce", "enhance", "both"}


def test_tools_delegate_and_answers_default_to_none(monkeypatch):
    seen = {}

    def fake_evaluate(prompt, model_id):
        seen["evaluate"] = (prompt, model_id)
        return {"evaluation_id": None, "gate": {"skipped": True, "reason": "under 40 tokens"}}

    def fake_improve(evaluation_id, mode, answers=None):
        seen["improve"] = (evaluation_id, mode, answers)
        return {"status": "ready", "rewritten_prompt": "Use the original."}

    monkeypatch.setattr(server, "evaluate", fake_evaluate)
    monkeypatch.setattr(server, "improve", fake_improve)
    evaluated = call("evaluate_prompt", {"prompt": "hello", "model_id": "gpt-test"})
    improved = call("improve_prompt", {"evaluation_id": "ev_1", "mode": "both"})
    assert json.loads(evaluated[0].text)["gate"]["skipped"] is True
    assert seen["evaluate"] == ("hello", "gpt-test")
    assert json.loads(improved[0].text)["status"] == "ready"
    assert seen["improve"] == ("ev_1", "both", None)


def test_gemma_and_expired_errors_are_actionable(monkeypatch):
    def fail(prompt, model_id):
        raise GemmaOutputError(GEMMA_MESSAGE)

    monkeypatch.setattr(server, "evaluate", fail)
    gemma = call("evaluate_prompt", {"prompt": "SECRET_PROMPT_TEXT", "model_id": "gpt-test"})
    expired = call("improve_prompt", {"evaluation_id": "ev_missing", "mode": "reduce"})
    assert isinstance(gemma, CallToolResult) and gemma.isError
    assert gemma.content[0].text == GEMMA_MESSAGE
    assert "SECRET_PROMPT_TEXT" not in gemma.content[0].text
    assert isinstance(expired, CallToolResult) and expired.isError
    assert expired.content[0].text == EXPIRED


def test_stdio_calls_both_tools():
    asyncio.run(_stdio_calls_both_tools())


async def _stdio_calls_both_tools():
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], cwd=ROOT)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "promptdry"
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["evaluate_prompt", "improve_prompt"]
            evaluated = await session.call_tool("evaluate_prompt", {"prompt": "hi", "model_id": "gpt-4o"})
            assert evaluated.isError is False
            body = json.loads(evaluated.content[0].text)
            assert body["gate"]["skipped"] is True
            assert body["evaluation_id"] is None
            assert "SECRET" not in evaluated.content[0].text
            improved = await session.call_tool(
                "improve_prompt", {"evaluation_id": "ev_missing", "mode": "enhance"}
            )
            assert improved.isError is True
            assert improved.content[0].text == EXPIRED
