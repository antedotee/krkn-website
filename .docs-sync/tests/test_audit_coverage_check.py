"""Tests for audit/coverage_check.py — flags scenario_types in the upstream
taxonomy that have no corresponding doc directory.

This is the SCENARIO-WITHOUT-DOC direction. The DOC-WITHOUT-SCENARIO
direction (likely-deprecated docs) is handled by deprecation_check.
"""
import json
from pathlib import Path

import pytest

from audit.coverage_check import Finding, find_coverage_gaps


def _write_coverage(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "COVERAGE.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestFindCoverageGaps:
    def test_unmatched_scenario_types_become_findings(self, tmp_path: Path):
        path = _write_coverage(tmp_path, {
            "scenario_types_without_directory": [
                {"scenario_type": "cluster_shut_down_scenarios",
                 "best_candidate": "managed-cluster-scenario",
                 "best_score": 0.25},
                {"scenario_type": "kubevirt_vm_outage_scenarios",
                 "best_candidate": None,
                 "best_score": 0.0},
            ],
            "directories_without_scenario_type": [],
            "matched": [], "stats": {},
        })
        findings = find_coverage_gaps(path)
        assert len(findings) == 2
        scenario_types = [f.source for f in findings]
        assert "cluster_shut_down_scenarios" in scenario_types
        # The near-match candidate is surfaced for triage
        cluster = next(f for f in findings if f.source == "cluster_shut_down_scenarios")
        assert "managed-cluster-scenario" in cluster.detail

    def test_no_gaps_returns_empty(self, tmp_path: Path):
        path = _write_coverage(tmp_path, {
            "scenario_types_without_directory": [],
            "directories_without_scenario_type": [],
            "matched": [], "stats": {},
        })
        assert find_coverage_gaps(path) == []

    def test_missing_file_returns_empty_not_raise(self, tmp_path: Path):
        # The audit cron must NEVER crash on a missing digest — it runs
        # weekly and the digest could be transiently absent.
        assert find_coverage_gaps(tmp_path / "missing.json") == []

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        p = tmp_path / "COVERAGE.json"
        p.write_text("not json", encoding="utf-8")
        assert find_coverage_gaps(p) == []

    def test_unmatched_without_candidate_still_reports(self, tmp_path: Path):
        # When `best_score` is 0 or best_candidate is None, the digest
        # still emits the scenario_type — we want a finding for it.
        path = _write_coverage(tmp_path, {
            "scenario_types_without_directory": [
                {"scenario_type": "fully_orphan_scenarios"},
            ],
            "directories_without_scenario_type": [],
            "matched": [], "stats": {},
        })
        findings = find_coverage_gaps(path)
        assert len(findings) == 1
        assert findings[0].source == "fully_orphan_scenarios"
        assert findings[0].category == "coverage_gap"

    def test_finding_title_is_human_readable(self, tmp_path: Path):
        path = _write_coverage(tmp_path, {
            "scenario_types_without_directory": [
                {"scenario_type": "foo_scenarios"},
            ],
            "directories_without_scenario_type": [],
            "matched": [], "stats": {},
        })
        f = find_coverage_gaps(path)[0]
        assert "foo_scenarios" in f.title
        # Should NOT just be the raw key name — must contain explanatory phrasing
        assert f.title != "foo_scenarios"
