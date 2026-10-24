"""Prompt pre-flight orchestration with a process-local, one-hour cache."""

from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

from promptdry.estimate import count_tokens, cost_range, gate, output_tiers, reasoning_overhead
from promptdry.gemma import BothResult, EnhanceResult, Evaluation, ReduceResult, call_json, render
from promptdry.pricing import resolve_model

Mode = Literal["reduce", "enhance", "both"]
PROPERTY_FIELDS = ("task_type", "needs_search_or_tools", "multi_step_reasoning",
                   "format_constrained", "length_hint_words")


class EvaluationExpiredError(ValueError):
    def __init__(self):
        super().__init__("evaluation_expired: evaluate the prompt again")


@dataclass
class CachedEvaluation:
    prompt: str
    evaluation: Evaluation
    model_profile: dict
    input_tokens: int
    tiers: dict
    expires_at: float


class PromptService:
    """Cache lifetime starts after successful evaluation; rewrites do not extend it."""

    def __init__(self, *, clock=monotonic):
        self._clock = clock
        self._cache: dict[str, CachedEvaluation] = {}
        self._lock = Lock()

    def _prune(self):
        # Caller holds the lock; never hold it while making API calls.
        now = self._clock()
        for key in [key for key, entry in self._cache.items() if entry.expires_at <= now]:
            del self._cache[key]

    def evaluate(self, prompt: str, model_id: str) -> dict:
        profile = resolve_model(model_id)
        tokens = count_tokens(prompt)
        decision = gate(tokens)
        public_profile = {key: value for key, value in profile.items() if key != "prices"}
        with self._lock:
            self._prune()
        if decision["skipped"]:
            # No fabricated analysis or cache handle for a prompt we did not evaluate.
            return {"evaluation_id": None, "gate": decision, "model_profile": public_profile,
                    "input_tokens": tokens, "output_tokens": None, "cost_usd": None,
                    "reasoning_overhead": None, "properties": None, "scores": None,
                    "slots": None, "missing_slots": [], "issues": []}

        evaluation = call_json(render("evaluate", PROMPT=prompt), Evaluation)
        data = evaluation.model_dump()
        properties = {key: data[key] for key in PROPERTY_FIELDS}
        tiers = output_tiers(properties)
        costs = cost_range(profile["prices"] if profile["status"] == "resolved" else None, tokens, tiers)
        evaluation_id = "ev_" + uuid4().hex
        with self._lock:
            self._prune()
            self._cache[evaluation_id] = CachedEvaluation(
                prompt, evaluation.model_copy(deep=True), deepcopy(profile), tokens,
                tiers.copy(), self._clock() + 3600,
            )
        return {"evaluation_id": evaluation_id, "gate": decision,
                "model_profile": public_profile, "input_tokens": tokens,
                "output_tokens": tiers, "cost_usd": costs,
                "reasoning_overhead": reasoning_overhead(properties), "properties": properties,
                "scores": {"efficiency": evaluation.efficiency_score,
                           "specification": evaluation.specification_score},
                "slots": data["slots"], "missing_slots": data["missing_slots"], "issues": data["issues"]}

    def improve(self, evaluation_id: str, mode: Mode, answers: str | None = None) -> dict:
        with self._lock:
            self._prune()
            entry = self._cache.get(evaluation_id)
            if entry is None:
                raise EvaluationExpiredError()
        if mode not in ("reduce", "enhance", "both"):
            raise ValueError("mode must be reduce, enhance, or both")
        if answers is not None and not isinstance(answers, str):
            raise ValueError("answers must be a string or None")
        properties = {key: getattr(entry.evaluation, key) for key in PROPERTY_FIELDS}
        template = render(mode, PROMPT=entry.prompt,
                          MISSING_SLOTS=entry.evaluation.missing_slots,
                          PROPERTIES=properties, ANSWERS=answers or "")
        result = call_json(template, {"reduce": ReduceResult, "enhance": EnhanceResult,
                                      "both": BothResult}[mode])
        rewritten = result.rewritten_prompt
        before_tokens = count_tokens(entry.prompt)
        after_tokens = count_tokens(rewritten)
        status = "ready" if mode == "reduce" else result.status
        removed = list(getattr(result, "removed", []))
        added = list(getattr(result, "added_slots", []))
        assumptions = list(getattr(result, "assumptions", []))
        note = result.note
        effect = getattr(result, "expected_output_effect", "similar")
        risk = getattr(result, "risk", "medium")
        if mode == "reduce" and after_tokens >= before_tokens:
            rewritten, after_tokens = entry.prompt, before_tokens
            removed, risk, effect = [], "low", "similar"
            note = "Original retained: the proposed reduction did not use fewer input tokens."
        pending = status == "needs_answers"
        if pending:
            note = "Draft only; awaiting answers. Token counts are provisional; no savings claimed."
            effect = "n/a"
        return {
            "status": status,
            "questions": [question.model_dump() for question in getattr(result, "questions", [])],
            "rewritten_prompt": rewritten,
            "changes": {"removed": removed, "added_slots": added, "assumptions": assumptions},
            "before": {"input_tokens": before_tokens, "output_tokens": output_tiers(properties)},
            "after": {"input_tokens": after_tokens, "output_tokens": output_tiers(properties)},
            "net": {"input_delta": 0 if pending else after_tokens - before_tokens,
                    "expected_output_effect": effect, "note": note},
            "risk": risk,
        }


_service = PromptService()


def evaluate(prompt: str, model_id: str) -> dict:
    return _service.evaluate(prompt, model_id)


def improve(evaluation_id: str, mode: Mode, answers: str | None = None) -> dict:
    return _service.improve(evaluation_id, mode, answers)
