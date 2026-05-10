"""Thin wrapper around the OpenAI-compatible client that adds the
production-time concerns the bot needs:

  - Retry with backoff for transient errors (uses utils.retry_with_backoff)
  - Token budget enforcement (prompts that would exceed get clipped or
    rejected — we never silently truncate without a marker the LLM can see)
  - Sanitized logging (subprocess.CalledProcessError style: secrets stripped
    from any error stack trace via security_utils)
  - Sensible defaults (low temperature for prose tasks; deterministic seed
    where supported)

The actual LLM client (the OpenAI SDK pointed at Gemini's compat endpoint)
lives in config.py. This module is the layer above.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import get_client, get_max_context_chars, get_model_name, truncate_content
from security_utils import sanitize_output
from utils import retry_with_backoff


# Conservative defaults for documentation prose tasks. We deliberately keep
# `temperature` low so the same input + same prompt produces near-identical
# output run-to-run — matters for the test suite and for review predictability.
_DEFAULT_TEMPERATURE = 0.2
_DEFAULT_MAX_OUTPUT_TOKENS = 2048

# Budget guardrails. The plan locked D13 at 50K input / 8K output. Default
# truncation handled by config.truncate_content; this is the OUTPUT cap.
_HARD_OUTPUT_TOKENS_CEILING = 8192


@dataclass
class LLMResponse:
    """Carrier for a single chat completion result.

    Keeping fields explicit (not the raw SDK object) makes it trivial to mock
    in tests and to swap out the SDK if Gemini's compat endpoint changes shape.
    """
    content: str
    model: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class TokenBudgetExceededError(RuntimeError):
    """Raised when an input prompt would exceed the configured context budget."""


def _approx_token_count(s: str) -> int:
    """Rough token estimate: chars/4. Overcounts for English prose, undercounts
    for code-heavy content. Good enough for budget enforcement (we err
    slightly conservative — better to reject early than 429 mid-run)."""
    return len(s) // 4


def _validate_budget(messages: list[dict]) -> int:
    """Sum approximate input tokens across messages and reject if over budget.
    Returns the input token estimate."""
    total = sum(_approx_token_count(m.get("content", "")) for m in messages)
    cap_chars = get_max_context_chars()
    cap_tokens = cap_chars // 4
    if total > cap_tokens:
        raise TokenBudgetExceededError(
            f"prompt exceeds budget: ~{total} tokens > cap {cap_tokens} "
            f"(MAX_CONTEXT_CHARS={cap_chars}). Reduce input size or raise cap."
        )
    return total


def _on_retry(attempt: int, max_retries: int, exc: Exception, wait: float) -> None:
    """Sanitized log line per retry attempt."""
    print(
        f"[llm_client] retry {attempt + 1}/{max_retries} after "
        f"{type(exc).__name__}: {sanitize_output(str(exc))} "
        f"(waiting {wait}s)"
    )


@retry_with_backoff(
    max_retries=3,
    delay_multiplier=2,
    on_retry=_on_retry,
    reraise=True,
)
def chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = _DEFAULT_TEMPERATURE,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> LLMResponse:
    """Send a chat-completion request and return a normalized response.

    Args:
        messages: list of `{"role": "...", "content": "..."}` dicts
        model: override config's MODEL_NAME (rarely needed)
        temperature: 0.0-2.0; default 0.2 for prose tasks
        max_output_tokens: capped at _HARD_OUTPUT_TOKENS_CEILING

    Raises:
        TokenBudgetExceededError if the prompt is over budget
        Any underlying API error (after retries exhausted)
    """
    _validate_budget(messages)

    cap = min(max_output_tokens, _HARD_OUTPUT_TOKENS_CEILING)
    client = get_client()
    model_name = model or get_model_name()

    completion = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=cap,
    )

    choice = completion.choices[0]
    usage = completion.usage
    return LLMResponse(
        content=choice.message.content or "",
        model=completion.model or model_name,
        finish_reason=choice.finish_reason or "unknown",
        prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
    )
