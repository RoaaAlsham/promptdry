"""Gemma JSON generation with local validation, one repair, and bounded retries."""

import json
import os
import random
import re
import time
import httpx
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_MODEL = "gemma-4-26b-a4b-it"
Slot = Literal["goal", "context", "constraints", "output_format", "success_criteria", "examples_or_inputs"]
Risk = Literal["low", "medium", "high"]
Score = Annotated[int, Field(ge=1, le=5)]


class GemmaOutputError(RuntimeError):
    """Safe, actionable failure with no response, prompt, or credential details."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Slots(StrictModel):
    goal: bool
    context: bool
    constraints: bool
    output_format: bool
    success_criteria: bool
    examples_or_inputs: bool


class Evaluation(StrictModel):
    task_type: Literal["classification", "extraction", "summarization", "code", "reasoning", "open_ended"]
    needs_search_or_tools: bool
    multi_step_reasoning: bool
    format_constrained: bool
    length_hint_words: Annotated[int, Field(ge=0)] | None
    slots: Slots
    specification_score: Score
    efficiency_score: Score
    issues: list[str] = Field(max_length=5)
    missing_slots: list[Slot]

    @field_validator("issues")
    @classmethod
    def short_issues(cls, values):
        if any(len(value.split()) >= 12 for value in values):
            raise ValueError("Each issue must be under 12 words")
        return values

    @model_validator(mode="after")
    def consistent_slots(self):
        missing = {key for key, present in self.slots.model_dump().items() if not present}
        if set(self.missing_slots) != missing or len(self.missing_slots) != len(missing):
            raise ValueError("missing_slots must match false slots without duplicates")
        return self


class ReduceResult(StrictModel):
    rewritten_prompt: str = Field(min_length=1)
    removed: list[str] = Field(max_length=8)
    preserved_check: list[str] = Field(max_length=8)
    risk: Risk
    note: str


class Question(StrictModel):
    slot: Slot
    question: str = Field(min_length=1)
    suggested_default: str


class EnhanceResult(StrictModel):
    status: Literal["needs_answers", "ready"]
    questions: list[Question] = Field(max_length=3)
    rewritten_prompt: str = Field(min_length=1)
    added_slots: list[Slot]
    assumptions: list[str]
    expected_output_effect: Literal["shorter", "similar", "longer"]
    note: str

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "ready" and (self.questions or "[PLACEHOLDER:" in self.rewritten_prompt):
            raise ValueError("Ready results must have no questions or placeholders")
        if self.status == "needs_answers" and not self.questions:
            raise ValueError("needs_answers requires questions")
        return self


class BothResult(EnhanceResult):
    removed: list[str]
    risk: Risk


def render(template_name: str, **variables: object) -> str:
    """Render allowed templates in one pass, leaving inserted content untouched."""
    name = template_name.removesuffix(".md")
    if name not in {"evaluate", "reduce", "enhance", "both", "repair"}:
        raise ValueError("Unknown prompt template")
    template = (PROMPTS / f"{name}.md").read_text(encoding="utf-8-sig")

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise ValueError(f"Missing template variable: {key}")
        value = variables[key]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text.replace("</prompt>", "&lt;/prompt&gt;") if key == "PROMPT" else text

    return re.sub(r"\{\{([A-Z_]+)\}\}", substitute, template)


T = TypeVar("T", bound=BaseModel)


def _validate(text: str, model_cls: type[T]) -> T:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object")
    return model_cls.model_validate_json(text[start:end + 1])


def _generate(client, model: str, contents: str) -> str:
    config = types.GenerateContentConfig(
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        http_options=types.HttpOptions(timeout=30_000,
                                      retry_options=types.HttpRetryOptions(attempts=1)),
    )
    for attempt in range(3):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            return response.text or ""
        except errors.APIError as exc:
            retryable = exc.code == 429 or (isinstance(exc.code, int) and 500 <= exc.code < 600)
            if not retryable or attempt == 2:
                status = exc.code if isinstance(exc.code, int) else "unknown"
                raise GemmaOutputError(f"Gemma request failed (HTTP {status}); check model access and quota.") from None
            time.sleep(2 ** attempt + random.uniform(0, 0.5))
        except (httpx.TimeoutException, TimeoutError):
            raise GemmaOutputError("Gemma request timed out (30-second timeout); retry later.") from None
        except httpx.TransportError:
            raise GemmaOutputError("Gemma connection failed; check network access.") from None
        except Exception:
            raise GemmaOutputError("Gemma request failed; check model access, quota, and network.") from None
    raise AssertionError("Unreachable")


def call_json(template: str, model_cls: type[T], *, client=None, model: str | None = None) -> T:
    """Send rendered text as a user message, validate, and repair at most once.

    No system instruction, JSON-schema mode, or MIME requirement is used.
    The caller loads .env before calling; genai.Client() reads GEMINI_API_KEY.
    An injected client is caller-owned; otherwise this function closes its client.
    """
    owned = client is None
    if owned:
        try:
            client = genai.Client()
        except Exception:
            raise GemmaOutputError("Cannot initialize Gemini; configure GEMINI_API_KEY.") from None
    try:
        selected = model or os.getenv("GEMMA_MODEL", DEFAULT_MODEL)
        output = _generate(client, selected, template)
        for attempt in range(2):
            try:
                return _validate(output, model_cls)
            except (ValueError, ValidationError):
                if attempt:
                    raise GemmaOutputError("Gemma returned invalid JSON after one repair; retry evaluation.") from None
                # Never stringify validation exceptions: they may include input values.
                repair = render("repair", ERROR="JSON parsing or response validation failed.",
                                PREVIOUS_OUTPUT=output)
                # Repeat original instructions and supply the schema as plain user text.
                # This is not API JSON-schema mode and does not require model support.
                contents = (template + "\n\n" + repair + "\nRequired response structure:\n" +
                            json.dumps(model_cls.model_json_schema(), ensure_ascii=False))
                output = _generate(client, selected, contents)
        raise AssertionError("Unreachable")
    finally:
        if owned:
            try:
                client.close()
            except Exception:
                pass
