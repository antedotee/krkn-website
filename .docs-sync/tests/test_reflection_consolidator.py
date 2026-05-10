"""Tests for reflection/consolidator.py — the single LLM call that turns
a list of REFLECTION.md objects into ranked, deduped proposals for
AGENTS.md and repo-map.yaml additions.

Mock the LLM. The consolidator's job is plumbing + validation, NOT the
specific judgment of the model.
"""
import json
from unittest.mock import patch

import pytest

from agent.llm_client import LLMResponse
from reflection.consolidator import (
    ConsolidatorOutput,
    ProposedAddition,
    _build_consolidator_messages,
    _parse_consolidator_response,
    consolidate,
)
from reflection.writer import (
    OUTCOME_PASS,
    OUTCOME_REJECTED,
    Reflection,
    Suggestion,
    SUGGESTION_AGENTS_RULE,
    SUGGESTION_SKIP_PATTERN,
    new_reflection,
)


def _mock_response(content: str, model: str = "gemini-2.5-flash") -> LLMResponse:
    return LLMResponse(
        content=content, model=model, finish_reason="stop",
        prompt_tokens=200, completion_tokens=300,
    )


def _reflection_with_suggestions(
    pr_number: int,
    outcome: str = OUTCOME_PASS,
    suggestions: list[Suggestion] | None = None,
) -> Reflection:
    r = new_reflection("o/r", pr_number, f"sha{pr_number}", outcome)
    r.suggestions = suggestions or []
    return r


# ─────────────────────────────────────────────────────────────────────────────
# _build_consolidator_messages
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildMessages:
    def test_system_prompt_demands_json(self):
        msgs = _build_consolidator_messages([
            _reflection_with_suggestions(1),
        ])
        assert msgs[0]["role"] == "system"
        assert "JSON" in msgs[0]["content"]
        # The system prompt must constrain shape (1-3 agents, 1-2 skip)
        assert "agents_rule_additions" in msgs[0]["content"]
        assert "skip_pattern_additions" in msgs[0]["content"]

    def test_user_message_embeds_reflection_snippets(self):
        r = _reflection_with_suggestions(
            42, suggestions=[
                Suggestion(SUGGESTION_AGENTS_RULE, "always check X", "o/r#42"),
            ],
        )
        msgs = _build_consolidator_messages([r])
        # Reflections must be visible to the LLM
        assert "o/r#42" in msgs[1]["content"] or "42" in msgs[1]["content"]
        assert "always check X" in msgs[1]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# _parse_consolidator_response
# ─────────────────────────────────────────────────────────────────────────────

class TestParseConsolidatorResponse:
    def test_parses_well_formed_output(self):
        content = json.dumps({
            "agents_rule_additions": [
                {"text": "Always check for pipeless tables",
                 "rationale": "missed in pod-scenarios PR",
                 "source_prs": ["o/r#42"]},
            ],
            "skip_pattern_additions": [
                {"text": "docs/internal/*",
                 "rationale": "irrelevant internal docs",
                 "source_prs": ["o/r#43"]},
            ],
        })
        result = _parse_consolidator_response(content)
        assert result is not None
        assert len(result.agents_rule_additions) == 1
        assert result.agents_rule_additions[0].text == "Always check for pipeless tables"
        assert len(result.skip_pattern_additions) == 1

    def test_strips_code_fence_wrapping(self):
        content = "```json\n" + json.dumps({
            "agents_rule_additions": [], "skip_pattern_additions": [],
        }) + "\n```"
        result = _parse_consolidator_response(content)
        assert result is not None
        assert result.agents_rule_additions == []

    def test_extracts_json_from_prose(self):
        content = (
            "Here's my analysis:\n"
            + json.dumps({
                "agents_rule_additions": [],
                "skip_pattern_additions": [],
            })
            + "\nLet me know if you want more detail."
        )
        result = _parse_consolidator_response(content)
        assert result is not None

    def test_returns_none_on_invalid_json(self):
        assert _parse_consolidator_response("not json") is None
        assert _parse_consolidator_response("{broken") is None
        assert _parse_consolidator_response("") is None

    def test_drops_entries_with_missing_required_fields(self):
        # The LLM might omit `source_prs` or `text` — drop those quietly
        # rather than failing the whole consolidator.
        content = json.dumps({
            "agents_rule_additions": [
                {"text": "good rule", "rationale": "ok", "source_prs": ["o/r#1"]},
                {"rationale": "no text"},  # bad — drop
                {"text": "no citations", "rationale": "ok"},  # bad — drop
            ],
            "skip_pattern_additions": [],
        })
        result = _parse_consolidator_response(content)
        assert result is not None
        assert len(result.agents_rule_additions) == 1
        assert result.agents_rule_additions[0].text == "good rule"

    def test_caps_output_at_plan_limits(self):
        """Plan locks 1-3 agents rules, 1-2 skip patterns. Even if the LLM
        ignores the limit, the parser enforces it (truncate after sorting
        by source_prs count — most-cited wins)."""
        content = json.dumps({
            "agents_rule_additions": [
                {"text": f"rule {i}", "rationale": "ok", "source_prs": ["o/r#1"]}
                for i in range(10)
            ],
            "skip_pattern_additions": [
                {"text": f"skip {i}", "rationale": "ok", "source_prs": ["o/r#1"]}
                for i in range(10)
            ],
        })
        result = _parse_consolidator_response(content)
        assert len(result.agents_rule_additions) <= 3
        assert len(result.skip_pattern_additions) <= 2


# ─────────────────────────────────────────────────────────────────────────────
# consolidate — full flow with mocked LLM
# ─────────────────────────────────────────────────────────────────────────────

class TestConsolidate:
    def test_empty_input_returns_empty_without_llm_call(self):
        """No reflections → don't burn tokens, just return empty output."""
        with patch("reflection.consolidator.chat_completion") as mock_chat:
            result = consolidate([])
        assert result is not None
        assert result.agents_rule_additions == []
        assert result.skip_pattern_additions == []
        mock_chat.assert_not_called()

    def test_no_suggestions_across_all_reflections_skips_llm(self):
        """If every reflection has zero suggestions, the LLM has nothing to
        chew on — skip the call."""
        reflections = [
            _reflection_with_suggestions(1),
            _reflection_with_suggestions(2),
        ]
        with patch("reflection.consolidator.chat_completion") as mock_chat:
            result = consolidate(reflections)
        assert result.agents_rule_additions == []
        assert result.skip_pattern_additions == []
        mock_chat.assert_not_called()

    def test_passes_suggestions_to_llm(self):
        reflections = [
            _reflection_with_suggestions(42, suggestions=[
                Suggestion(SUGGESTION_AGENTS_RULE, "check pipeless tables", "o/r#42"),
            ]),
        ]
        llm_content = json.dumps({
            "agents_rule_additions": [
                {"text": "check pipeless tables", "rationale": "from PR 42",
                 "source_prs": ["o/r#42"]},
            ],
            "skip_pattern_additions": [],
        })
        with patch("reflection.consolidator.chat_completion",
                   return_value=_mock_response(llm_content)) as mock_chat:
            result = consolidate(reflections)

        assert mock_chat.call_count == 1
        assert len(result.agents_rule_additions) == 1
        assert result.agents_rule_additions[0].text == "check pipeless tables"

    def test_llm_unparseable_returns_empty(self):
        reflections = [
            _reflection_with_suggestions(42, suggestions=[
                Suggestion(SUGGESTION_AGENTS_RULE, "x", "o/r#42"),
            ]),
        ]
        with patch("reflection.consolidator.chat_completion",
                   return_value=_mock_response("gibberish")):
            result = consolidate(reflections)
        assert result.agents_rule_additions == []

    def test_llm_failure_returns_empty(self):
        """API errors must not crash the cron — return empty so the workflow
        skips opening a PR and tries again tomorrow."""
        reflections = [
            _reflection_with_suggestions(42, suggestions=[
                Suggestion(SUGGESTION_AGENTS_RULE, "x", "o/r#42"),
            ]),
        ]
        with patch("reflection.consolidator.chat_completion",
                   side_effect=RuntimeError("rate limited")):
            result = consolidate(reflections)
        assert result.agents_rule_additions == []
        assert result.skip_pattern_additions == []

    def test_rejected_reflections_get_higher_signal_weight(self):
        """REJECTED runs are the most valuable data — make sure they're
        explicitly highlighted in the prompt so the LLM weights them right."""
        rejected = _reflection_with_suggestions(99, outcome=OUTCOME_REJECTED)
        rejected.surprises = ["LLM kept inventing fake scenario types"]
        msgs = _build_consolidator_messages([rejected])
        prompt = msgs[1]["content"]
        assert "rejected" in prompt.lower() or "REJECTED" in prompt
        assert "fake scenario types" in prompt
