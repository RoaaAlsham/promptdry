# Shared contracts

**`evaluate_prompt(prompt, model_id)` returns:**

```json
{
  "evaluation_id": "ev_ab12",
  "gate": {"skipped": false, "reason": ""},
  "model_profile": {"requested": "…", "resolved": "provider/model",
                    "status": "resolved|needs_model",
                    "pricing_source": "llm_costs|override", "as_of": "…"},
  "input_tokens": 0,
  "output_tokens": {"low": 0, "medium": 0, "high": 0},
  "cost_usd": {"low": 0.0, "medium": 0.0, "high": 0.0},
  "reasoning_overhead": false,
  "properties": {"task_type": "", "needs_search_or_tools": false,
                 "multi_step_reasoning": false, "format_constrained": false,
                 "length_hint_words": null},
  "scores": {"efficiency": 1, "specification": 1},
  "slots": {"goal": true, "context": true, "constraints": true,
            "output_format": false, "success_criteria": false, "examples_or_inputs": true},
  "missing_slots": ["output_format", "success_criteria"],
  "issues": []
}
```

`cost_usd` is `null` when pricing is unresolved.

When `gate.skipped` is true, no Gemma analysis runs and no cache entry is created.
`evaluation_id`, `output_tokens`, `cost_usd`, `reasoning_overhead`, `properties`,
`scores`, and `slots` are null; `missing_slots` and `issues` are empty arrays.
`model_profile`, `input_tokens`, and `gate` are still populated. These null values
mean not evaluated, rather than zero output or a fabricated score.

**`improve_prompt(evaluation_id, mode, answers?)`**, with `mode` one of `reduce | enhance | both`, returns:

```json
{
  "status": "ready|needs_answers",
  "questions": [{"slot": "", "question": "", "suggested_default": ""}],
  "rewritten_prompt": "",
  "changes": {"removed": [], "added_slots": [], "assumptions": []},
  "before": {"input_tokens": 0, "output_tokens": {"low":0,"medium":0,"high":0}},
  "after":  {"input_tokens": 0, "output_tokens": {"low":0,"medium":0,"high":0}},
  "net": {"input_delta": 0, "expected_output_effect": "shorter|similar|longer|n/a", "note": ""},
  "risk": "low|medium|high"
}
```

Token counts always come from the server (tiktoken), never from Gemma.

For `needs_answers`, the rewritten prompt is a draft. Its `after` counts are
provisional; `net.input_delta` is a neutral zero (not a calculated savings claim),
`expected_output_effect` is `n/a`, and the note states that answers are pending.
For `ready`, `input_delta` is after minus before; negative values mean fewer tokens.
Before/after output tiers use the same original properties, so they remain equal;
the separate output-effect label is Gemma's qualitative assessment. Reduce uses
`similar` because its template has no output-effect field. Enhance uses a
conservative `medium` risk because its template has no risk field.

