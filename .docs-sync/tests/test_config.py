"""Tests for .docs-sync/config.py — env-driven LLM client factory.

The key behavior is: existing user secrets (GEMINI_API_KEY,
WEBSITE_DISPATCH_PAT) work as-is, with optional MODEL_API_KEY override
for swapping providers without touching code.
"""
import pytest

from config import (
    get_api_base,
    get_model_name,
    get_max_context_chars,
    _read_api_key,
    truncate_content,
    truncate_diff,
)


class TestApiKeyResolution:
    def test_uses_gemini_api_key_when_only_one_set(self, monkeypatch):
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-123")
        assert _read_api_key() == "gemini-key-123"

    def test_model_api_key_overrides_gemini_api_key(self, monkeypatch):
        # User can switch providers per-run by setting MODEL_API_KEY in
        # the workflow env block, without unsetting GEMINI_API_KEY.
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("MODEL_API_KEY", "openai-key")
        assert _read_api_key() == "openai-key"

    def test_returns_empty_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert _read_api_key() == ""


class TestApiBase:
    def test_default_is_gemini_compat_endpoint(self, monkeypatch):
        monkeypatch.delenv("MODEL_API_BASE", raising=False)
        assert "generativelanguage.googleapis.com" in get_api_base()

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MODEL_API_BASE", "https://api.openai.com/v1")
        assert get_api_base() == "https://api.openai.com/v1"


class TestModelName:
    def test_default_is_gemini_flash(self, monkeypatch):
        monkeypatch.delenv("MODEL_NAME", raising=False)
        assert get_model_name() == "gemini-2.5-flash"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MODEL_NAME", "gpt-4o-mini")
        assert get_model_name() == "gpt-4o-mini"


class TestMaxContextChars:
    def test_default_is_200k(self, monkeypatch):
        monkeypatch.delenv("MAX_CONTEXT_CHARS", raising=False)
        assert get_max_context_chars() == 200_000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "50000")
        assert get_max_context_chars() == 50_000

    def test_invalid_value_falls_back_to_default(self, monkeypatch, capsys):
        # Don't silently disable budget if user typoed the env var.
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "fifty thousand")
        result = get_max_context_chars()
        assert result == 200_000
        captured = capsys.readouterr()
        assert "warning" in captured.out.lower()


class TestTruncateContent:
    def test_short_content_returned_unchanged(self):
        text = "short"
        assert truncate_content(text, max_chars=1000) == text

    def test_long_content_truncated_with_marker(self):
        text = "x" * 5000
        result = truncate_content(text, max_chars=100)
        assert len(result) <= 100
        assert "truncated" in result.lower()

    def test_uses_default_when_no_explicit_cap(self, monkeypatch):
        monkeypatch.delenv("MAX_CONTEXT_CHARS", raising=False)
        # 250K text with default 200K cap → should be truncated
        result = truncate_content("y" * 250_000)
        assert len(result) < 250_000


class TestTruncateDiff:
    def test_short_diff_returned_unchanged(self):
        diff = "short diff"
        assert truncate_diff(diff, max_chars=1000) == diff

    def test_long_diff_truncated_with_marker(self):
        diff = "+" * 5000
        result = truncate_diff(diff, max_chars=100)
        assert len(result) <= 100
        assert "truncated" in result.lower()
        assert "diff" in result.lower()
