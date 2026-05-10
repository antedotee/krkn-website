"""Tests for audit/issue_writer.py — formats findings into a markdown body
and upserts the weekly audit GitHub issue.

The issue is idempotent: re-running the cron with identical findings
edits the existing issue rather than opening a new one. When all checks
return empty, the open issue (if any) gets closed.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from audit import Finding
from audit.issue_writer import (
    AUDIT_ISSUE_LABEL,
    find_existing_audit_issue,
    format_issue_body,
    upsert_audit_issue,
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _f(category: str, source: str = "x", title: str | None = None,
       detail: str = "detail body") -> Finding:
    return Finding(
        category=category,
        title=title or f"finding for {source}",
        detail=detail,
        source=source,
    )


# ─────────────────────────────────────────────────────────────────────────────
# format_issue_body
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatIssueBody:
    def test_three_sections_present_when_all_categories_have_findings(self):
        body = format_issue_body([
            _f("coverage_gap", "foo_scenarios"),
            _f("deprecation", "old_thing"),
            _f("broken_link", "https://x"),
        ])
        # Section headings — each category gets its own.
        assert "Coverage gaps" in body
        assert "Deprecation" in body
        assert "Broken links" in body

    def test_empty_findings_yields_clean_body(self):
        body = format_issue_body([])
        # Should NOT include section headings if empty (less noise).
        assert "Coverage gaps" not in body
        # Should explain the clean state.
        assert "no findings" in body.lower() or "clean" in body.lower()

    def test_only_categories_with_findings_get_sections(self):
        body = format_issue_body([_f("coverage_gap", "foo_scenarios")])
        assert "Coverage gaps" in body
        # Sections that have no findings should NOT appear
        assert "Deprecation" not in body
        assert "Broken links" not in body

    def test_findings_grouped_by_category(self):
        body = format_issue_body([
            _f("coverage_gap", "scenario_alpha"),
            _f("broken_link", "https://x"),
            _f("coverage_gap", "scenario_beta"),
        ])
        # Coverage section should list both findings BEFORE Broken links starts
        cov_pos = body.find("Coverage gaps")
        bl_pos = body.find("Broken links")
        alpha_pos = body.find("scenario_alpha")
        beta_pos = body.find("scenario_beta")
        assert cov_pos < alpha_pos < bl_pos
        assert cov_pos < beta_pos < bl_pos

    def test_body_includes_marker_for_idempotent_matching(self):
        # The issue_writer needs a stable marker to recognize "this is the
        # audit issue I wrote last week" vs. a human-opened issue.
        body = format_issue_body([])
        assert "<!-- docs-sync:audit -->" in body


# ─────────────────────────────────────────────────────────────────────────────
# find_existing_audit_issue
# ─────────────────────────────────────────────────────────────────────────────

class TestFindExistingAuditIssue:
    def test_returns_open_issue_with_audit_label(self):
        gh_output = json.dumps([
            {"number": 42, "title": "weekly audit", "state": "OPEN",
             "labels": [{"name": AUDIT_ISSUE_LABEL}]},
        ])
        with patch("audit.issue_writer.run_command_safe",
                   return_value=_completed(stdout=gh_output)):
            n = find_existing_audit_issue("o/r")
        assert n == 42

    def test_returns_none_when_no_open_audit_issue(self):
        with patch("audit.issue_writer.run_command_safe",
                   return_value=_completed(stdout="[]")):
            assert find_existing_audit_issue("o/r") is None

    def test_returns_none_on_gh_error(self):
        with patch("audit.issue_writer.run_command_safe",
                   return_value=_completed(returncode=1, stderr="auth")):
            assert find_existing_audit_issue("o/r") is None


# ─────────────────────────────────────────────────────────────────────────────
# upsert_audit_issue — full flow
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertAuditIssue:
    def test_creates_new_issue_when_none_open(self):
        """No existing audit issue + findings → `gh issue create` invoked."""
        called: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            called.append(list(cmd))
            if "list" in cmd:
                return _completed(stdout="[]")  # no existing issue
            return _completed(stdout="https://github.com/o/r/issues/100")

        with patch("audit.issue_writer.run_command_safe", side_effect=fake_run):
            n = upsert_audit_issue("o/r", [_f("coverage_gap", "foo_scenarios")])
        # The `gh issue create` should have been the second call.
        creates = [c for c in called if "create" in c]
        assert len(creates) == 1
        # And the new issue's number is returned (or its URL parsed)
        assert n is not None

    def test_edits_existing_issue_when_findings_present(self):
        """Existing audit issue + findings → `gh issue edit` invoked, NOT create."""
        called: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            called.append(list(cmd))
            if "list" in cmd:
                return _completed(stdout=json.dumps([
                    {"number": 7, "title": "weekly audit", "state": "OPEN",
                     "labels": [{"name": AUDIT_ISSUE_LABEL}]},
                ]))
            return _completed()

        with patch("audit.issue_writer.run_command_safe", side_effect=fake_run):
            upsert_audit_issue("o/r", [_f("coverage_gap", "foo_scenarios")])
        # gh issue edit, NOT create
        assert any("edit" in c for c in called)
        assert not any("create" in c for c in called)

    def test_closes_existing_issue_when_all_clean(self):
        """Empty findings + open audit issue → close it. Don't open a new one."""
        called: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            called.append(list(cmd))
            if "list" in cmd:
                return _completed(stdout=json.dumps([
                    {"number": 9, "title": "weekly audit", "state": "OPEN",
                     "labels": [{"name": AUDIT_ISSUE_LABEL}]},
                ]))
            return _completed()

        with patch("audit.issue_writer.run_command_safe", side_effect=fake_run):
            upsert_audit_issue("o/r", [])
        assert any("close" in c for c in called)
        assert not any("create" in c for c in called)

    def test_does_nothing_when_no_findings_and_no_existing_issue(self):
        """Clean run with no prior issue → no API calls beyond the lookup."""
        called: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            called.append(list(cmd))
            if "list" in cmd:
                return _completed(stdout="[]")
            return _completed()

        with patch("audit.issue_writer.run_command_safe", side_effect=fake_run):
            result = upsert_audit_issue("o/r", [])
        # Only the lookup happened; no create/edit/close.
        assert all("create" not in c and "edit" not in c and "close" not in c
                   for c in called)
        assert result is None
