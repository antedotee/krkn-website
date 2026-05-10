"""Tests for agent/llm_client.py — budget enforcement, retries, response shape."""
from unittest.mock import MagicMock, patch

import pytest

from agent.llm_client import (
    LLMResponse,
    TokenBudgetExceededError,
    chat_completion,
    _approx_token_count,
    _validate_budget,
)


class TestApproxTokenCount:
    def test_empty(self):
        assert _approx_token_count("") == 0

    def test_simple(self):
        # 100 chars / 4 = 25 tokens
        assert _approx_token_count("x" * 100) == 25


class TestValidateBudget:
    def test_under_budget_returns_count(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "10000")
        messages = [{"role": "user", "content": "hello world"}]
        # ~3 tokens, well under 2500-token cap
        assert _validate_budget(messages) >= 0

    def test_over_budget_raises(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "100")  # 25-token cap
        messages = [{"role": "user", "content": "x" * 1000}]  # ~250 tokens
        with pytest.raises(TokenBudgetExceededError, match="exceeds budget"):
            _validate_budget(messages)


class TestChatCompletion:
    def _mock_client(self, content: str, finish_reason: str = "stop",
                     prompt_tokens: int = 100, completion_tokens: int = 50):
        """Build a mock OpenAI SDK response chain."""
        choice = MagicMock()
        choice.message.content = content
        choice.finish_reason = finish_reason

        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens

        completion = MagicMock()
        completion.choices = [choice]
        completion.usage = usage
        completion.model = "gemini-2.5-flash"

        client = MagicMock()
        client.chat.completions.create.return_value = completion
        return client

    def test_returns_llm_response_dataclass(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "200000")
        with patch("agent.llm_client.get_client",
                   return_value=self._mock_client("hello")):
            response = chat_completion([{"role": "user", "content": "hi"}])
        assert isinstance(response, LLMResponse)
        assert response.content == "hello"
        assert response.finish_reason == "stop"
        assert response.prompt_tokens == 100
        assert response.completion_tokens == 50

    def test_passes_temperature_and_max_tokens_to_client(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "200000")
        client = self._mock_client("ok")
        with patch("agent.llm_client.get_client", return_value=client):
            chat_completion(
                [{"role": "user", "content": "x"}],
                temperature=0.5,
                max_output_tokens=1024,
            )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.5
        assert kwargs["max_tokens"] == 1024

    def test_max_output_tokens_capped_at_hard_ceiling(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "200000")
        client = self._mock_client("ok")
        with patch("agent.llm_client.get_client", return_value=client):
            chat_completion(
                [{"role": "user", "content": "x"}],
                max_output_tokens=99999,  # absurdly high
            )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_tokens"] <= 8192

    def test_handles_missing_usage_block(self, monkeypatch):
        # Some providers return null usage; don't crash.
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "200000")
        client = self._mock_client("ok")
        client.chat.completions.create.return_value.usage = None
        with patch("agent.llm_client.get_client", return_value=client):
            response = chat_completion([{"role": "user", "content": "x"}])
        assert response.prompt_tokens == 0
        assert response.completion_tokens == 0

    def test_budget_exceeded_does_not_call_client(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "100")  # tiny
        client = self._mock_client("should never be called")
        with patch("agent.llm_client.get_client", return_value=client):
            with pytest.raises(TokenBudgetExceededError):
                chat_completion([{"role": "user", "content": "x" * 500}])
        client.chat.completions.create.assert_not_called()

    def test_retries_on_transient_error_then_succeeds(self, monkeypatch):
        # First two calls raise; third returns ok. retry_with_backoff
        # (delay_multiplier=2) should retry up to 3 times.
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "200000")
        client = MagicMock()
        success_choice = MagicMock()
        success_choice.message.content = "recovered"
        success_choice.finish_reason = "stop"
        success_completion = MagicMock(
            choices=[success_choice], usage=MagicMock(prompt_tokens=0, completion_tokens=0),
            model="gemini-2.5-flash",
        )
        client.chat.completions.create.side_effect = [
            RuntimeError("transient error 1"),
            RuntimeError("transient error 2"),
            success_completion,
        ]
        # Patch sleep so the test runs fast
        with patch("agent.llm_client.get_client", return_value=client), \
             patch("time.sleep"):
            response = chat_completion([{"role": "user", "content": "x"}])
        assert response.content == "recovered"
        assert client.chat.completions.create.call_count == 3
