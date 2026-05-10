"""Environment + LLM client factory (Pattern 1 from code-to-docs adoption).

Uses the OpenAI Python SDK pointed at any OpenAI-compatible endpoint.
By default, points at Gemini's compat endpoint, reads `GEMINI_API_KEY`
directly (the secret name already configured on `antedotee/krkn-website`).

Override via env vars to swap to vLLM, OpenAI, Ollama, etc. without
touching code:
    MODEL_API_BASE=https://api.openai.com/v1
    MODEL_API_KEY=sk-...
    MODEL_NAME=gpt-4o-mini

Reading priority for the API key:
    MODEL_API_KEY (explicit override) > GEMINI_API_KEY (default)
"""
import os
from typing import Any


# Defaults — override via env vars
_DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
_DEFAULT_MODEL_NAME = "gemini-2.5-flash"
_DEFAULT_MAX_CONTEXT_CHARS = 200_000  # ~50K tokens, our D13 cap


def _read_api_key() -> str:
    """Return the API key, preferring MODEL_API_KEY over GEMINI_API_KEY.

    Allows a user to override the key for one workflow run (e.g. trying
    OpenAI) without unsetting their permanent GEMINI_API_KEY secret.
    """
    return os.environ.get("MODEL_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


def get_api_base() -> str:
    return os.environ.get("MODEL_API_BASE") or _DEFAULT_API_BASE


def get_model_name() -> str:
    return os.environ.get("MODEL_NAME") or _DEFAULT_MODEL_NAME


def get_max_context_chars() -> int:
    raw = os.environ.get("MAX_CONTEXT_CHARS", "")
    if not raw:
        return _DEFAULT_MAX_CONTEXT_CHARS
    try:
        return int(raw)
    except ValueError:
        # Don't let a typo in env config silently disable budget enforcement.
        # Fall back to default and surface a warning at runtime.
        print(f"warning: MAX_CONTEXT_CHARS={raw!r} is not an integer; using default")
        return _DEFAULT_MAX_CONTEXT_CHARS


def get_client() -> Any:
    """Return a configured OpenAI-compatible client.

    The `openai` SDK is imported lazily so that test code that doesn't need
    the client doesn't pay the import cost (and so unit tests can run
    without the package installed if they mock around it).
    """
    from openai import OpenAI  # type: ignore

    return OpenAI(
        base_url=get_api_base(),
        api_key=_read_api_key(),
    )


_CONTENT_TRUNCATE_MARKER = "\n\n[content truncated to fit context budget]"
_DIFF_TRUNCATE_MARKER = "\n\n[diff truncated to fit context budget]"


def _truncate_with_marker(text: str, cap: int, marker: str) -> str:
    """Truncate `text` so the marker fits within `cap` total characters."""
    if len(text) <= cap:
        return text
    # Reserve marker length plus a small buffer for `.rstrip()` shrinkage.
    keep = max(0, cap - len(marker))
    return text[:keep].rstrip() + marker[: cap - keep] if keep > 0 else marker[:cap]


def truncate_content(text: str, max_chars: int | None = None) -> str:
    """Hard-cap content length to fit within the context budget.

    Returns the original if under the limit; otherwise truncates and
    appends a clear marker so the LLM can recognize the cutoff.
    """
    cap = max_chars if max_chars is not None else get_max_context_chars()
    return _truncate_with_marker(text, cap, _CONTENT_TRUNCATE_MARKER)


def truncate_diff(diff: str, max_chars: int | None = None) -> str:
    """Same as truncate_content but with a diff-aware marker."""
    cap = max_chars if max_chars is not None else get_max_context_chars()
    return _truncate_with_marker(diff, cap, _DIFF_TRUNCATE_MARKER)
