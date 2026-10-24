"""Stdio MCP server for prompt pre-flight evaluation and rewriting."""

from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from promptdry.gemma import GemmaOutputError
from promptdry.service import EvaluationExpiredError, evaluate, improve

# Project-local .env, so Claude Desktop's working directory does not matter.
# Existing process variables stay in place.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Package specs stay free of < and >. `mcp dev` on Windows passes them through
# cmd.exe, which treats those characters as redirection.
mcp = FastMCP(
    "promptdry",
    dependencies=[
        "mcp[cli]==1.30.0",
        "google-genai",
        "tiktoken",
        "pydantic",
        "python-dotenv",
        "llm-costs",
        "PyYAML",
        "typer",
    ],
)


def _failure(exc: GemmaOutputError | EvaluationExpiredError) -> CallToolResult:
    """Return a tool error whose text is safe to show an agent."""
    if isinstance(exc, EvaluationExpiredError):
        text = "evaluation_expired: call evaluate_prompt again; this id is missing or older than one hour."
    else:
        text = str(exc) or "Gemma output was unusable; retry evaluate_prompt."
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


@mcp.tool(structured_output=False)
def evaluate_prompt(prompt: str, model_id: str) -> dict[str, Any] | CallToolResult:
    """Call once at the start of a new user task, before other tools. Skip follow-ups, slash commands, and skip eval."""
    try:
        return evaluate(prompt, model_id)
    except (GemmaOutputError, EvaluationExpiredError) as exc:
        return _failure(exc)


@mcp.tool(structured_output=False)
def improve_prompt(
    evaluation_id: str,
    mode: Literal["reduce", "enhance", "both"],
    answers: str | None = None,
) -> dict[str, Any] | CallToolResult:
    """Call when the user chooses reduce, enhance, or both. Pass answers only after a needs_answers result."""
    try:
        return improve(evaluation_id, mode, answers)
    except (GemmaOutputError, EvaluationExpiredError) as exc:
        return _failure(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
