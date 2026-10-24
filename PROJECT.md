Project: promptdry — an MCP server that dry-runs a prompt before an agent executes it.
Goal: estimate token consumption (input exact-ish; output as low/medium/high), score clarity
and specification, and offer rewrites (reduce tokens / improve specification / both).
Evaluation and rewriting use Gemma 4 through the Gemini API (google-genai SDK; default model
env GEMMA_MODEL=gemma-4-26b-a4b-it; key in GEMINI_API_KEY).
Tools: exactly two MCP tools — evaluate_prompt, improve_prompt (contracts in CONTRACTS.md).
Constraints: Python 3.13+, FastMCP (mcp package), pydantic v2, tiktoken, llm_costs, pytest.
Do not rely on system_instruction or JSON-schema mode for Gemma; put instructions in the user
message and validate output with pydantic (repair once, then fail cleanly).
Never log prompt text or API keys. Hackathon scope: no database, no auth, in-memory cache.
Token counts and costs are computed by the server, never by the LLM.
Prompt templates live in prompts/*.md with {{PLACEHOLDER}} variables (evaluate, reduce,
enhance, both, repair).
Work in small steps; after each step run pytest and report what passed.


