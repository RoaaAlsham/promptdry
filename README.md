# promptdry

An MCP server that dry-runs a prompt before an agent executes it. It estimates
tokens and cost, scores how well the prompt is specified, and can rewrite it.
`main` speaks stdio for a local desktop client. `streamable-http` serves
[https://promptdry.onrender.com/mcp](https://promptdry.onrender.com/mcp).
Read PROJECT.md for constraints and CONTRACTS.md for the tool results.

## Core features

- **`evaluate_prompt(prompt, model_id)`** counts input tokens, estimates output as low / medium / high, and prices that range when the model is known. It scores efficiency and specification from 1 to 5 and lists missing slots plus a few short issues.
- **`improve_prompt(evaluation_id, mode, answers?)`** rewrites the original prompt. `mode` is `reduce` (fewer input tokens), `enhance` (clearer specification), or `both`.
- Token counts and dollar amounts come from the server (tiktoken and llm-costs). Gemma does not invent them.
- Prompts under `PROMPTDRY_MIN_TOKENS` (default 40) are skipped, and Gemma is not called.
- A successful evaluation is cached in memory for one hour. `evaluation_expired` means call `evaluate_prompt` again.
- Prompt text and API keys are not logged. The prompt is sent to the Gemini API for analysis, so keep secrets out of it.

## How to use it

Connect once, then use the two tools at the start of a new task.

**Hosted connector** (`streamable-http`): in Claude, add a custom connector and set the URL to `https://promptdry.onrender.com/mcp`. That path is where the server accepts MCP requests.

**Local desktop** (`main`): launch `server.py` over stdio with the project virtualenv. That branch's MCP server section has the Claude Desktop command.

Then:

1. Call `evaluate_prompt` with the user's message and the model id you will actually run, such as `gpt-4o`. Pass `unknown` when you do not know it.
2. Read the pre-flight: input tokens, output range, cost range, the two scores, and the issues. If `gate.skipped` is true, continue the task. If `cost_usd` is null, pricing for that model is unavailable.
3. Ask which to do next: proceed as written, reduce tokens, improve the specification, or both.
4. For a rewrite, call `improve_prompt` with the `evaluation_id`. If `status` is `needs_answers`, show the questions, wait, and call again with `answers`. If `status` is `ready`, show the new prompt, the before/after token counts, and the risk, and wait for approval.
5. Run the task with the prompt the user approved.

Skip the pre-flight for a follow-up in the same task, for a slash command, and when the user writes "skip eval".

## Simple demo

This call was made against `https://promptdry.onrender.com/mcp` with `model_id` `gpt-4o`.

Prompt:

> Please write a detailed weekend city-break plan for a first-time visitor. Include a packing list, a daily itinerary, restaurant ideas, and budget tips. Repeat the important rules a few times so nothing is missed, and add extra background about why this trip should feel special and well organized.

**Prompt pre-flight** — model: openai/gpt-4o

- Input: 57 tokens
- Output estimate: low 200 · medium 780 · high 3000 tokens
- Cost estimate: low $0.0021 · medium $0.0079 · high $0.0301
- Efficiency: 2/5 · Specification: 2/5
- Issues: missing destination, missing budget, redundant instruction to repeat rules, vague constraints

`improve_prompt` with mode `reduce` returned `ready` and cut the input from 57 tokens to 29:

> Write a detailed weekend city-break plan for a first-time visitor. Include a packing list, a daily itinerary, restaurant ideas, and budget tips.

The output-tier estimates stayed the same. Risk was low. Output tiers are heuristics, and a later call can produce different numbers.

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

`https://promptdry.onrender.com/mcp`

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

