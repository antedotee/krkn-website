"""Tests for .docs-sync/digest/extract_taxonomy.py

The taxonomy is what later allows the judge stage to detect LLM hallucination —
"is this scenario_type the LLM mentioned actually a real one?". Every false
positive in extraction makes the judge weaker. Tests bias toward precision.
"""
import json
from pathlib import Path
from textwrap import dedent

import pytest

from digest.extract_taxonomy import (
    extract_scenario_directories,
    extract_scenario_types,
    extract_cli_flags,
    build_taxonomy,
)


# ─────────────────────────────────────────────────────────────────────────────
# extract_scenario_directories
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractScenarioDirectories:
    def test_returns_only_directories(self, tmp_path: Path):
        scenarios = tmp_path / "content/en/docs/scenarios"
        scenarios.mkdir(parents=True)
        (scenarios / "pod-scenario").mkdir()
        (scenarios / "network-chaos").mkdir()
        (scenarios / "_index.md").write_text("---\ntitle: x\n---\n")  # file, not dir
        (scenarios / "cloud_setup.md").write_text("body\n")           # file, not dir

        result = extract_scenario_directories(tmp_path / "content/en/docs")
        assert result == ["network-chaos", "pod-scenario"]  # sorted

    def test_skips_hidden_and_archived(self, tmp_path: Path):
        scenarios = tmp_path / "content/en/docs/scenarios"
        scenarios.mkdir(parents=True)
        (scenarios / "pod-scenario").mkdir()
        (scenarios / "_archived").mkdir()         # underscore prefix
        (scenarios / ".hidden").mkdir()           # dot prefix

        result = extract_scenario_directories(tmp_path / "content/en/docs")
        assert result == ["pod-scenario"]

    def test_recursive_one_level_for_nested_categories(self, tmp_path: Path):
        # Real corpus has e.g. scenarios/hog-scenarios/cpu-hog-scenario/
        scenarios = tmp_path / "content/en/docs/scenarios"
        scenarios.mkdir(parents=True)
        (scenarios / "hog-scenarios").mkdir()
        (scenarios / "hog-scenarios/cpu-hog-scenario").mkdir()
        (scenarios / "hog-scenarios/io-hog-scenario").mkdir()

        result = extract_scenario_directories(tmp_path / "content/en/docs")
        # Both top-level and nested directories included; sorted
        assert "hog-scenarios" in result
        assert "cpu-hog-scenario" in result
        assert "io-hog-scenario" in result

    def test_returns_empty_when_no_scenarios_dir(self, tmp_path: Path):
        (tmp_path / "content/en/docs").mkdir(parents=True)
        assert extract_scenario_directories(tmp_path / "content/en/docs") == []


# ─────────────────────────────────────────────────────────────────────────────
# extract_scenario_types — regex on digest text
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractScenarioTypes:
    def _write_page(self, dir: Path, slug: str, content: str):
        path = dir / f"{slug}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_matches_yaml_list_item(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "pod-scenario", dedent("""\
            kraken:
              chaos_scenarios:
                - pod_disruption_scenarios:
                  - path/to/x.yaml
                - node_scenarios:
                  - path/to/y.yaml
            """))
        result = extract_scenario_types(per_page)
        assert "pod_disruption_scenarios" in result
        assert "node_scenarios" in result
        assert result == sorted(result)  # deterministic sort

    def test_dedupes_across_files(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "a", "  - pod_disruption_scenarios:\n")
        self._write_page(per_page, "b", "  - pod_disruption_scenarios:\n")
        result = extract_scenario_types(per_page)
        assert result.count("pod_disruption_scenarios") == 1

    def test_skips_non_scenario_yaml_keys(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "x", dedent("""\
            kraken:
              cerberus_enabled: true
              chaos_scenarios:
                - foo_scenarios:
                  - x.yaml
            """))
        result = extract_scenario_types(per_page)
        # Only items ending in _scenario or _scenarios are taxonomy entries
        assert "foo_scenarios" in result
        assert "kraken" not in result
        assert "cerberus_enabled" not in result

    def test_handles_indent_variants(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "x", dedent("""\
                - deeply_indented_scenarios:
            -no_space_scenarios:
              -  extra_space_scenarios :
            - normal_scenarios:
            """))
        result = extract_scenario_types(per_page)
        # We accept variable indentation and trailing whitespace before colon
        assert "deeply_indented_scenarios" in result
        assert "normal_scenarios" in result

    def test_singular_scenario_also_matched(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "x", "- pod_disruption_scenario:\n")
        result = extract_scenario_types(per_page)
        assert "pod_disruption_scenario" in result

    def test_ignores_prose_mentions(self, tmp_path: Path):
        # "the pod_disruption scenario" in prose should NOT be extracted —
        # only YAML config-block keys (with leading "- " and trailing ":").
        per_page = tmp_path / "PER_PAGE"
        self._write_page(per_page, "x",
            "The pod_disruption_scenarios feature lets you kill pods.\n")
        result = extract_scenario_types(per_page)
        assert "pod_disruption_scenarios" not in result


# ─────────────────────────────────────────────────────────────────────────────
# extract_cli_flags — regex on digest text
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractCliFlags:
    def _write(self, d: Path, slug: str, body: str):
        p = d / f"{slug}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_extracts_from_command_line(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x",
            "krknctl run pod-scenario --config foo.yaml --namespace default\n")
        result = extract_cli_flags(per_page)
        assert "--config" in result
        assert "--namespace" in result
        assert result == sorted(result)

    def test_dedupes(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "a", "use --config x\n")
        self._write(per_page, "b", "or --config y\n")
        result = extract_cli_flags(per_page)
        assert result.count("--config") == 1

    def test_does_not_extract_from_url_path(self, tmp_path: Path):
        # URLs like https://github.com/foo--bar should NOT match
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x",
            "See https://example.com/path--with--dashes for details.\n")
        result = extract_cli_flags(per_page)
        assert "--with" not in result
        assert "--dashes" not in result

    def test_does_not_extract_yaml_separator(self, tmp_path: Path):
        # Markdown horizontal rule "---" or YAML separator must not match
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x", "---\nkey: value\n---\n")
        result = extract_cli_flags(per_page)
        assert all(not f.startswith("---") for f in result)
        # And no empty/short matches
        assert "--" not in result

    def test_handles_short_flag_with_long_form(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x", "Use -h or --help for usage.\n")
        result = extract_cli_flags(per_page)
        assert "--help" in result
        # Short flags (-h) are out of scope for V1 — too noisy
        assert "-h" not in result

    def test_minimum_flag_length(self, tmp_path: Path):
        # --x is too short to be a real flag (rare and likely false positive)
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x", "Use --x and --foo.\n")
        result = extract_cli_flags(per_page)
        assert "--foo" in result
        assert "--x" not in result  # less than 2 chars after --

    # === Run 1 inspection finding T2 — wildcard flag stems ===

    def test_rejects_trailing_hyphen_flag(self):
        # `--aws-*` family in prose should not produce `--aws-` (real flag never
        # ends in hyphen). Earned from real-corpus inspection.
        per_page = pytest.importorskip("pathlib")
        from pathlib import Path as P
        # Use tmp via fixture-less form
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = P(td) / "PER_PAGE"
            self._write(d, "x",
                "The `--aws-*`, `--azure-*` parameters configure cloud creds.\n"
                "For specifics, see `--aws-access-key-id`.\n")
            result = extract_cli_flags(d)
            # Real flags WITH trailing word survive
            assert "--aws-access-key-id" in result
            # Stems ending in hyphen are rejected
            assert "--aws-" not in result
            assert "--azure-" not in result

    # === Run 1 inspection finding T1 — CSS custom properties ===

    def test_rejects_css_custom_property_in_var_call(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        # Real source from _root.txt — _index.md homepage has inline <style>
        self._write(per_page, "x", dedent("""\
            <style>
            .hero {
              background: var(--krkn-bg);
              color: var(--krkn-text);
            }
            </style>
            """))
        result = extract_cli_flags(per_page)
        assert "--krkn-bg" not in result
        assert "--krkn-text" not in result

    def test_rejects_css_property_declaration(self, tmp_path: Path):
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x", dedent("""\
            :root {
              --krkn-primary: #0E58A0;
              --krkn-secondary: #EC1C24;
            }
            """))
        result = extract_cli_flags(per_page)
        assert "--krkn-primary" not in result
        assert "--krkn-secondary" not in result

    def test_still_extracts_real_flag_followed_by_space_then_colon(self, tmp_path: Path):
        # Doc tables like "--foo : description" should still extract --foo
        per_page = tmp_path / "PER_PAGE"
        self._write(per_page, "x", "  --config       : path to config file\n")
        result = extract_cli_flags(per_page)
        assert "--config" in result


# ─────────────────────────────────────────────────────────────────────────────
# build_taxonomy — integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTaxonomy:
    def test_returns_dict_with_expected_keys(self, tmp_path: Path):
        (tmp_path / "content/en/docs/scenarios/pod-scenario").mkdir(parents=True)
        (tmp_path / "PER_PAGE").mkdir()

        result = build_taxonomy(
            content_root=tmp_path / "content/en/docs",
            per_page_dir=tmp_path / "PER_PAGE",
        )
        assert set(result.keys()) == {
            "scenario_directories",
            "scenario_types",
            "cli_flags",
            "crd_names",
        }
        assert isinstance(result["scenario_directories"], list)
        assert isinstance(result["scenario_types"], list)
        assert isinstance(result["cli_flags"], list)
        assert isinstance(result["crd_names"], list)

    def test_deterministic_output(self, tmp_path: Path):
        (tmp_path / "content/en/docs/scenarios/pod-scenario").mkdir(parents=True)
        (tmp_path / "content/en/docs/scenarios/network-chaos").mkdir(parents=True)
        per_page = tmp_path / "PER_PAGE"
        per_page.mkdir()
        (per_page / "x.txt").write_text(
            "- pod_disruption_scenarios:\n--config foo --namespace bar\n"
        )

        r1 = build_taxonomy(tmp_path / "content/en/docs", per_page)
        r2 = build_taxonomy(tmp_path / "content/en/docs", per_page)
        assert r1 == r2
        # All lists sorted
        for key, val in r1.items():
            if isinstance(val, list):
                assert val == sorted(val), f"{key} not sorted: {val}"

    def test_serializes_to_json(self, tmp_path: Path):
        (tmp_path / "content/en/docs/scenarios/pod-scenario").mkdir(parents=True)
        per_page = tmp_path / "PER_PAGE"
        per_page.mkdir()

        result = build_taxonomy(tmp_path / "content/en/docs", per_page)
        # Must be JSON-serializable
        s = json.dumps(result)
        assert json.loads(s) == result
