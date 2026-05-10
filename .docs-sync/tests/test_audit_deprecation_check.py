"""Tests for audit/deprecation_check.py — flags doc pages that reference
upstream entities (scenario_types or CLI flags) that no longer exist
upstream.

Inputs:
  - The website's CURRENT TAXONOMY.json (what its docs collectively know)
  - The UPSTREAM's CURRENT taxonomy (rebuilt by re-fetching its llms-full.txt)
  - PER_PAGE/*.txt digests (so we can locate which doc references the
    dropped entity)

The intersection produces "deprecated references" — high-confidence
signal that a doc page got stale because upstream removed the thing
the page documents.
"""
import json
from pathlib import Path

import pytest

from audit import Finding
from audit.deprecation_check import find_deprecated_references


def _write_taxonomy(path: Path, scenario_types, cli_flags=None) -> None:
    path.write_text(json.dumps({
        "scenario_types": scenario_types,
        "cli_flags": cli_flags or [],
        "scenario_directories": [],
    }), encoding="utf-8")


def _write_per_page(dir_: Path, slug: str, content: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{slug}.txt").write_text(content, encoding="utf-8")


class TestFindDeprecatedReferences:
    def test_scenario_type_removed_upstream_but_referenced_in_docs(
        self, tmp_path: Path,
    ):
        # Website's stored taxonomy lists `legacy_scenarios` (it's documented).
        # Upstream's current taxonomy no longer has it (it was removed).
        # A PER_PAGE file mentions it. That's a deprecated reference.
        website_tax = tmp_path / "TAXONOMY.json"
        _write_taxonomy(website_tax, ["legacy_scenarios", "pod_scenarios"])
        upstream_tax = tmp_path / "UPSTREAM_TAXONOMY.json"
        _write_taxonomy(upstream_tax, ["pod_scenarios"])

        per_page = tmp_path / "PER_PAGE"
        _write_per_page(per_page, "legacy", "This doc explains legacy_scenarios.")
        _write_per_page(per_page, "pod", "Pod stuff (pod_scenarios) info.")

        findings = find_deprecated_references(website_tax, upstream_tax, per_page)
        assert len(findings) == 1
        f = findings[0]
        assert f.category == "deprecation"
        assert "legacy_scenarios" in f.source
        # The doc that mentions it must be cited
        assert "legacy" in f.detail.lower()

    def test_cli_flag_removed_upstream_flagged(self, tmp_path: Path):
        website_tax = tmp_path / "TAXONOMY.json"
        website_tax.write_text(json.dumps({
            "scenario_types": [],
            "cli_flags": ["--old-flag", "--still-here"],
            "scenario_directories": [],
        }), encoding="utf-8")
        upstream_tax = tmp_path / "UPSTREAM_TAXONOMY.json"
        upstream_tax.write_text(json.dumps({
            "scenario_types": [],
            "cli_flags": ["--still-here"],
            "scenario_directories": [],
        }), encoding="utf-8")

        per_page = tmp_path / "PER_PAGE"
        _write_per_page(per_page, "config", "Use --old-flag to do X.")

        findings = find_deprecated_references(website_tax, upstream_tax, per_page)
        assert any("--old-flag" in f.source for f in findings)

    def test_entity_not_referenced_anywhere_no_finding(self, tmp_path: Path):
        """If the entity was removed AND no doc page mentions it, that's
        fine — nothing to clean up."""
        website_tax = tmp_path / "TAXONOMY.json"
        _write_taxonomy(website_tax, ["never_mentioned_scenarios"])
        upstream_tax = tmp_path / "UPSTREAM_TAXONOMY.json"
        _write_taxonomy(upstream_tax, [])

        per_page = tmp_path / "PER_PAGE"
        _write_per_page(per_page, "unrelated", "This doc is about something else.")

        findings = find_deprecated_references(website_tax, upstream_tax, per_page)
        assert findings == []

    def test_no_taxonomy_drift_no_findings(self, tmp_path: Path):
        """Upstream and website agree on everything → quiet week."""
        website_tax = tmp_path / "TAXONOMY.json"
        _write_taxonomy(website_tax, ["pod_scenarios"], ["--ns"])
        upstream_tax = tmp_path / "UPSTREAM_TAXONOMY.json"
        _write_taxonomy(upstream_tax, ["pod_scenarios"], ["--ns"])

        per_page = tmp_path / "PER_PAGE"
        _write_per_page(per_page, "pod", "uses pod_scenarios with --ns")

        assert find_deprecated_references(website_tax, upstream_tax, per_page) == []

    def test_missing_files_return_empty_not_raise(self, tmp_path: Path):
        """The audit cron MUST tolerate transient missing files."""
        assert find_deprecated_references(
            tmp_path / "missing1.json",
            tmp_path / "missing2.json",
            tmp_path / "missing-per-page",
        ) == []

    def test_multiple_docs_cite_same_removed_entity_combined(self, tmp_path: Path):
        """When 3 docs mention the same dropped scenario_type, the finding
        should list all of them — not three separate findings (that's just
        noise in the issue body)."""
        website_tax = tmp_path / "TAXONOMY.json"
        _write_taxonomy(website_tax, ["dropped_scenarios"])
        upstream_tax = tmp_path / "UPSTREAM_TAXONOMY.json"
        _write_taxonomy(upstream_tax, [])

        per_page = tmp_path / "PER_PAGE"
        _write_per_page(per_page, "a", "mentions dropped_scenarios here")
        _write_per_page(per_page, "b", "and dropped_scenarios again")
        _write_per_page(per_page, "c", "even more dropped_scenarios")

        findings = find_deprecated_references(website_tax, upstream_tax, per_page)
        # Exactly ONE finding for the dropped entity
        assert len(findings) == 1
        # All three doc slugs are mentioned in the detail
        for slug in ("a", "b", "c"):
            assert slug in findings[0].detail
