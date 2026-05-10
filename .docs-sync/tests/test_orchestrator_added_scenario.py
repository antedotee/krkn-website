"""End-to-end test for the Slice 2 path: an upstream PR adds a new scenario,
the orchestrator routes it through prose drafting + judge + file write.

LLM is fully mocked — we test the wiring + validation, not Gemini's prose."""
import json
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from agent.draft_new_scenario import DraftResult
from agent.judge import JudgeVerdict, JUDGE_VERDICT_CLEAN, JUDGE_VERDICT_FLAGGED
from agent.llm_client import LLMResponse
from extractors.krkn_hub import ChangeSet, Parameter, Scenario


def _good_body() -> str:
    return dedent("""\
        This new scenario disrupts an experimental cluster surface to test
        how applications respond when the relevant subsystem becomes
        intermittent or unavailable. The scenario is parameterized to allow
        tuning of duration and target selection from one config file.

        ## Why this matters

        Modern Kubernetes platforms layer many subsystems on top of the
        scheduler, and resilience needs to be tested at every layer.
        This scenario adds coverage at the layer where existing tests
        previously had a gap, giving operators a way to certify recovery
        behavior in environments that would otherwise be opaque.

        ## Use cases

        - Validate that downstream consumers degrade gracefully when this
          subsystem is unavailable for a configurable window.
        - Surface flake conditions that only appear under coordinated
          intermittent failure across multiple nodes simultaneously.
        - Add to a regression suite that runs nightly so the team learns
          about regressions within a 24-hour window.

        ## Configuration

        Pick the tab below that matches your runner. Each tab documents
        the same parameter schema with the appropriate variable names
        and CLI flags for that runner. The mechanical regen step keeps
        these tabs in sync as upstream config evolves, so the values
        you see are what's actually supported today.
        """)


def _make_added_scenario():
    return Scenario(
        name="foo-outage",
        scenario_type="foo_outage_scenarios",
        parameters=[
            Parameter(name="namespace", variable="NAMESPACE", type="string",
                      default="default", required=True, description="ns"),
        ],
    )


def _setup_website_fixture(root: Path):
    """Build a minimal content tree + digest dir."""
    docs = root / "content/en/docs"
    (docs / "scenarios" / "pod-scenario").mkdir(parents=True)
    (docs / "scenarios" / "pod-scenario" / "_index.md").write_text(
        "---\ntitle: Pod\n---\n\n" + ("filler " * 200)
    )
    digest = root / ".docs-sync-digest"
    digest.mkdir(parents=True)
    (digest / "TAXONOMY.json").write_text(json.dumps({
        "scenario_directories": ["foo-outage", "pod-scenario"],
        "scenario_types": ["foo_outage_scenarios", "pod_disruption_scenarios"],
        "cli_flags": ["--namespace"],
    }))
    return docs


class TestAddedScenarioE2E:
    def test_writes_index_md_when_draft_accepted_and_judge_clean(self, tmp_path: Path):
        """Happy path: LLM produces good draft, judge says clean, file written."""
        from orchestrator import _draft_added_scenarios

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()

        good_draft = DraftResult(accepted=True, body=_good_body(),
                                 rejections=[], response=None)
        clean_verdict = JudgeVerdict(
            verdict=JUDGE_VERDICT_CLEAN, reasoning="ok", flagged_phrases=[],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=good_draft), \
             patch("orchestrator.judge_draft", return_value=clean_verdict):
            written = _draft_added_scenarios([scenario], content_root=docs)

        assert written is not None
        assert len(written) == 1
        assert written[0].name == "_index.md"
        # File contains frontmatter + drafted body
        content = written[0].read_text()
        assert "title: Foo Outage" in content
        assert "Why this matters" in content

    def test_returns_none_when_draft_rejected_aborts_partial_output(
        self, tmp_path: Path,
    ):
        """If draft rejected, function returns None — orchestrator aborts.
        The test asserts NO file is written even with multiple scenarios in
        the same call (don't ship partial output)."""
        from orchestrator import _draft_added_scenarios

        docs = _setup_website_fixture(tmp_path)
        scenario_a = _make_added_scenario()
        scenario_b = Scenario(name="bar-outage", scenario_type="bar_outage_scenarios",
                              parameters=[])

        from agent.draft_new_scenario import RejectionReason
        bad_draft = DraftResult(
            accepted=False, body="bad", response=None,
            rejections=[RejectionReason("too_short", "too short")],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=bad_draft):
            written = _draft_added_scenarios(
                [scenario_a, scenario_b], content_root=docs,
            )
        assert written is None
        # No files written
        assert not (docs / "scenarios" / "foo-outage" / "_index.md").exists()
        assert not (docs / "scenarios" / "bar-outage" / "_index.md").exists()

    def test_writes_file_even_when_judge_flagged_logs_warning(
        self, tmp_path: Path, capsys,
    ):
        """Judge-flagged drafts still get written — the workflow YAML adds a
        `judge-flagged` label so a human reviews. We don't BLOCK on judge
        because the judge can be unavailable / wrong; the deterministic
        validate() is the actual gate."""
        from orchestrator import _draft_added_scenarios

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()

        good_draft = DraftResult(accepted=True, body=_good_body(),
                                 rejections=[], response=None)
        flagged = JudgeVerdict(
            verdict=JUDGE_VERDICT_FLAGGED,
            reasoning="suspicious phrase",
            flagged_phrases=["something_fake_scenarios"],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=good_draft), \
             patch("orchestrator.judge_draft", return_value=flagged):
            written = _draft_added_scenarios([scenario], content_root=docs)

        assert written is not None
        assert len(written) == 1
        # Verify warning surfaced for downstream label injection
        captured = capsys.readouterr()
        assert "flagged" in (captured.out + captured.err).lower()

    def test_handles_zero_added_scenarios(self, tmp_path: Path):
        """Empty input → empty output, no errors."""
        from orchestrator import _draft_added_scenarios
        docs = _setup_website_fixture(tmp_path)
        written = _draft_added_scenarios([], content_root=docs)
        assert written == []
