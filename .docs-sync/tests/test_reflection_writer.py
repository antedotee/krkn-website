"""Tests for reflection/writer.py — REFLECTION.md round-trip.

REFLECTION.md is the per-run learning record. The harvester reads N of
these from recent docs-sync PRs and condenses them into proposed
AGENTS.md / repo-map.yaml additions. So the round-trip parser must
survive maintainer edits inside the prose section without losing the
structured suggestions block.
"""
from pathlib import Path

import pytest

from reflection.writer import (
    OUTCOME_HUGO_FAILED,
    OUTCOME_PASS,
    OUTCOME_REJECTED,
    OUTCOME_SKIPPED,
    Reflection,
    Suggestion,
    SUGGESTION_AGENTS_RULE,
    SUGGESTION_SKIP_PATTERN,
    from_markdown,
    load,
    new_reflection,
    save,
    to_markdown,
)


@pytest.fixture
def reflection():
    r = new_reflection(
        upstream_repo="antedotee/krkn-hub",
        pr_number=42,
        head_sha="abc123def456",
        outcome=OUTCOME_PASS,
    )
    r.scenarios_processed = ["pod-scenarios", "node-cpu-hog"]
    r.token_usage_total = 1234
    r.token_usage_by_model = {"gemini-2.5-flash": 1000, "phi-4-mini-instruct": 234}
    r.retries = 1
    r.surprises = ["Pipeless table layout was unexpected; required has_outer_pipes path."]
    r.suggestions = [
        Suggestion(
            kind=SUGGESTION_AGENTS_RULE,
            text="Always check for pipeless tables before regen.",
            source_pr="antedotee/krkn-hub#42",
        ),
        Suggestion(
            kind=SUGGESTION_SKIP_PATTERN,
            text="docs/internal/*",
            source_pr="antedotee/krkn-hub#42",
        ),
    ]
    return r


# ─────────────────────────────────────────────────────────────────────────────
# new_reflection / construction
# ─────────────────────────────────────────────────────────────────────────────

class TestReflectionConstruction:
    def test_new_reflection_initializes_fields(self):
        r = new_reflection("o/r", 1, "sha", OUTCOME_PASS)
        assert r.upstream_repo == "o/r"
        assert r.pr_number == 1
        assert r.outcome == OUTCOME_PASS
        assert r.scenarios_processed == []
        assert r.surprises == []
        assert r.suggestions == []
        assert r.token_usage_total == 0
        assert r.retries == 0
        assert r.run_at  # ISO timestamp set

    def test_outcomes_are_distinct_constants(self):
        # The harvester pattern-matches on these — they must be stable strings
        outcomes = {OUTCOME_PASS, OUTCOME_SKIPPED, OUTCOME_REJECTED, OUTCOME_HUGO_FAILED}
        assert len(outcomes) == 4
        for o in outcomes:
            assert isinstance(o, str) and o


# ─────────────────────────────────────────────────────────────────────────────
# Markdown round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkdownRoundTrip:
    def test_round_trip_preserves_all_fields(self, reflection):
        md = to_markdown(reflection)
        recovered = from_markdown(md)
        assert recovered is not None
        assert recovered.upstream_repo == reflection.upstream_repo
        assert recovered.pr_number == reflection.pr_number
        assert recovered.head_sha == reflection.head_sha
        assert recovered.outcome == reflection.outcome
        assert recovered.scenarios_processed == reflection.scenarios_processed
        assert recovered.token_usage_total == reflection.token_usage_total
        assert recovered.token_usage_by_model == reflection.token_usage_by_model
        assert recovered.retries == reflection.retries
        assert recovered.surprises == reflection.surprises
        assert len(recovered.suggestions) == len(reflection.suggestions)
        for orig, rec in zip(reflection.suggestions, recovered.suggestions):
            assert rec.kind == orig.kind
            assert rec.text == orig.text
            assert rec.source_pr == orig.source_pr

    def test_human_readable_sections_present(self, reflection):
        md = to_markdown(reflection)
        assert "# docs-sync REFLECTION" in md
        assert "## Surprises" in md
        assert "## Suggestions" in md
        # Machine-parseable block too
        assert "```json" in md
        assert "<!-- BEGIN MACHINE REFLECTION" in md

    def test_source_pr_link_is_clickable(self, reflection):
        md = to_markdown(reflection)
        assert "[antedotee/krkn-hub#42]" in md
        assert "https://github.com/antedotee/krkn-hub/pull/42" in md

    def test_token_receipt_table_present(self, reflection):
        md = to_markdown(reflection)
        assert "## Token usage" in md
        assert "gemini-2.5-flash" in md
        assert "phi-4-mini-instruct" in md

    def test_human_edits_to_prose_dont_break_parse(self, reflection):
        # Maintainer adds a note above the JSON block; parser must still recover.
        md = to_markdown(reflection)
        injected = md.replace(
            "<!-- BEGIN MACHINE REFLECTION",
            "Maintainer note: I think suggestion #2 is too narrow.\n\n"
            "<!-- BEGIN MACHINE REFLECTION",
        )
        recovered = from_markdown(injected)
        assert recovered is not None
        assert recovered.upstream_repo == reflection.upstream_repo

    def test_outcomes_other_than_pass_serialize(self):
        for outcome in (OUTCOME_SKIPPED, OUTCOME_REJECTED, OUTCOME_HUGO_FAILED):
            r = new_reflection("o/r", 1, "sha", outcome)
            md = to_markdown(r)
            recovered = from_markdown(md)
            assert recovered is not None
            assert recovered.outcome == outcome


# ─────────────────────────────────────────────────────────────────────────────
# from_markdown — defensive parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestFromMarkdownDefensive:
    def test_returns_none_on_empty(self):
        assert from_markdown("") is None

    def test_returns_none_when_no_json_block(self):
        assert from_markdown("# just markdown") is None

    def test_returns_none_on_malformed_json(self):
        assert from_markdown("```json\n{not valid\n```\n") is None

    def test_returns_none_on_unknown_outcome_value(self):
        # outcome must be one of our 4 constants — anything else is suspect
        text = (
            "```json\n"
            '{"upstream_repo": "o/r", "pr_number": 1, "head_sha": "x", '
            '"outcome": "MAYBE", "run_at": "now", "scenarios_processed": [], '
            '"token_usage_total": 0, "token_usage_by_model": {}, "retries": 0, '
            '"surprises": [], "suggestions": []}\n'
            "```\n"
        )
        assert from_markdown(text) is None

    def test_unknown_suggestion_kind_is_dropped_not_fatal(self):
        # Future-proofing — if a future writer emits a new suggestion kind,
        # consumers should keep the known ones and skip the unknown.
        text = (
            "```json\n"
            '{"upstream_repo": "o/r", "pr_number": 1, "head_sha": "x", '
            '"outcome": "pass", "run_at": "now", "scenarios_processed": [], '
            '"token_usage_total": 0, "token_usage_by_model": {}, "retries": 0, '
            '"surprises": [], '
            '"suggestions": ['
            '{"kind": "agents_rule", "text": "ok", "source_pr": "o/r#1"},'
            '{"kind": "FUTURE_FORMAT", "text": "?", "source_pr": "o/r#1"}'
            ']}\n'
            "```\n"
        )
        r = from_markdown(text)
        assert r is not None
        assert len(r.suggestions) == 1
        assert r.suggestions[0].kind == SUGGESTION_AGENTS_RULE


# ─────────────────────────────────────────────────────────────────────────────
# save / load — disk I/O
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveLoad:
    def test_save_creates_parent_dirs(self, reflection, tmp_path: Path):
        path = tmp_path / "deeply/nested/REFLECTION.md"
        save(reflection, path)
        assert path.is_file()

    def test_round_trip_via_disk(self, reflection, tmp_path: Path):
        path = tmp_path / "REFLECTION.md"
        save(reflection, path)
        loaded = load(path)
        assert loaded is not None
        assert loaded.upstream_repo == reflection.upstream_repo
        assert len(loaded.suggestions) == 2

    def test_load_missing_file_returns_none(self, tmp_path: Path):
        assert load(tmp_path / "missing.md") is None
