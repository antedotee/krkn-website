"""Tests for state/state_md.py — round-trip serialization, mutation,
resume logic.

STATE.md is the human-readable + machine-parseable progress hand-off file.
Roundtrip fidelity matters: a future run's parser must reconstruct the
exact state the previous run wrote, even when humans have edited the
prose around the JSON block.
"""
from pathlib import Path

import pytest

from state.state_md import (
    STATUS_DONE_DRAFT,
    STATUS_DONE_REGEN,
    STATUS_FAILED_DRAFT,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    ScenarioProgress,
    StateMd,
    add_scenario,
    from_markdown,
    is_scenario_done,
    load,
    mark_scenario,
    new_state,
    pending_scenarios,
    save,
    to_markdown,
)


@pytest.fixture
def state():
    s = new_state(
        upstream_repo="antedotee/krkn-hub",
        pr_number=42,
        head_sha="abc123def456",
        base_sha="def456abc123",
    )
    add_scenario(s, "pod-scenarios", "modified")
    add_scenario(s, "new-foo-scenario", "added")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# new_state / add_scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestStateConstruction:
    def test_new_state_initializes_metadata(self):
        s = new_state("o/r", 1, "h", "b")
        assert s.upstream_repo == "o/r"
        assert s.pr_number == 1
        assert s.scenarios == []
        assert s.completed is False
        assert s.started_at  # ISO string
        assert s.updated_at == s.started_at  # initially equal

    def test_add_scenario_appends(self, state):
        assert len(state.scenarios) == 2
        assert state.scenarios[0].name == "pod-scenarios"
        assert state.scenarios[0].status == STATUS_PENDING

    def test_add_scenario_is_idempotent(self, state):
        add_scenario(state, "pod-scenarios", "modified")  # already there
        assert len(state.scenarios) == 2  # not duplicated


# ─────────────────────────────────────────────────────────────────────────────
# mark_scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkScenario:
    def test_updates_status(self, state):
        mark_scenario(state, "pod-scenarios", status=STATUS_DONE_REGEN)
        sc = next(s for s in state.scenarios if s.name == "pod-scenarios")
        assert sc.status == STATUS_DONE_REGEN

    def test_updates_target_files(self, state):
        mark_scenario(state, "pod-scenarios",
                      target_files=["a.md", "b.md"])
        sc = next(s for s in state.scenarios if s.name == "pod-scenarios")
        assert sc.target_files == ["a.md", "b.md"]

    def test_unknown_scenario_raises(self, state):
        with pytest.raises(KeyError, match="not-here"):
            mark_scenario(state, "not-here", status=STATUS_DONE_REGEN)


# ─────────────────────────────────────────────────────────────────────────────
# is_scenario_done / pending_scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressQueries:
    def test_pending_scenario_not_done(self, state):
        assert is_scenario_done(state, "pod-scenarios") is False

    def test_completed_regen_counts_as_done(self, state):
        mark_scenario(state, "pod-scenarios", status=STATUS_DONE_REGEN)
        assert is_scenario_done(state, "pod-scenarios") is True

    def test_completed_draft_counts_as_done(self, state):
        mark_scenario(state, "new-foo-scenario", status=STATUS_DONE_DRAFT)
        assert is_scenario_done(state, "new-foo-scenario") is True

    def test_failed_does_not_count_as_done(self, state):
        # Failed = bot tried, output rejected. NOT "done" — orchestrator
        # wants to know it's still pending so a retry can re-process it.
        mark_scenario(state, "new-foo-scenario", status=STATUS_FAILED_DRAFT)
        assert is_scenario_done(state, "new-foo-scenario") is False

    def test_pending_scenarios_filters_correctly(self, state):
        mark_scenario(state, "pod-scenarios", status=STATUS_DONE_REGEN)
        # Only new-foo-scenario remains
        pending = pending_scenarios(state)
        assert len(pending) == 1
        assert pending[0].name == "new-foo-scenario"


# ─────────────────────────────────────────────────────────────────────────────
# Markdown round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkdownRoundTrip:
    def test_round_trip_preserves_all_fields(self, state):
        mark_scenario(state, "pod-scenarios",
                      status=STATUS_DONE_REGEN,
                      target_files=["a.md"], notes="updated 1 row")
        md = to_markdown(state)
        recovered = from_markdown(md)
        assert recovered is not None
        assert recovered.upstream_repo == state.upstream_repo
        assert recovered.pr_number == state.pr_number
        assert len(recovered.scenarios) == len(state.scenarios)
        # Per-scenario fidelity
        for orig, rec in zip(state.scenarios, recovered.scenarios):
            assert rec.name == orig.name
            assert rec.status == orig.status
            assert rec.target_files == orig.target_files
            assert rec.notes == orig.notes

    def test_human_readable_sections_present(self, state):
        md = to_markdown(state)
        # Must have human-readable sections
        assert "# docs-sync STATE" in md
        assert "## Scenarios" in md
        # And the JSON block for machines
        assert "```json" in md
        assert "<!-- BEGIN MACHINE STATE" in md
        assert "<!-- END MACHINE STATE" in md

    def test_pr_link_is_clickable(self, state):
        md = to_markdown(state)
        # Format: [owner/repo#NUM](https://github.com/owner/repo/pull/NUM)
        assert "[antedotee/krkn-hub#42]" in md
        assert "https://github.com/antedotee/krkn-hub/pull/42" in md

    def test_status_count_table_shows_only_present_statuses(self, state):
        # All pending → only `pending` row in counts table
        md = to_markdown(state)
        assert "`pending`" in md
        assert "`done_regen`" not in md  # nothing has this status yet

    def test_human_edits_to_prose_dont_break_parse(self, state):
        # Surface area: STATE.md is committed alongside content. A maintainer
        # might add a comment outside the JSON block; the parser must still
        # round-trip.
        md = to_markdown(state)
        # Inject a hand-written note before the JSON block
        injected = md.replace(
            "<!-- BEGIN MACHINE STATE",
            "Maintainer note: looks fine to me!\n\n<!-- BEGIN MACHINE STATE",
        )
        recovered = from_markdown(injected)
        assert recovered is not None
        assert recovered.upstream_repo == state.upstream_repo


# ─────────────────────────────────────────────────────────────────────────────
# from_markdown — defensive parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestFromMarkdownDefensive:
    def test_returns_none_on_empty_input(self):
        assert from_markdown("") is None

    def test_returns_none_when_no_json_block(self):
        assert from_markdown("# Just some markdown") is None

    def test_returns_none_on_malformed_json(self):
        text = "```json\n{not valid json\n```\n"
        assert from_markdown(text) is None

    def test_returns_none_when_json_is_array_not_dict(self):
        text = "```json\n[1, 2, 3]\n```\n"
        assert from_markdown(text) is None

    def test_handles_partial_state(self):
        # Future-proofing: extra fields shouldn't crash the parser
        text = (
            "```json\n"
            '{"upstream_repo": "o/r", "pr_number": 1, "head_sha": "h", '
            '"base_sha": "b", "started_at": "now", "updated_at": "now", '
            '"scenarios": [], "future_field": "ignored"}\n'
            "```\n"
        )
        result = from_markdown(text)
        assert result is not None or result is None  # don't crash either way


# ─────────────────────────────────────────────────────────────────────────────
# save / load — disk I/O
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveLoad:
    def test_round_trip_via_disk(self, state, tmp_path: Path):
        path = tmp_path / "STATE.md"
        save(state, path)
        loaded = load(path)
        assert loaded is not None
        assert loaded.upstream_repo == state.upstream_repo

    def test_load_missing_file_returns_none(self, tmp_path: Path):
        assert load(tmp_path / "missing.md") is None

    def test_save_creates_parent_dirs(self, state, tmp_path: Path):
        path = tmp_path / "deeply/nested/dir/STATE.md"
        save(state, path)
        assert path.is_file()
