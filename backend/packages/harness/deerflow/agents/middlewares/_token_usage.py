"""How much a run has spent, counted once.

Two middlewares need this number and must agree on it: ``TokenBudgetMiddleware``
enforces the ceiling, and ``FinalizationDeadlineMiddleware`` warns before it so
the worker can still write its structured result. A deadline that counted
differently from the enforcer would warn at the wrong moment — early enough to
waste budget, or late enough to be useless — and the disagreement would be
invisible, because both numbers look plausible in isolation.

The accounting is not a plain sum over history. ``TokenUsageMiddleware``
retroactively rewrites a message's ``usage_metadata`` once its subagents report,
so the same message is observed several times with a growing value. Summing the
field would count those tokens repeatedly; what is accumulated is the *delta*
against the last value seen for that exact message id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage


@dataclass
class TokenUsage:
    input: int = 0
    output: int = 0
    total: int = 0


def accumulate_usage(messages: Iterable[Any], seen: dict[str, tuple[int, int]], usage: TokenUsage) -> TokenUsage:
    """Fold new provider-reported usage into ``usage``, mutating both arguments.

    ``seen`` maps a message id to the largest ``(input, output)`` already
    counted for it. Only increases are added, so a revised message contributes
    its growth and a re-observed one contributes nothing. A message with no id
    is skipped rather than guessed at: without a stable identity there is no way
    to tell a revision from a repeat.
    """
    for message in messages:
        if not isinstance(message, AIMessage) or not message.id:
            continue
        metadata = getattr(message, "usage_metadata", None) or {}
        input_tokens = metadata.get("input_tokens", 0)
        output_tokens = metadata.get("output_tokens", 0)
        previous_input, previous_output = seen.get(message.id, (0, 0))
        added_input = max(0, input_tokens - previous_input)
        added_output = max(0, output_tokens - previous_output)
        if added_input > 0 or added_output > 0:
            usage.input += added_input
            usage.output += added_output
            usage.total += added_input + added_output
            seen[message.id] = (input_tokens, output_tokens)
    return usage
