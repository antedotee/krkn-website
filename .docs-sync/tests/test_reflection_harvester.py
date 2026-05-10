"""Tests for reflection/harvester.py — finds and reads recent REFLECTION.md
files from docs-sync PRs on the website repo.

The harvester is the "input layer" of the self-improvement loop:
  PRs in GitHub  →  harvester (this module)  →  consolidator (LLM)  →  PR

This module only does deterministic plumbing — find PRs, fetch each one's
REFLECTION.md from its branch, parse it. The LLM judgment lives in the
consolidator. Keep the boundary clean so the harvester is unit-testable
without an API key.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from reflection.harvester import (
    PrSummary,
    fetch_reflection,
    find_recent_docs_sync_prs,
    harvest,
)
from reflection.writer import (
    OUTCOME_PASS,
    OUTCOME_REJECTED,
    Reflection,
    Suggestion,
    SUGGESTION_AGENTS_RULE,
    new_reflection,
    to_markdown,
)


def _completed_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Build a CompletedProcess-like mock for run_command_safe."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ─────────────────────────────────────────────────────────────────────────────
# find_recent_docs_sync_prs
# ─────────────────────────────────────────────────────────────────────────────

class TestFindRecentDocsSyncPrs:
    def test_filters_by_label_and_age(self):
        """Only PRs with label `docs-sync` AND closed within `days` are returned."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=2)).isoformat()
        old = (now - timedelta(days=30)).isoformat()
        gh_output = json.dumps([
            {"number": 1, "state": "MERGED", "headRefName": "docs-sync/x-abc",
             "closedAt": recent, "url": "https://github.com/o/r/pull/1"},
            {"number": 2, "state": "CLOSED", "headRefName": "docs-sync/y-def",
             "closedAt": old, "url": "https://github.com/o/r/pull/2"},
            {"number": 3, "state": "MERGED", "headRefName": "docs-sync/z-ghi",
             "closedAt": recent, "url": "https://github.com/o/r/pull/3"},
        ])
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout=gh_output)):
            prs = find_recent_docs_sync_prs("o/r", days=7)

        # PR #2 is too old; #1 and #3 should remain
        assert {p.number for p in prs} == {1, 3}
        assert all(isinstance(p, PrSummary) for p in prs)

    def test_handles_empty_pr_list(self):
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout="[]")):
            prs = find_recent_docs_sync_prs("o/r", days=7)
        assert prs == []

    def test_includes_both_merged_and_closed_state(self):
        """Closed-without-merge PRs are EXTRA valuable — they show what the
        bot got wrong. Don't filter them out."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        gh_output = json.dumps([
            {"number": 10, "state": "CLOSED", "headRefName": "docs-sync/rejected",
             "closedAt": recent, "url": "https://github.com/o/r/pull/10"},
            {"number": 11, "state": "MERGED", "headRefName": "docs-sync/accepted",
             "closedAt": recent, "url": "https://github.com/o/r/pull/11"},
        ])
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout=gh_output)):
            prs = find_recent_docs_sync_prs("o/r", days=7)
        assert {p.state for p in prs} == {"CLOSED", "MERGED"}

    def test_skips_still_open_prs(self):
        """Open PRs have no final outcome yet — exclude until they close."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        gh_output = json.dumps([
            {"number": 20, "state": "OPEN", "headRefName": "docs-sync/draft",
             "closedAt": None, "url": "https://github.com/o/r/pull/20"},
        ])
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout=gh_output)):
            prs = find_recent_docs_sync_prs("o/r", days=7)
        assert prs == []

    def test_malformed_gh_output_returns_empty(self):
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout="not json")):
            prs = find_recent_docs_sync_prs("o/r", days=7)
        assert prs == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_reflection — pull REFLECTION.md from a PR's head branch
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchReflection:
    def test_returns_parsed_reflection_on_success(self):
        r = new_reflection("o/r", 1, "head", OUTCOME_PASS)
        r.scenarios_processed = ["foo"]
        md = to_markdown(r)
        # gh api returns base64-encoded content
        import base64
        encoded = base64.b64encode(md.encode("utf-8")).decode("ascii")
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout=encoded)):
            result = fetch_reflection("o/r", "docs-sync/foo")
        assert result is not None
        assert result.upstream_repo == "o/r"
        assert result.scenarios_processed == ["foo"]

    def test_returns_none_when_file_missing(self):
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(returncode=1, stderr="404 not found")):
            result = fetch_reflection("o/r", "docs-sync/missing")
        assert result is None

    def test_returns_none_on_unparseable_content(self):
        # gh returned base64, decodes to garbage that's not a reflection
        import base64
        garbage = base64.b64encode(b"# random markdown\n").decode("ascii")
        with patch("reflection.harvester.run_command_safe",
                   return_value=_completed_proc(stdout=garbage)):
            result = fetch_reflection("o/r", "docs-sync/bad")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# harvest — top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

class TestHarvest:
    def test_collects_reflections_from_all_recent_prs(self):
        """harvest() should call find_recent + fetch_reflection for each,
        returning the successful ones."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()

        # Two PRs found; one has a REFLECTION.md, one doesn't.
        r1 = new_reflection("o/r", 1, "h1", OUTCOME_PASS)
        r1.scenarios_processed = ["a"]

        gh_pr_list = json.dumps([
            {"number": 1, "state": "MERGED", "headRefName": "docs-sync/a",
             "closedAt": recent, "url": "https://github.com/o/r/pull/1"},
            {"number": 2, "state": "CLOSED", "headRefName": "docs-sync/b",
             "closedAt": recent, "url": "https://github.com/o/r/pull/2"},
        ])

        def fake_run(cmd, **kwargs):
            # gh pr list call
            if "pr" in cmd and "list" in cmd:
                return _completed_proc(stdout=gh_pr_list)
            # gh api contents — branch a has REFLECTION.md, b does not
            if "docs-sync/a" in " ".join(cmd):
                import base64
                return _completed_proc(stdout=base64.b64encode(
                    to_markdown(r1).encode("utf-8")).decode("ascii"))
            return _completed_proc(returncode=1, stderr="404 not found")

        with patch("reflection.harvester.run_command_safe", side_effect=fake_run):
            harvested = harvest("o/r", days=7)

        # Only PR#1's reflection should come back. PR#2's missing file is a
        # silent skip (it's normal for very old runs to not have REFLECTION.md).
        assert len(harvested) == 1
        assert harvested[0].pr_number == 1
        assert harvested[0].scenarios_processed == ["a"]
