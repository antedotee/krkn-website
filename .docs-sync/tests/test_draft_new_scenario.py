"""Tests for agent/draft_new_scenario.py.

Heavy emphasis on validation gates per the user's "must be excellent,
never accept whatever LLM produces" directive. Every rejection path is
tested with a deterministic-mock LLM response so we never silently let
bad output through.
"""
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

from agent.draft_new_scenario import (
    RejectionReason,
    build_prompt,
    draft,
    validate,
)
from agent.llm_client import LLMResponse
from extractors.krkn_hub import Parameter, Scenario


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_scenario():
    return Scenario(
        name="foo-scenarios",
        scenario_type="foo_scenarios",
        parameters=[
            Parameter(name="namespace", variable="NAMESPACE", type="string",
                      default="default", required=False, description="Target ns"),
            Parameter(name="kill-count", variable="KILL_COUNT", type="number",
                      default="1", required=False, description="How many to kill"),
        ],
    )


@pytest.fixture
def taxonomy():
    return {
        "scenario_directories": ["foo-scenario", "bar-scenario"],
        "scenario_types": [
            "foo_scenarios", "bar_scenarios", "pod_disruption_scenarios",
        ],
        "cli_flags": ["--namespace", "--kill-count", "--config"],
    }


@pytest.fixture
def voice_samples():
    return [
        dedent("""\
            This scenario disrupts pods matching a label selector.

            ## Why this matters

            Modern apps demand resilience under chaos.

            ## Use cases

            - Validate replica recovery
            - Test pod readiness probes

            ## Configuration

            See the per-tool tabs below.
            """),
    ]


def _good_body() -> str:
    """A body that passes all validation gates (~250 words, comfortably in range)."""
    body = dedent("""\
        This scenario disrupts a configurable count of pods matching label or
        namespace patterns. The disruption is observed by Krkn telemetry and
        compared against a recovery deadline so each run produces a clear
        pass or fail signal for downstream automation and alerting.

        ## Why this matters

        Kubernetes promises automatic pod rescheduling, but in practice a
        cluster's recovery time depends on resource pressure, scheduler
        configuration, and workload-specific readiness probes. This scenario
        forces those interactions and surfaces subtle latency that a steady
        state would never reveal. Teams running this scenario regularly catch
        regressions in their replica policies before users notice them, and
        it gives platform engineers a repeatable way to compare resilience
        across cluster configurations or release candidates.

        ## Use cases

        - Validate that a Deployment with three replicas tolerates a single
          unplanned pod kill within an acceptable recovery window.
        - Stress-test PodDisruptionBudgets across a namespace by killing
          multiple pods simultaneously and confirming evictions stay below
          the configured budget.
        - Reproduce flake conditions reported by ops teams, with deterministic
          parameters captured in the scenario config so the failure becomes
          shareable across the team.

        ## Configuration

        Pick the tab that matches your runner — krkn for native python,
        krkn-hub for the containerized form, krknctl for the CLI wrapper.
        Each tab below documents the same parameter schema with the
        appropriate variable names and flags for that runner. The mechanical
        regen step keeps these tabs in sync as upstream config evolves, so
        you can trust that a flag shown here is one currently supported.
        """)
    return body


# ─────────────────────────────────────────────────────────────────────────────
# build_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_includes_skill_taxonomy_samples_scenario(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        messages = build_prompt(fresh_scenario, taxonomy, voice_samples)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        user_text = messages[1]["content"]
        # Skill spec embedded
        assert "Skill specification" in user_text
        # Taxonomy embedded as JSON
        assert "foo_scenarios" in user_text
        # Scenario data embedded
        assert "foo-scenarios" in user_text
        assert "NAMESPACE" in user_text
        # Voice sample embedded
        assert "This scenario disrupts" in user_text


# ─────────────────────────────────────────────────────────────────────────────
# validate — every rejection path
# ─────────────────────────────────────────────────────────────────────────────

class TestValidate:
    def test_accepts_good_body(self, taxonomy):
        rejections = validate(_good_body(), taxonomy)
        assert rejections == []

    def test_rejects_empty(self, taxonomy):
        result = validate("", taxonomy)
        assert any(r.code == "empty" for r in result)

    def test_rejects_too_short(self, taxonomy):
        body = "## Why this matters\n\nShort.\n\n## Use cases\n\n## Configuration\n"
        result = validate(body, taxonomy)
        assert any(r.code == "too_short" for r in result)

    def test_rejects_too_long(self, taxonomy):
        body = "## Why this matters\n\n" + "padding " * 1000
        result = validate(body, taxonomy)
        assert any(r.code == "too_long" for r in result)

    def test_rejects_h1(self, taxonomy):
        body = "# Wrong Title\n\n" + _good_body()
        result = validate(body, taxonomy)
        assert any(r.code == "forbidden_pattern" and "H1" in r.message
                   for r in result)

    def test_rejects_h4_or_deeper(self, taxonomy):
        body = _good_body() + "\n#### Too deep\n\n"
        result = validate(body, taxonomy)
        assert any(r.code == "forbidden_pattern" and "deeper than H3" in r.message
                   for r in result)

    def test_rejects_hugo_shortcode(self, taxonomy):
        body = _good_body() + "\n{{< include file=\"x.md\" >}}\n"
        result = validate(body, taxonomy)
        assert any(r.code == "forbidden_pattern" and "shortcode" in r.message.lower()
                   for r in result)

    def test_rejects_krkn_html_tag(self, taxonomy):
        body = _good_body() + "\n<krkn-namespace />\n"
        result = validate(body, taxonomy)
        assert any(r.code == "forbidden_pattern" and "krkn-" in r.message
                   for r in result)

    def test_rejects_missing_required_heading(self, taxonomy):
        body = dedent("""\
            One paragraph.

            ## Use cases

            - thing

            ## Configuration

            See tabs.
            """) + "\nfiller " * 50
        result = validate(body, taxonomy)
        assert any(r.code == "missing_heading" and "Why this matters" in r.message
                   for r in result)

    def test_rejects_invented_scenario_type(self, taxonomy):
        body = _good_body().replace(
            "krkn for native python",
            "krkn for native python — uses the brand_new_invented_scenarios type",
        )
        result = validate(body, taxonomy)
        assert any(r.code == "invented_scenario_type" for r in result)

    def test_accepts_known_scenario_type_mention(self, taxonomy):
        body = _good_body() + "\n\nSee also: pod_disruption_scenarios.\n"
        result = validate(body, taxonomy)
        # pod_disruption_scenarios IS in taxonomy → no hallucination flag
        assert not any(r.code == "invented_scenario_type" for r in result)

    def test_rejects_no_prose(self, taxonomy):
        body = "```\nfoo\nbar\n```\n```\nbaz\n```\n"
        result = validate(body, taxonomy)
        assert any(r.code == "no_prose" for r in result)


# ─────────────────────────────────────────────────────────────────────────────
# draft — full integration with mocked LLM
# ─────────────────────────────────────────────────────────────────────────────

class TestDraft:
    def _mock_response(self, content: str) -> LLMResponse:
        return LLMResponse(
            content=content, model="gemini-2.5-flash", finish_reason="stop",
            prompt_tokens=100, completion_tokens=200,
        )

    def test_returns_accepted_draft_on_good_output(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        with patch("agent.draft_new_scenario.chat_completion",
                   return_value=self._mock_response(_good_body())):
            result = draft(fresh_scenario, taxonomy, voice_samples)
        assert result.accepted is True
        assert result.rejections == []
        assert "Why this matters" in result.body

    def test_returns_rejected_on_invented_scenario_type(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        bad = _good_body() + "\n\nUses fake_invented_scenarios.\n"
        with patch("agent.draft_new_scenario.chat_completion",
                   return_value=self._mock_response(bad)):
            result = draft(fresh_scenario, taxonomy, voice_samples,
                           max_attempts=1)
        assert result.accepted is False
        assert any(r.code == "invented_scenario_type" for r in result.rejections)

    def test_strips_wrapping_code_fence(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        # Common LLM behavior: wrap entire output in ```markdown ... ```
        wrapped = "```markdown\n" + _good_body() + "\n```"
        with patch("agent.draft_new_scenario.chat_completion",
                   return_value=self._mock_response(wrapped)):
            result = draft(fresh_scenario, taxonomy, voice_samples)
        assert result.accepted is True
        # Wrapper stripped
        assert not result.body.startswith("```")

    def test_retries_on_first_rejection_succeeds_on_second(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        attempts = [
            self._mock_response("# Wrong title\n\n" + _good_body()),  # rejected
            self._mock_response(_good_body()),                          # accepted
        ]
        with patch("agent.draft_new_scenario.chat_completion",
                   side_effect=attempts):
            result = draft(fresh_scenario, taxonomy, voice_samples,
                           max_attempts=2)
        assert result.accepted is True

    def test_returns_last_failure_when_all_attempts_rejected(
        self, fresh_scenario, taxonomy, voice_samples,
    ):
        bad_body = "# Bad\n\n" + _good_body()
        with patch("agent.draft_new_scenario.chat_completion",
                   return_value=self._mock_response(bad_body)):
            result = draft(fresh_scenario, taxonomy, voice_samples,
                           max_attempts=2)
        assert result.accepted is False
        assert result.response is not None
