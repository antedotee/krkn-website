"""Tests for .docs-sync/digest/extract_coverage.py.

Coverage finds drift between TAXONOMY.json and the actual doc directory tree.
Two failure modes it catches:
  1. scenario_type referenced in YAML config blocks but no doc directory exists
  2. doc directory exists but no scenario_type points at it (orphan)

The matching is token-Jaccard since krkn's snake_case config keys don't always
have a clean s/_/-/g transform to kebab-case doc directories.
"""
from pathlib import Path

import pytest

from digest.extract_coverage import (
    tokenize_scenario_type,
    tokenize_directory,
    jaccard,
    find_best_match,
    build_coverage,
)


# ─────────────────────────────────────────────────────────────────────────────
# Tokenization
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_scenario_type_strips_scenarios_suffix(self):
        assert tokenize_scenario_type("pod_disruption_scenarios") == {"pod", "disruption"}

    def test_scenario_type_strips_singular_scenario_suffix(self):
        assert tokenize_scenario_type("http_load_scenario") == {"http", "load"}

    def test_scenario_type_no_recognized_suffix_keeps_all_tokens(self):
        # Defensive — a scenario_type without _scenarios suffix shouldn't lose tokens
        assert tokenize_scenario_type("some_other_thing") == {"some", "other", "thing"}

    def test_directory_strips_scenarios_suffix(self):
        assert tokenize_directory("network-chaos-scenarios") == {"network", "chaos"}

    def test_directory_strips_singular_scenario_suffix(self):
        assert tokenize_directory("pod-scenario") == {"pod"}

    def test_directory_no_recognized_suffix(self):
        assert tokenize_directory("aurora-disruption") == {"aurora", "disruption"}

    # === Run 1 inspection finding C1 — plural normalization ===

    def test_normalizes_known_plural_outages(self):
        # `outages` is in the explicit allowlist so it normalizes to `outage`.
        # Earned from inspection C1 — `application_outages_scenarios` failed
        # to match `application-outage` because of the plural mismatch.
        assert tokenize_scenario_type("application_outages_scenarios") == {
            "application", "outage",
        }
        assert tokenize_directory("application-outage") == {"application", "outage"}
        # And the same on zone-outage pair
        assert tokenize_scenario_type("zone_outages_scenarios") == {"zone", "outage"}
        assert tokenize_directory("zone-outage-scenarios") == {"zone", "outage"}

    def test_does_not_mangle_chaos_or_other_non_plurals(self):
        # Run 2 regression — the generic "drop trailing -s" heuristic mangled
        # `chaos` to `chao`. Allowlist approach prevents this.
        assert tokenize_scenario_type("network_chaos_scenarios") == {
            "network", "chaos",
        }
        assert tokenize_directory("network-chaos-scenarios") == {"network", "chaos"}
        # Other Greek/Latin singulars-ending-in-s would also be safe by default
        # (they're not in the allowlist).

    def test_keeps_singular_unchanged(self):
        # `outage` is already singular and not in the plural keys
        assert tokenize_scenario_type("outage_scenarios") == {"outage"}


# ─────────────────────────────────────────────────────────────────────────────
# Jaccard similarity
# ─────────────────────────────────────────────────────────────────────────────

class TestJaccard:
    def test_identical_sets_score_1(self):
        assert jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets_score_0(self):
        assert jaccard({"a"}, {"b"}) == 0.0

    def test_one_empty_score_0(self):
        # Avoid ZeroDivisionError; empty set means "no info" → 0 similarity
        assert jaccard(set(), {"a"}) == 0.0
        assert jaccard({"a"}, set()) == 0.0

    def test_both_empty_score_0(self):
        assert jaccard(set(), set()) == 0.0

    def test_partial_overlap(self):
        # {a,b} & {a,c} = {a}, |union| = 3 → 1/3
        assert jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1.0 / 3.0)


# ─────────────────────────────────────────────────────────────────────────────
# Best-match search
# ─────────────────────────────────────────────────────────────────────────────

class TestFindBestMatch:
    def test_exact_match_wins(self):
        directories = ["pod-scenario", "node-scenarios", "container-scenario"]
        # node_scenarios → tokens {node} → best is node-scenarios (also {node})
        match, score = find_best_match("node_scenarios", directories)
        assert match == "node-scenarios"
        assert score == 1.0

    def test_partial_token_match(self):
        directories = ["pod-scenario", "pod-network-scenario", "node-scenarios"]
        # pod_disruption_scenarios → {pod, disruption}
        # vs pod-scenario {pod}: 1/2 = 0.5
        # vs pod-network-scenario {pod, network}: 1/3 ≈ 0.33
        # pod-scenario wins
        match, score = find_best_match("pod_disruption_scenarios", directories)
        assert match == "pod-scenario"
        assert score == pytest.approx(0.5)

    def test_no_match_returns_none_with_score_0(self):
        directories = ["aurora-disruption", "dns-outage"]
        match, score = find_best_match("cluster_shut_down_scenarios", directories)
        # No token overlap with any directory
        assert score == 0.0
        # Either return None or a candidate — implementation choice.
        # Test only that score communicates "no real match"
        assert score < 0.5

    def test_deterministic_ordering_on_tie(self):
        # If two directories tie at the same score, alphabetical (sorted) wins.
        directories = ["bbb-scenario", "aaa-scenario"]
        match, score = find_best_match("aaa_bbb_scenarios", directories)
        # Both tie at 1/2 — return the alphabetically first
        assert match == "aaa-scenario"

    def test_empty_directories_returns_no_match(self):
        match, score = find_best_match("pod_scenarios", [])
        assert score == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# build_coverage — full integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCoverage:
    def test_perfect_match_in_matched_list(self):
        result = build_coverage(
            scenario_types=["node_scenarios"],
            scenario_directories=["node-scenarios"],
        )
        assert len(result["matched"]) == 1
        assert result["matched"][0]["scenario_type"] == "node_scenarios"
        assert result["matched"][0]["directory"] == "node-scenarios"
        assert result["matched"][0]["score"] == 1.0
        assert result["scenario_types_without_directory"] == []
        # node-scenarios is matched, so not orphan
        assert result["directories_without_scenario_type"] == []

    def test_unmatched_scenario_type_listed(self):
        result = build_coverage(
            scenario_types=["cluster_shut_down_scenarios"],
            scenario_directories=["pod-scenario", "node-scenarios"],
        )
        # No match above threshold
        assert len(result["matched"]) == 0
        assert len(result["scenario_types_without_directory"]) == 1
        entry = result["scenario_types_without_directory"][0]
        assert entry["scenario_type"] == "cluster_shut_down_scenarios"
        # We surface the closest candidate even if below threshold —
        # helps maintainers triage whether to lower the threshold or rename.
        assert "best_candidate" in entry
        assert "best_score" in entry

    def test_orphan_directories_listed(self):
        result = build_coverage(
            scenario_types=["pod_disruption_scenarios"],
            scenario_directories=["pod-scenario", "aurora-disruption", "dns-outage"],
        )
        # pod-scenario is the match; aurora and dns are orphan
        assert "aurora-disruption" in result["directories_without_scenario_type"]
        assert "dns-outage" in result["directories_without_scenario_type"]
        assert "pod-scenario" not in result["directories_without_scenario_type"]

    def test_stats_block_present(self):
        result = build_coverage(
            scenario_types=["a_scenarios", "b_scenarios"],
            scenario_directories=["a-scenarios", "c-scenarios"],
        )
        s = result["stats"]
        assert s["total_scenario_types"] == 2
        assert s["total_directories"] == 2
        assert s["matched"] == 1            # a_scenarios ↔ a-scenarios
        assert s["unmatched_scenario_types"] == 1  # b_scenarios
        assert s["orphan_directories"] == 1  # c-scenarios

    def test_deterministic_output(self):
        scenario_types = ["pod_scenarios", "node_scenarios"]
        scenario_directories = ["pod-scenario", "node-scenarios"]

        r1 = build_coverage(scenario_types, scenario_directories)
        r2 = build_coverage(scenario_types, scenario_directories)
        assert r1 == r2
        # Lists sorted
        assert r1["directories_without_scenario_type"] == sorted(r1["directories_without_scenario_type"])

    def test_empty_inputs(self):
        result = build_coverage([], [])
        assert result["matched"] == []
        assert result["scenario_types_without_directory"] == []
        assert result["directories_without_scenario_type"] == []
        assert result["stats"]["total_scenario_types"] == 0
        assert result["stats"]["total_directories"] == 0

    def test_real_corpus_pairs_realistically(self):
        """End-to-end smoke test using realistic data from the corpus."""
        result = build_coverage(
            scenario_types=[
                "pod_disruption_scenarios",
                "node_scenarios",
                "network_chaos_scenarios",
                "cluster_shut_down_scenarios",  # known orphan
                "hog_scenarios",
            ],
            scenario_directories=[
                "pod-scenario",
                "node-scenarios",
                "network-chaos-scenario",
                "hog-scenarios",
                "aurora-disruption",  # known orphan dir
                "dns-outage",         # known orphan dir
            ],
        )
        matched_pairs = {(m["scenario_type"], m["directory"]) for m in result["matched"]}
        assert ("pod_disruption_scenarios", "pod-scenario") in matched_pairs
        assert ("node_scenarios", "node-scenarios") in matched_pairs
        assert ("network_chaos_scenarios", "network-chaos-scenario") in matched_pairs
        assert ("hog_scenarios", "hog-scenarios") in matched_pairs

        # cluster_shut_down_scenarios has no good match
        unmatched_st = {e["scenario_type"] for e in result["scenario_types_without_directory"]}
        assert "cluster_shut_down_scenarios" in unmatched_st

        # aurora and dns are orphans
        orphans = set(result["directories_without_scenario_type"])
        assert "aurora-disruption" in orphans
        assert "dns-outage" in orphans
