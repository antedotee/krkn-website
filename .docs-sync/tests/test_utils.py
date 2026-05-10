"""Tests for .docs-sync/utils.py retry/backoff helpers.

Lifted from code-to-docs but with our own thin test layer to ensure the
behavior we depend on is locked.
"""
import pytest

from utils import retry_with_backoff, calc_backoff_delay


class TestCalcBackoffDelay:
    def test_linear_growth(self):
        assert calc_backoff_delay(0) == 3   # (0+1)*3
        assert calc_backoff_delay(1) == 6   # (1+1)*3
        assert calc_backoff_delay(2) == 9   # (2+1)*3

    def test_custom_multiplier(self):
        assert calc_backoff_delay(2, multiplier=10) == 30


class TestRetryWithBackoff:
    def test_succeeds_on_first_try_no_retry(self):
        calls = {"n": 0}

        @retry_with_backoff(max_retries=3, delay_multiplier=0)
        def succeed():
            calls["n"] += 1
            return "ok"

        assert succeed() == "ok"
        assert calls["n"] == 1

    def test_retries_until_success(self):
        calls = {"n": 0}

        @retry_with_backoff(max_retries=3, delay_multiplier=0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("not yet")
            return "third time's a charm"

        assert flaky() == "third time's a charm"
        assert calls["n"] == 3

    def test_returns_default_when_all_attempts_fail(self):
        @retry_with_backoff(max_retries=2, delay_multiplier=0, default="fallback")
        def always_fails():
            raise RuntimeError("nope")

        assert always_fails() == "fallback"

    def test_reraise_propagates_last_exception(self):
        @retry_with_backoff(max_retries=2, delay_multiplier=0, reraise=True)
        def always_fails():
            raise ValueError("the actual error")

        with pytest.raises(ValueError, match="the actual error"):
            always_fails()

    def test_on_retry_callback_invoked_per_attempt(self):
        attempts = []

        def track(attempt, max_retries, exc, wait):
            attempts.append((attempt, str(exc)))

        @retry_with_backoff(max_retries=3, delay_multiplier=0, on_retry=track,
                            default=None)
        def fail():
            raise RuntimeError("boom")

        fail()
        assert len(attempts) == 3
