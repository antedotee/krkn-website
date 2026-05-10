"""Tests for audit/link_check.py — parses lychee JSON output into Finding
objects.

We don't actually run lychee in tests (no network access in CI for unit
tests). Instead the module is a parser around lychee's `--format json`
output, plus a thin runner that invokes lychee when called from the
audit CLI.
"""
import json
from pathlib import Path

import pytest

from audit import Finding
from audit.link_check import parse_lychee_output


class TestParseLycheeOutput:
    def test_extracts_failed_links_as_findings(self):
        """Lychee emits a JSON object with `fail_map` keyed by source file."""
        payload = json.dumps({
            "success": False,
            "fail_map": {
                "content/en/docs/scenarios/pod-scenario/_index.md": [
                    {"status": "404 Not Found",
                     "url": "https://example.com/missing"},
                    {"status": "timeout",
                     "url": "https://slow.example.com"},
                ],
                "content/en/docs/getting-started/_index.md": [
                    {"status": "500 Internal Server Error",
                     "url": "https://broken.example.com"},
                ],
            },
        })
        findings = parse_lychee_output(payload)
        assert len(findings) == 3
        # All findings are broken_link category
        assert {f.category for f in findings} == {"broken_link"}
        # URLs surfaced as the source for dedup
        urls = {f.source for f in findings}
        assert "https://example.com/missing" in urls
        assert "https://broken.example.com" in urls
        # The source file path appears in the detail so a human can fix it
        details = " ".join(f.detail for f in findings)
        assert "pod-scenario" in details
        assert "getting-started" in details

    def test_empty_fail_map_yields_no_findings(self):
        payload = json.dumps({"success": True, "fail_map": {}})
        assert parse_lychee_output(payload) == []

    def test_malformed_input_returns_empty(self):
        assert parse_lychee_output("not json") == []
        assert parse_lychee_output("") == []
        # Wrong shape (list at root)
        assert parse_lychee_output("[]") == []

    def test_missing_fail_map_returns_empty(self):
        """Older lychee versions emit a different schema — don't crash."""
        payload = json.dumps({"success": True})
        assert parse_lychee_output(payload) == []

    def test_finding_title_includes_status_code(self):
        payload = json.dumps({
            "success": False,
            "fail_map": {
                "docs/foo.md": [
                    {"status": "404 Not Found", "url": "https://x.test"},
                ],
            },
        })
        f = parse_lychee_output(payload)[0]
        assert "404" in f.title or "404" in f.detail
        assert "https://x.test" in f.title or "https://x.test" in f.source

    def test_handles_failure_records_without_url(self):
        """Defensive: tolerate truncated lychee entries."""
        payload = json.dumps({
            "success": False,
            "fail_map": {
                "docs/foo.md": [
                    {"status": "404"},  # no url
                    {"url": "https://ok.test", "status": "500"},
                ],
            },
        })
        findings = parse_lychee_output(payload)
        # Only the well-formed one becomes a finding
        assert len(findings) == 1
        assert findings[0].source == "https://ok.test"
