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


class TestAddedScenarioStateTracking:
    """State.md must be updated as each scenario draft progresses so a
    failed mid-run leaves an actionable trail on the PR branch."""

    def test_state_marked_done_draft_after_accepted_draft(self, tmp_path: Path):
        from orchestrator import _draft_added_scenarios
        from state.state_md import (
            STATUS_DONE_DRAFT,
            add_scenario,
            load,
            new_state,
        )

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()
        state = new_state("o/r", 1, "h", "b")
        add_scenario(state, scenario.name, "added")
        state_path = tmp_path / "STATE.md"

        good_draft = DraftResult(accepted=True, body=_good_body(),
                                 rejections=[], response=None)
        clean_verdict = JudgeVerdict(
            verdict=JUDGE_VERDICT_CLEAN, reasoning="ok", flagged_phrases=[],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=good_draft), \
             patch("orchestrator.judge_draft", return_value=clean_verdict):
            written = _draft_added_scenarios(
                [scenario], content_root=docs,
                state=state, state_path=state_path,
            )

        assert written is not None and len(written) == 1
        # In-memory state updated
        sc = next(s for s in state.scenarios if s.name == scenario.name)
        assert sc.status == STATUS_DONE_DRAFT
        assert sc.target_files and sc.target_files[0].endswith("_index.md")
        # Disk state was persisted and round-trips
        loaded = load(state_path)
        assert loaded is not None
        loaded_sc = next(s for s in loaded.scenarios if s.name == scenario.name)
        assert loaded_sc.status == STATUS_DONE_DRAFT

    def test_state_marked_failed_draft_on_rejection(self, tmp_path: Path):
        from orchestrator import _draft_added_scenarios
        from agent.draft_new_scenario import RejectionReason
        from state.state_md import (
            STATUS_FAILED_DRAFT,
            add_scenario,
            load,
            new_state,
        )

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()
        state = new_state("o/r", 1, "h", "b")
        add_scenario(state, scenario.name, "added")
        state_path = tmp_path / "STATE.md"

        bad_draft = DraftResult(
            accepted=False, body="bad", response=None,
            rejections=[RejectionReason("too_short", "too short")],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=bad_draft):
            written = _draft_added_scenarios(
                [scenario], content_root=docs,
                state=state, state_path=state_path,
            )

        assert written is None
        # The rejection must have been recorded so the PR shows the failure.
        loaded = load(state_path)
        assert loaded is not None
        sc = next(s for s in loaded.scenarios if s.name == scenario.name)
        assert sc.status == STATUS_FAILED_DRAFT
        assert "too_short" in sc.notes

    def test_reflection_accumulates_tokens_across_draft_and_judge(self, tmp_path: Path):
        """Token usage must be summed per-model so REFLECTION.md can show
        the harvester where the budget actually went."""
        from orchestrator import _draft_added_scenarios
        from reflection.writer import OUTCOME_PASS, new_reflection
        from state.state_md import add_scenario, new_state

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()
        state = new_state("o/r", 1, "h", "b")
        add_scenario(state, scenario.name, "added")
        reflection = new_reflection("o/r", 1, "h", OUTCOME_PASS)

        good_draft = DraftResult(
            accepted=True, body=_good_body(), rejections=[],
            response=LLMResponse(
                content=_good_body(), model="gemini-2.5-flash",
                finish_reason="stop", prompt_tokens=100, completion_tokens=350,
            ),
            attempts=1,
        )
        clean_verdict = JudgeVerdict(
            verdict=JUDGE_VERDICT_CLEAN, reasoning="ok", flagged_phrases=[],
            response=LLMResponse(
                content="{}", model="phi-4-mini-instruct",
                finish_reason="stop", prompt_tokens=50, completion_tokens=20,
            ),
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=good_draft), \
             patch("orchestrator.judge_draft", return_value=clean_verdict):
            _draft_added_scenarios(
                [scenario], content_root=docs,
                state=state, state_path=tmp_path / "STATE.md",
                reflection=reflection,
            )

        # Per-model breakdown
        assert reflection.token_usage_by_model["gemini-2.5-flash"] == 350
        assert reflection.token_usage_by_model["phi-4-mini-instruct"] == 20
        assert reflection.token_usage_total == 370
        # No retries — accepted on first try
        assert reflection.retries == 0

    def test_reflection_counts_draft_retries(self, tmp_path: Path):
        """An accepted-on-attempt-2 draft must show retries=1 in REFLECTION."""
        from orchestrator import _draft_added_scenarios
        from reflection.writer import OUTCOME_PASS, new_reflection
        from state.state_md import add_scenario, new_state

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()
        state = new_state("o/r", 1, "h", "b")
        add_scenario(state, scenario.name, "added")
        reflection = new_reflection("o/r", 1, "h", OUTCOME_PASS)

        retried_draft = DraftResult(
            accepted=True, body=_good_body(), rejections=[],
            response=LLMResponse(
                content=_good_body(), model="gemini-2.5-flash",
                finish_reason="stop", prompt_tokens=100, completion_tokens=400,
            ),
            attempts=2,
        )
        clean_verdict = JudgeVerdict(
            verdict=JUDGE_VERDICT_CLEAN, reasoning="ok", flagged_phrases=[],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=retried_draft), \
             patch("orchestrator.judge_draft", return_value=clean_verdict):
            _draft_added_scenarios(
                [scenario], content_root=docs,
                state=state, state_path=tmp_path / "STATE.md",
                reflection=reflection,
            )

        assert reflection.retries == 1

    def test_state_notes_judge_flagged_drafts(self, tmp_path: Path):
        from orchestrator import _draft_added_scenarios
        from state.state_md import (
            STATUS_DONE_DRAFT,
            add_scenario,
            load,
            new_state,
        )

        docs = _setup_website_fixture(tmp_path)
        scenario = _make_added_scenario()
        state = new_state("o/r", 1, "h", "b")
        add_scenario(state, scenario.name, "added")
        state_path = tmp_path / "STATE.md"

        good_draft = DraftResult(accepted=True, body=_good_body(),
                                 rejections=[], response=None)
        flagged = JudgeVerdict(
            verdict=JUDGE_VERDICT_FLAGGED, reasoning="suspect",
            flagged_phrases=["something_fake"],
        )

        with patch("orchestrator.draft_new_scenario_prose", return_value=good_draft), \
             patch("orchestrator.judge_draft", return_value=flagged):
            _draft_added_scenarios(
                [scenario], content_root=docs,
                state=state, state_path=state_path,
            )

        loaded = load(state_path)
        assert loaded is not None
        sc = next(s for s in loaded.scenarios if s.name == scenario.name)
        # File still written (judge doesn't block); state note flags it for review
        assert sc.status == STATUS_DONE_DRAFT
        assert "judge-flagged" in sc.notes
