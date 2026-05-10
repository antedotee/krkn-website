"""Tests for .docs-sync/discovery.py — the 2-stage relevance gate.

Stage A (path_gate): pure-Python glob match against repo-map.yaml.
  Most-common case: PR touched only tests/ → exit early, no LLM, no work.

Stage B (digest_diff): fetch upstream's llms-full.txt at head and base
  refs, structurally diff. If the surface didn't change, exit silently.
"""
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from discovery import (
    load_repo_map,
    classify_paths,
    path_gate,
    digest_diff,
    PathClassification,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def repo_map(tmp_path: Path) -> Path:
    """A minimal repo-map.yaml with one upstream entry."""
    p = tmp_path / "repo-map.yaml"
    p.write_text(dedent("""\
        krkn-hub:
          doc_affecting_paths:
            - "*/env.sh"
            - "*/krknctl-input.json"
          always_skip_paths:
            - "tests/**"
            - "**/*.md"
            - ".github/**"
        """))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# load_repo_map
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadRepoMap:
    def test_parses_yaml_into_dict(self, repo_map: Path):
        data = load_repo_map(repo_map)
        assert "krkn-hub" in data
        assert "doc_affecting_paths" in data["krkn-hub"]

    def test_raises_on_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_repo_map(tmp_path / "nope.yaml")


# ─────────────────────────────────────────────────────────────────────────────
# classify_paths
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyPaths:
    def test_doc_affecting_path_classified_correctly(self, repo_map: Path):
        cfg = load_repo_map(repo_map)["krkn-hub"]
        result = classify_paths(["pod-scenarios/env.sh"], cfg)
        assert result.doc_affecting == ["pod-scenarios/env.sh"]
        assert result.skipped == []

    def test_always_skip_path_classified_correctly(self, repo_map: Path):
        cfg = load_repo_map(repo_map)["krkn-hub"]
        result = classify_paths(
            ["tests/test_pod.py", "README.md", ".github/workflows/x.yml"],
            cfg,
        )
        assert "tests/test_pod.py" in result.skipped
        assert "README.md" in result.skipped
        assert ".github/workflows/x.yml" in result.skipped
        assert result.doc_affecting == []

    def test_unknown_path_classified_as_other(self, repo_map: Path):
        cfg = load_repo_map(repo_map)["krkn-hub"]
        result = classify_paths(["random/file.py"], cfg)
        # Not in either list → "other" (defaults to skip-with-warning)
        assert result.doc_affecting == []
        assert "random/file.py" in result.other

    def test_mixed_paths(self, repo_map: Path):
        cfg = load_repo_map(repo_map)["krkn-hub"]
        result = classify_paths(
            ["pod-scenarios/env.sh", "tests/x.py", "random/y.py"],
            cfg,
        )
        assert result.doc_affecting == ["pod-scenarios/env.sh"]
        assert result.skipped == ["tests/x.py"]
        assert result.other == ["random/y.py"]

    def test_skip_takes_precedence_over_doc_affecting(self, repo_map: Path):
        # If a path matches both lists, skip wins. Defensive — prevents
        # "tests/scenario/env.sh"-style sneaky matches.
        cfg = {
            "doc_affecting_paths": ["**/env.sh"],
            "always_skip_paths": ["tests/**"],
        }
        result = classify_paths(["tests/scenario/env.sh"], cfg)
        assert "tests/scenario/env.sh" in result.skipped
        assert result.doc_affecting == []


# ─────────────────────────────────────────────────────────────────────────────
# path_gate — top-level Stage A
# ─────────────────────────────────────────────────────────────────────────────

class TestPathGate:
    def test_returns_pass_when_doc_affecting_paths_present(self, repo_map: Path):
        result = path_gate(
            changed_paths=["pod-scenarios/env.sh"],
            upstream_repo="krkn-hub",
            repo_map_path=repo_map,
        )
        assert result.passed is True

    def test_returns_skip_when_only_skip_paths_touched(self, repo_map: Path):
        result = path_gate(
            changed_paths=["tests/x.py", "README.md"],
            upstream_repo="krkn-hub",
            repo_map_path=repo_map,
        )
        assert result.passed is False
        # Reason explains WHY for telemetry — we want to learn from skips
        assert "skip" in result.reason.lower()

    def test_unknown_upstream_raises_clear_error(self, repo_map: Path):
        with pytest.raises(KeyError, match="krkn-ai"):
            path_gate(
                changed_paths=["foo/x.py"],
                upstream_repo="krkn-ai",  # not in repo-map.yaml
                repo_map_path=repo_map,
            )

    def test_empty_diff_skipped_with_clear_reason(self, repo_map: Path):
        result = path_gate(
            changed_paths=[],
            upstream_repo="krkn-hub",
            repo_map_path=repo_map,
        )
        assert result.passed is False
        assert "no changed paths" in result.reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# digest_diff — Stage B
# ─────────────────────────────────────────────────────────────────────────────

class TestDigestDiff:
    def test_identical_digests_means_no_change(self):
        # Same content at head and base → surface area unchanged → skip
        digest = "## scenario: pod-scenarios\nscenario_type: pod_disruption_scenarios\n"
        result = digest_diff(head_digest=digest, base_digest=digest)
        assert result.passed is False  # no change → skip
        assert "unchanged" in result.reason.lower()

    def test_different_digests_means_change(self):
        head = "## scenario: pod-scenarios\nscenario_type: pod_d_s\n"
        base = "## scenario: pod-scenarios\nscenario_type: pod_d_s\n### parameters\nfoo\n"
        result = digest_diff(head_digest=head, base_digest=base)
        assert result.passed is True

    def test_handles_empty_base_digest(self):
        # First-ever build of a scenario — base has no record
        head = "## scenario: new-scenario\nscenario_type: x\n"
        result = digest_diff(head_digest=head, base_digest="")
        assert result.passed is True
        assert "added" in result.reason.lower() or "new" in result.reason.lower()

    def test_handles_empty_head_digest(self):
        # Scenario removed in head but present in base
        base = "## scenario: removed-scenario\nscenario_type: x\n"
        result = digest_diff(head_digest="", base_digest=base)
        assert result.passed is True
