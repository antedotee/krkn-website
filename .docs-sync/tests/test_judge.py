"""Tests for agent/judge.py — verdict parsing, fallback behavior, prompt shape."""
import json
from unittest.mock import patch

import pytest

from agent.judge import (
    JUDGE_VERDICT_CLEAN,
    JUDGE_VERDICT_FLAGGED,
    JUDGE_VERDICT_UNAVAILABLE,
    JudgeVerdict,
    _build_judge_messages,
    _parse_verdict,
    judge,
)
from agent.llm_client import LLMResponse
from extractors.krkn_hub import Parameter, Scenario


@pytest.fixture
def scenario():
    return Scenario(
        name="foo-scenarios",
        scenario_type="foo_scenarios",
        parameters=[
            Parameter(name="ns", variable="NS", type="string",
                      default="", required=False, description="ns"),
        ],
    )


@pytest.fixture
def taxonomy():
    return {"scenario_types": ["foo_scenarios"], "cli_flags": ["--ns"]}


def _mock_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content, model="phi-4-mini-instruct", finish_reason="stop",
        prompt_tokens=50, completion_tokens=20,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _build_judge_messages
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildJudgeMessages:
    def test_includes_scenario_schema_and_draft(self, scenario, taxonomy):
        msgs = _build_judge_messages(scenario, "Draft body here.", taxonomy)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        # Scenario schema embedded
        assert "foo-scenarios" in msgs[1]["content"]
        assert "NS" in msgs[1]["content"]
        # Taxonomy embedded
        assert "foo_scenarios" in msgs[1]["content"]
        # Draft embedded
        assert "Draft body here." in msgs[1]["content"]

    def test_system_prompt_demands_json(self, scenario, taxonomy):
        msgs = _build_judge_messages(scenario, "x", taxonomy)
        assert "JSON" in msgs[0]["content"]
        assert "verdict" in msgs[0]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# _parse_verdict
# ─────────────────────────────────────────────────────────────────────────────

class TestParseVerdict:
    def test_parses_clean_verdict(self):
        text = json.dumps({"verdict": "clean", "reasoning": "ok", "flagged_phrases": []})
        result = _parse_verdict(text)
        assert result is not None
        assert result.verdict == JUDGE_VERDICT_CLEAN
        assert result.reasoning == "ok"

    def test_parses_flagged_with_phrases(self):
        text = json.dumps({
            "verdict": "flagged",
            "reasoning": "found a hallucination",
            "flagged_phrases": ["fake_invented_scenarios", "--unreal-flag"],
        })
        result = _parse_verdict(text)
        assert result.verdict == JUDGE_VERDICT_FLAGGED
        assert "fake_invented_scenarios" in result.flagged_phrases

    def test_strips_code_fence_wrapping(self):
        text = "```json\n" + json.dumps({
            "verdict": "clean", "reasoning": "ok", "flagged_phrases": []
        }) + "\n```"
        result = _parse_verdict(text)
        assert result is not None
        assert result.verdict == JUDGE_VERDICT_CLEAN

    def test_extracts_json_from_surrounding_prose(self):
        # Some models return prose + JSON despite "json only" instruction
        text = (
            "Here's my verdict:\n"
            + json.dumps({"verdict": "clean", "reasoning": "ok", "flagged_phrases": []})
            + "\nLet me know if you need more."
        )
        result = _parse_verdict(text)
        assert result is not None
        assert result.verdict == JUDGE_VERDICT_CLEAN

    def test_returns_none_on_invalid_json(self):
        assert _parse_verdict("not json at all") is None
        assert _parse_verdict("{this is broken json") is None

    def test_returns_none_on_unknown_verdict_value(self):
        text = json.dumps({"verdict": "MAYBE", "reasoning": "?", "flagged_phrases": []})
        assert _parse_verdict(text) is None

    def test_returns_none_on_empty_content(self):
        assert _parse_verdict("") is None
        assert _parse_verdict(None) is None  # type: ignore

    def test_handles_missing_optional_fields(self):
        # `reasoning` and `flagged_phrases` should be optional
        text = json.dumps({"verdict": "clean"})
        result = _parse_verdict(text)
        assert result is not None
        assert result.verdict == JUDGE_VERDICT_CLEAN
        assert result.reasoning == ""
        assert result.flagged_phrases == []


# ─────────────────────────────────────────────────────────────────────────────
# judge — full flow with mocked LLM
# ─────────────────────────────────────────────────────────────────────────────

class TestJudge:
    def test_clean_verdict_passthrough(self, scenario, taxonomy):
        verdict_json = json.dumps({
            "verdict": "clean", "reasoning": "all good", "flagged_phrases": [],
        })
        with patch("agent.judge.chat_completion",
                   return_value=_mock_response(verdict_json)):
            result = judge(scenario, "draft body", taxonomy)
        assert result.verdict == JUDGE_VERDICT_CLEAN
        assert result.reasoning == "all good"

    def test_flagged_verdict_includes_phrases(self, scenario, taxonomy):
        verdict_json = json.dumps({
            "verdict": "flagged",
            "reasoning": "invented scenario type",
            "flagged_phrases": ["fake_scenarios"],
        })
        with patch("agent.judge.chat_completion",
                   return_value=_mock_response(verdict_json)):
            result = judge(scenario, "draft body", taxonomy)
        assert result.verdict == JUDGE_VERDICT_FLAGGED
        assert "fake_scenarios" in result.flagged_phrases

    def test_unparseable_response_returns_unavailable(self, scenario, taxonomy):
        with patch("agent.judge.chat_completion",
                   return_value=_mock_response("yeah, looks fine to me")):
            result = judge(scenario, "draft body", taxonomy)
        assert result.verdict == JUDGE_VERDICT_UNAVAILABLE

    def test_api_failure_returns_unavailable(self, scenario, taxonomy):
        with patch("agent.judge.chat_completion",
                   side_effect=RuntimeError("rate limited")):
            result = judge(scenario, "draft body", taxonomy)
        assert result.verdict == JUDGE_VERDICT_UNAVAILABLE
        # Reasoning surfaces the error type so the orchestrator can log it
        assert "RuntimeError" in result.reasoning

    def test_uses_phi_model_by_default(self, scenario, taxonomy, monkeypatch):
        monkeypatch.delenv("JUDGE_MODEL", raising=False)
        verdict_json = json.dumps({
            "verdict": "clean", "reasoning": "ok", "flagged_phrases": [],
        })
        captured = {}

        def fake_chat_completion(messages, *, model=None, **kwargs):
            captured["model"] = model
            return _mock_response(verdict_json)

        with patch("agent.judge.chat_completion", side_effect=fake_chat_completion):
            judge(scenario, "draft body", taxonomy)
        assert "phi" in captured["model"].lower()

    def test_model_can_be_overridden_via_env(self, scenario, taxonomy, monkeypatch):
        monkeypatch.setenv("JUDGE_MODEL", "openai/gpt-4o-mini")
        verdict_json = json.dumps({
            "verdict": "clean", "reasoning": "ok", "flagged_phrases": [],
        })
        captured = {}

        def fake_chat_completion(messages, *, model=None, **kwargs):
            captured["model"] = model
            return _mock_response(verdict_json)

        with patch("agent.judge.chat_completion", side_effect=fake_chat_completion):
            judge(scenario, "draft body", taxonomy)
        assert captured["model"] == "openai/gpt-4o-mini"
