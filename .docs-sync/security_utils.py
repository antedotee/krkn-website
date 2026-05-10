"""Security utilities — sanitize subprocess output, validate paths.

Adapted from redhat-community-ai-tools/code-to-docs (MIT). Pattern 3 in our
plan. The secret-name list is replaced with the krkn-docs-sync set:
  - GEMINI_API_KEY, MODEL_API_KEY (LLM credentials)
  - WEBSITE_DISPATCH_PAT (cross-repo PAT)
  - GITHUB_TOKEN, GH_TOKEN (GitHub Actions tokens)

A bot that runs on every merge with credentialed access has a real
exfiltration risk if any subprocess output containing tokens hits a log.
This module makes redaction the default for ALL subprocess calls.
"""
import os
import subprocess
from pathlib import Path


# Environment variables whose values must NEVER appear in logs.
# Adapted from code-to-docs's list — added our specific secrets, dropped Jira.
_SENSITIVE_ENV_VARS = (
    "GEMINI_API_KEY",
    "MODEL_API_KEY",
    "WEBSITE_DISPATCH_PAT",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def sanitize_output(text: str | None, sensitive_tokens: list[str] | None = None) -> str | None:
    """Replace any sensitive token values found in `text` with `***TOKEN***`.

    Reads the values of well-known secret env vars at call time, so this
    works even if secrets are loaded after import.

    Args:
        text: The text to sanitize. Returned unchanged if None or empty.
        sensitive_tokens: Optional additional token values to redact.

    Returns:
        Sanitized text, or the original if nothing matched.
    """
    if not text:
        return text

    tokens = list(sensitive_tokens) if sensitive_tokens else []
    for env_var in _SENSITIVE_ENV_VARS:
        val = os.environ.get(env_var, "")
        if val:
            tokens.append(val)

    sanitized = text
    for token in tokens:
        if token:
            sanitized = sanitized.replace(token, "***TOKEN***")
    return sanitized


def run_command_safe(
    cmd: list[str],
    check: bool = False,
    capture_output: bool = True,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run subprocess.run() with stdout/stderr sanitized of secret values.

    On failure (including unexpected exceptions), all error messages are
    sanitized before being raised — so a leaked token can't end up in
    GitHub Actions logs via a stack trace.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            **kwargs,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                cmd,
                output=sanitize_output(result.stdout),
                stderr=sanitize_output(result.stderr),
            )
        return result
    except subprocess.CalledProcessError as e:
        e.stderr = sanitize_output(str(e.stderr)) if e.stderr else None
        e.stdout = sanitize_output(str(e.stdout)) if e.stdout else None
        raise
    except Exception as e:
        e.args = tuple(sanitize_output(str(arg)) for arg in e.args)
        raise


def validate_path(file_path: str | Path, base_dir: str | Path | None = None) -> bool:
    """Return True if `file_path` resolves to a path inside `base_dir`.

    Rejects directory traversal (`..`) and absolute paths that escape the
    workspace. Used before any read/write to ensure the agent can't reach
    outside the website checkout.
    """
    base = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
    try:
        resolved = (base / Path(file_path)).resolve()
        resolved.relative_to(base)
        return True
    except (ValueError, OSError):
        return False
