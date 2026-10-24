"""Streamable HTTP MCP server for prompt pre-flight evaluation and rewriting."""

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse

from promptdry.gemma import GemmaOutputError
from promptdry.service import EvaluationExpiredError, evaluate, improve

# Project-local .env, so a platform working directory does not matter.
# Existing process variables, including Render's GEMINI_API_KEY, stay in place.
load_dotenv(Path(__file__).resolve().parent / ".env")


def _port() -> int:
    raw = os.environ.get("PORT", "10000")
    try:
        port = int(raw)
    except ValueError:
        raise SystemExit("PORT must be an integer.") from None
    if not 0 < port < 65536:
        raise SystemExit("PORT must be between 1 and 65535.")
    return port


def _transport_security() -> TransportSecuritySettings:
    """Allow local checks and this Render service's public hostname."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    configured = [os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")]
    configured.extend(os.environ.get("PROMPTDRY_ALLOWED_HOSTS", "").split(","))
    for name in [item.strip() for item in configured if item.strip()]:
        hosts.extend([name, f"{name}:*"])
        origins.extend([f"https://{name}", f"https://{name}:*", f"http://{name}", f"http://{name}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# Package specs stay free of < and >. `mcp dev` on Windows passes them through
# cmd.exe, which treats those characters as redirection.
mcp = FastMCP(
    "promptdry",
    host="0.0.0.0",
    port=_port(),
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security(),
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


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Render readiness probe. It does not call Gemma or read the prompt cache."""
    return JSONResponse({"status": "ok"})


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
    mcp.run(transport="streamable-http")
