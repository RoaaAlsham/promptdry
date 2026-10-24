# promptdry

An MCP server for estimating prompt token consumption, scoring specification,
and offering rewrites before an agent executes a task. Implementation is in progress.
Read PROJECT.md for constraints and CONTRACTS.md for the two planned MCP tools.

## Setup (Python 3.13+)

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

This checkout already has a working `.venv`. To recreate it using the local
Python installation, use `& "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe" -m venv .venv`.
Activation is optional: invoke `.\.venv\Scripts\python.exe` directly instead
of `python` if PowerShell execution policy prevents activation.

macOS/Linux:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Create `.env` only if it does not already exist. Set `GEMINI_API_KEY` locally;
never commit or share it. `GEMMA_MODEL` defaults to `gemma-4-26b-a4b-it` and
`PROMPTDRY_MIN_TOKENS` defaults to 40.

```sh
python smoke.py
python -m pytest
```

The smoke test lists available model names containing `gemma`, then sends a
minimal request to the configured model and prints its response. It requires
network access and a valid Gemini API key; the API call may incur charges.
Tests run offline. Input prompts are not logged.

## MCP server

This branch serves Streamable HTTP. The `main` branch keeps the stdio server
used by Claude Desktop's local command config.

`python server.py` listens on `0.0.0.0` and `$PORT` (default `10000`). The MCP
endpoint is `/mcp`. `GET /health` returns `{"status":"ok"}` and does not call
Gemma. Responses are JSON and each HTTP request is stateless; the evaluation
cache still lives in this process for one hour. A restart or a spun-down
Render instance drops it. Prompt text and API keys are not logged.

Run it locally:

```powershell
$env:PORT = "10000"
.\.venv\Scripts\python.exe server.py
```

Then connect a client to `http://127.0.0.1:10000/mcp`. A short prompt returns
a skipped gate. `improve_prompt` with an unknown `evaluation_id` returns
`evaluation_expired`.

## Render

`render.yaml` is a Blueprint for the `streamable-http` branch. It installs
`requirements.txt`, starts `python server.py`, and probes `/health`. Python
is pinned to 3.14.4. Set `GEMINI_API_KEY` in the Render dashboard when the
Blueprint asks for it. `GEMMA_MODEL` defaults to `gemma-4-26b-a4b-it`.

After deploy, the public endpoint is:

`hhttps://promptdry.onrender.com`

Render sends the service hostname in `RENDER_EXTERNAL_HOSTNAME`, and the
server allows that host. For an extra custom domain, set
`PROMPTDRY_ALLOWED_HOSTS` to a comma-separated list of hostnames.

Claude Desktop remote server entry:

```json
{
  "mcpServers": {
    "promptdry": {
      "url": "https://promptdry.onrender.com/mcp"
    }
  }
}
```

Replace the host with the hostname Render assigns. Free instances sleep when
idle; the first call after sleep waits for the process to start.

## Layout

- `server.py`: Streamable HTTP MCP entry point
- `render.yaml`: Render Blueprint for this branch
- `promptdry/`: service package
- `prompts/`: evaluate, reduce, enhance, both, and repair templates
- `tests/`: local tests
- `examples/`: reserved for example prompts

Templates are copied from `promptdry prompts.md` and use `{{PLACEHOLDER}}` variables.


Python 3.13 or newer is required by llm-costs. This checkout uses Python 3.14.4.
The MCP SDK is constrained to version 1.x to retain `mcp.server.fastmcp.FastMCP`.

## Model pricing

`promptdry.pricing.resolve_model(model_id)` normalizes whitespace and case,
infers the provider for Claude, GPT/o1/o3/o4, and Gemini/Gemma names, then
tries the exact name, an explicit alias, and a trailing `-YYYYMMDD` or
`-YYYY-MM-DD` removal. Each candidate checks overrides before library prices.
The library's own aliases resolve to their canonical model name.
Unknown names return `status: "needs_model"` with `resolved`, `prices`,
`pricing_source`, and `as_of` set to null. Missing prices are not zero prices.
`promptdry.estimate.cost_range` returns null for missing prices.

`prices_override.json` uses provider/model keys. For example, to configure a
fictional model with supplied USD rates per million tokens:

```json
{
  "example/my-model": {
    "input_per_m": 1.0,
    "output_per_m": 4.0,
    "source": "override",
    "as_of": "2026-10-24"
  }
}
```

Both rate fields are required, finite, nonnegative numbers. `source` defaults
to `override`; `as_of` may be null or omitted when the pricing date is unknown.
Malformed configuration raises a short error instead of silently ignoring it.

`aliases.json` maps shorthand to a model ID (one hop, no alias chains):

```json
{"my-model": "example/my-model"}
```

Both config files default to empty objects and are located relative to the
project, independent of the working directory. Missing files mean no entries.
The `llm-costs` 0.1.6 API was inspected locally: `get_model_pricing` returns
`input`/`output` in USD per million tokens, while `list_providers` and
`list_models` are exported by `llm_costs.calculator`. The adapter validates
the bundled YAML currency/unit and preserves its version as `as_of`, rather
than claiming the rates were verified today. These are standard base rates;
cache, batch, long-context, and provider-specific charges are not included.

## Token and cost estimates

`promptdry.estimate.count_tokens(text)` uses tiktoken's `o200k_base` vocabulary.
Counts are approximate for other models and exclude message framing, tool
definitions, and hidden reasoning. Literal special-token strings are counted
as ordinary text. The vocabulary is downloaded and cached on first use;
prompt text is never uploaded by the counter.

`output_tiers(properties)` returns `low`, `medium`, and `high` token estimates.
Search/tool and multi-step modifiers compound; results round down once after
all modifiers. A constrained word limit caps each tier at `int(words * 1.4)`.
`reasoning_overhead(properties)` separately returns whether the task is
reasoning or requires multi-step reasoning, without inventing extra tokens.

`cost_range(prices, input_tokens, tiers)` computes each USD estimate as
`(input_tokens * input_per_m + output_tokens * output_per_m) / 1_000_000`.
Output ranges are heuristics, not guarantees.

`gate(prompt_tokens, min_tokens=None)` skips strictly below the threshold.
An explicit threshold overrides `PROMPTDRY_MIN_TOKENS` (default 40); zero
disables skipping. Invalid or negative thresholds raise a clear error.
The application entry point is responsible for loading `.env`.

## Gemma client

`promptdry.gemma.render(name, **variables)` fills the five prompt templates in
one pass and escapes literal `</prompt>` delimiters in `PROMPT` values.
`call_json(rendered_text, model_cls)` sends a user message at temperature zero
and validates against `Evaluation`, `ReduceResult`, `EnhanceResult`, or
`BothResult`. No system instruction, JSON-schema response mode, or JSON MIME
support is required. Load `.env` at the application entry point before calling.
Thinking is explicitly disabled with `thinking_level="minimal"`, as documented
for Gemma 4, to avoid unnecessary reasoning latency for prompt analysis.

Fenced or surrounded JSON is accepted. Invalid responses get one repair call
containing the original instructions and required structure as user text.
Each generation allows three total attempts for HTTP 429/5xx, with exponential
backoff and jitter and a 30-second timeout per attempt. SDK retries are disabled
to avoid multiplying attempts. Other transport/API failures fail immediately.
Failures raise a sanitized `GemmaOutputError`; prompt text, returned content,
and API keys are not logged. Tests inject a fake client and never call Gemini.

## Core service

`promptdry.service.evaluate(prompt, model_id)` profiles the target model before
token counting, skips short prompts without calling Gemma, and returns the
evaluation contract with server-calculated costs and token estimates. Unresolved
models still receive analysis and token estimates, but no cost estimate.

`improve(evaluation_id, mode, answers=None)` supports `reduce`, `enhance`, and
`both`. Each call works from the original cached prompt. Rewrites do not replace
it or extend the cache lifetime. Reduce retains the original unless the rewrite
has strictly fewer input tokens. Drafts awaiting answers make no savings claim.

The process-local dictionary expires evaluations after one hour using a monotonic
clock, and removes expired entries on subsequent service calls. Restarting the
process loses evaluations. Missing/expired IDs raise `EvaluationExpiredError`
with `evaluation_expired`; evaluate again to obtain a new ID. A lock protects
cache operations without serializing network calls. The application entry point
must load `.env` before using the module-level service functions.

