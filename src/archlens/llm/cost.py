"""Token estimates and cost accounting (docs/LLM.md §9).

Estimates use characters / 3.5 — a deliberate over-estimate for code and JSON (ADR-016; tiktoken
would download its encodings at runtime). Estimates only feed the budget guard and the rate
limiter; recorded costs always use the provider's reported usage.
"""

import json
import math

from archlens.config import Price
from archlens.llm.types import ChatMessage, Usage

CHARS_PER_TOKEN = 3.5
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def estimate_messages(messages: list[ChatMessage]) -> int:
    return sum(estimate_tokens(json.dumps(m, ensure_ascii=False)) for m in messages)


def call_cost(price: Price, usage: Usage) -> float:
    """USD: uncached input + cached input + output (output includes reasoning)."""
    uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
    return (
        uncached * price.input
        + usage.cached_input_tokens * price.cached_input
        + usage.output_tokens * price.output
    ) / 1_000_000


def worst_case_cost(price: Price, input_tokens: int, max_output_tokens: int) -> float:
    """Upper bound used by the budget guard: no cache hits, every output token used."""
    return (input_tokens * price.input + max_output_tokens * price.output) / 1_000_000
