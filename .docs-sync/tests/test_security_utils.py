"""Tests for .docs-sync/security_utils.py.

A bot that runs on every merge with credentialed access has real
exfiltration risk. These tests are conservative — even silly false negatives
(redacting too much) is better than a leak.
"""
import os
import subprocess
from pathlib import Path

import pytest

from security_utils import (
    sanitize_output,
    run_command_safe,
    validate_path,
)


# ─────────────────────────────────────────────────────────────────────────────
# sanitize_output
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeOutput:
    def test_redacts_gemini_api_key_value(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "sk-secret-12345")
        text = "API call failed: bad key sk-secret-12345"
        result = sanitize_output(text)
        assert "sk-secret-12345" not in result
        assert "***TOKEN***" in result

    def test_redacts_website_dispatch_pat_value(self, monkeypatch):
        monkeypatch.setenv("WEBSITE_DISPATCH_PAT", "ghp_realtokenvalue9999")
        text = "curl auth failed: header had ghp_realtokenvalue9999"
        result = sanitize_output(text)
        assert "ghp_realtokenvalue9999" not in result

    def test_redacts_multiple_secrets_in_same_text(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AAA")
        monkeypatch.setenv("GH_TOKEN", "BBB")
        result = sanitize_output("see AAA and BBB and AAA again")
        assert "AAA" not in result
        assert "BBB" not in result
        assert result.count("***TOKEN***") == 3

    def test_returns_unchanged_when_no_secrets_set(self, monkeypatch):
        # Clear all known sensitive env vars
        for var in ("GEMINI_API_KEY", "MODEL_API_KEY", "WEBSITE_DISPATCH_PAT",
                    "GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        text = "ordinary log line with no secrets"
        assert sanitize_output(text) == text

    def test_handles_none_and_empty(self):
        assert sanitize_output(None) is None
        assert sanitize_output("") == ""

    def test_accepts_extra_tokens_param(self, monkeypatch):
        # Caller can pass additional tokens to redact (e.g., a one-off PAT)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        result = sanitize_output("foo bar baz", sensitive_tokens=["bar"])
        assert "bar" not in result
        assert "foo" in result and "baz" in result

    def test_does_not_redact_empty_token_value(self, monkeypatch):
        # Defense against env var being literally empty string — would
        # replace every empty-string boundary in the text otherwise.
        monkeypatch.setenv("GEMINI_API_KEY", "")
        result = sanitize_output("hello world")
        assert result == "hello world"


# ─────────────────────────────────────────────────────────────────────────────
# run_command_safe
# ─────────────────────────────────────────────────────────────────────────────

class TestRunCommandSafe:
    def test_successful_command(self):
        result = run_command_safe(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_returns_completed_process_with_capture(self):
        result = run_command_safe(["echo", "captured"])
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")

    def test_check_false_returns_nonzero_without_raise(self):
        # `false` exits 1; check=False means we don't raise
        result = run_command_safe(["false"])
        assert result.returncode != 0  # didn't raise

    def test_check_true_raises_on_nonzero(self):
        with pytest.raises(subprocess.CalledProcessError):
            run_command_safe(["false"], check=True)

    def test_sanitizes_stderr_in_raised_exception(self, monkeypatch, tmp_path):
        # If a subprocess error message contains a secret, it must be
        # sanitized before reaching any log surface.
        monkeypatch.setenv("GEMINI_API_KEY", "supersecret123")
        # Force stderr to contain the secret via a shell command
        try:
            run_command_safe(
                ["sh", "-c", "echo supersecret123 1>&2; exit 1"],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            assert "supersecret123" not in (e.stderr or "")
            assert "***TOKEN***" in (e.stderr or "")
        else:
            pytest.fail("expected CalledProcessError")


# ─────────────────────────────────────────────────────────────────────────────
# validate_path
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePath:
    def test_path_inside_base_is_valid(self, tmp_path: Path):
        (tmp_path / "subdir").mkdir()
        assert validate_path("subdir", base_dir=tmp_path) is True

    def test_directory_traversal_rejected(self, tmp_path: Path):
        # `../etc/passwd` from inside tmp_path resolves outside it
        assert validate_path("../etc/passwd", base_dir=tmp_path) is False

    def test_absolute_path_to_outside_dir_rejected(self, tmp_path: Path):
        # /etc is presumably not under tmp_path
        assert validate_path("/etc/passwd", base_dir=tmp_path) is False

    def test_nested_safe_path_is_valid(self, tmp_path: Path):
        (tmp_path / "a/b/c").mkdir(parents=True)
        assert validate_path("a/b/c", base_dir=tmp_path) is True

    def test_default_base_is_cwd(self):
        # If no base_dir, use cwd. A relative path is valid by definition.
        assert validate_path("foo") is True
