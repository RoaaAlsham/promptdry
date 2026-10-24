"""Server-side estimates; o200k_base counts approximate other models' tokenizers.

Counts cover the supplied text only, excluding chat framing, tools, and hidden
reasoning. Output tiers are heuristic ranges, not measured model predictions.
"""

import math
import os
from collections.abc import Mapping
from decimal import Decimal
from typing import TypedDict

import tiktoken

BASE_TIERS = {
    "classification": (5, 20, 60),
    "extraction": (40, 150, 500),
    "summarization": (100, 300, 700),
    "code": (200, 600, 2000),
    "reasoning": (300, 900, 3000),
    "open_ended": (200, 600, 2000),
}


class Tiers(TypedDict):
    low: int
    medium: int
    high: int


class Costs(TypedDict):
    low: float
    medium: float
    high: float


class Gate(TypedDict):
    skipped: bool
    reason: str


def _nonnegative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def count_tokens(text: str) -> int:
    """Approximate target-model input tokens with o200k_base, treating all text literally.

    tiktoken caches the encoding in memory and downloads its vocabulary on first
    use if absent from its disk cache. No prompt text is sent over the network.
    """
    return len(tiktoken.get_encoding("o200k_base").encode_ordinary(text))


def output_tiers(properties: Mapping[str, object]) -> Tiers:
    """Apply multiplicative modifiers, round down once, then cap by word limit.

    Missing task_type defaults to open_ended. Unsupported task types are errors.
    A word hint caps all three tiers only when format_constrained is true.
    """
    task = properties.get("task_type", "open_ended")
    if task not in BASE_TIERS:
        raise ValueError("Unknown task_type")
    low, medium, high = BASE_TIERS[task]
    # Decimal avoids losing a token to binary floating-point rounding.
    medium, high = Decimal(medium), Decimal(high)
    if properties.get("needs_search_or_tools", False):
        medium *= Decimal("1.3")
        high *= 2
    if properties.get("multi_step_reasoning", False):
        medium *= Decimal("1.3")
        high *= Decimal("1.5")
    values = [low, int(medium), int(high)]
    words = properties.get("length_hint_words")
    if properties.get("format_constrained", False) and words is not None:
        _nonnegative_int(words, "length_hint_words")
        cap = int(Decimal(words) * Decimal("1.4"))
        values = [min(value, cap) for value in values]
    return Tiers(low=values[0], medium=values[1], high=values[2])


def reasoning_overhead(properties: Mapping[str, object]) -> bool:
    """Flag possible reasoning overhead without adding invented reasoning tokens."""
    return bool(properties.get("multi_step_reasoning", False) or
                properties.get("task_type") == "reasoning")


def cost_range(
    prices: Mapping[str, object] | None, input_tokens: int, tiers: Tiers
) -> Costs | None:
    """USD for input plus each output tier, using per-million-token base prices."""
    if prices is None:
        return None
    _nonnegative_int(input_tokens, "input_tokens")
    rates = []
    for key in ("input_per_m", "output_per_m"):
        value = prices[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Prices must be finite nonnegative numbers")
        if not math.isfinite(value) or value < 0:
            raise ValueError("Prices must be finite nonnegative numbers")
        rates.append(Decimal(str(value)))
    values = [_nonnegative_int(tiers[key], key) for key in ("low", "medium", "high")]
    if values != sorted(values):
        raise ValueError("Output tiers must be monotonic")
    costs = [float((input_tokens * rates[0] + count * rates[1]) / 1_000_000)
             for count in values]
    return Costs(low=costs[0], medium=costs[1], high=costs[2])


def gate(prompt_tokens: int, min_tokens: int | None = None) -> Gate:
    """Skip strictly below the threshold; explicit threshold overrides environment.

    PROMPTDRY_MIN_TOKENS defaults to 40. Environment/.env loading belongs to the
    application entry point; this helper reads the environment on each call.
    """
    _nonnegative_int(prompt_tokens, "prompt_tokens")
    if min_tokens is None:
        try:
            min_tokens = int(os.getenv("PROMPTDRY_MIN_TOKENS", "40"))
        except ValueError:
            raise ValueError("PROMPTDRY_MIN_TOKENS must be a nonnegative integer") from None
    _nonnegative_int(min_tokens, "min_tokens")
    skipped = prompt_tokens < min_tokens
    return Gate(skipped=skipped, reason="below_min_tokens" if skipped else "")
