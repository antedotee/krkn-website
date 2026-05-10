"""Tests for .docs-sync/extractors/krkn_hub.py.

Parses llms-full.txt at head and base refs, produces a ChangeSet describing
what scenarios were added/removed/modified and (per scenario) which params
were added/removed/modified.
"""
from textwrap import dedent

import pytest

from extractors.krkn_hub import (
    parse_llms_full_txt,
    diff_scenarios,
    extract,
    Parameter,
    Scenario,
    ChangeSet,
)


# ─────────────────────────────────────────────────────────────────────────────
# parse_llms_full_txt
# ─────────────────────────────────────────────────────────────────────────────

class TestParseLlmsFullTxt:
    def test_parses_single_scenario(self):
        text = dedent("""\
            # krkn-hub — full scenario details

            ## scenario: pod-scenarios
            scenario_type: pod_disruption_scenarios

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | namespace | NAMESPACE | string | openshift-* | false | Target namespace |
            | kill-count | KILL_COUNT | number | 1 | false | Pods to kill |
            """)
        result = parse_llms_full_txt(text)

        assert "pod-scenarios" in result
        s = result["pod-scenarios"]
        assert s.name == "pod-scenarios"
        assert s.scenario_type == "pod_disruption_scenarios"
        assert len(s.parameters) == 2
        assert s.parameters[0].name == "namespace"
        assert s.parameters[0].variable == "NAMESPACE"
        assert s.parameters[0].default == "openshift-*"
        assert s.parameters[0].required is False
        assert s.parameters[1].name == "kill-count"

    def test_parses_multiple_scenarios(self):
        text = dedent("""\
            # title

            ## scenario: pod-scenarios
            scenario_type: pod_disruption_scenarios

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | a | A | string |  | false | first |

            ## scenario: container-scenarios
            scenario_type: container_scenarios

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | b | B | string |  | false | second |
            """)
        result = parse_llms_full_txt(text)
        assert set(result.keys()) == {"pod-scenarios", "container-scenarios"}

    def test_handles_required_true(self):
        text = dedent("""\
            ## scenario: x
            scenario_type: x_s

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | foo | FOO | string |  | true | required field |
            """)
        result = parse_llms_full_txt(text)
        assert result["x"].parameters[0].required is True

    def test_handles_scenario_with_no_parameters(self):
        text = dedent("""\
            ## scenario: rollback
            scenario_type: (unknown)

            (no documented parameters)
            """)
        result = parse_llms_full_txt(text)
        assert "rollback" in result
        # `(unknown)` literal becomes None — meaningful absence
        assert result["rollback"].scenario_type is None
        assert result["rollback"].parameters == []

    def test_returns_empty_dict_for_empty_input(self):
        assert parse_llms_full_txt("") == {}
        assert parse_llms_full_txt("# krkn-hub\n\n") == {}

    def test_handles_pipe_escape_in_cell(self):
        # Slice 0b's renderer escapes `|` as `\|` so the table parses cleanly.
        # The extractor must un-escape on the way back.
        text = dedent("""\
            ## scenario: x
            scenario_type: x_s

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | x | X | string |  | false | choose a or b \\| c |
            """)
        result = parse_llms_full_txt(text)
        assert "a or b | c" in result["x"].parameters[0].description


# ─────────────────────────────────────────────────────────────────────────────
# diff_scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestDiffScenarios:
    def _scen(self, name, scenario_type, params):
        return Scenario(
            name=name,
            scenario_type=scenario_type,
            parameters=[
                Parameter(name=n, variable=v, type=t, default=d,
                          required=r, description=desc)
                for (n, v, t, d, r, desc) in params
            ],
        )

    def test_added_scenario_in_head_only(self):
        head = {"new-scenario": self._scen("new-scenario", "x_s", [
            ("a", "A", "string", "", False, "."),
        ])}
        base = {}
        result = diff_scenarios(head, base)
        assert len(result.scenarios_added) == 1
        assert result.scenarios_added[0].name == "new-scenario"
        assert result.scenarios_removed == []
        assert result.scenarios_modified == []

    def test_removed_scenario_in_base_only(self):
        head = {}
        base = {"old": self._scen("old", "o_s", [])}
        result = diff_scenarios(head, base)
        assert len(result.scenarios_removed) == 1
        assert result.scenarios_added == []

    def test_unchanged_scenario_not_in_changeset(self):
        params = [("a", "A", "string", "default", False, "desc")]
        head = {"x": self._scen("x", "x_s", params)}
        base = {"x": self._scen("x", "x_s", params)}
        result = diff_scenarios(head, base)
        assert result.scenarios_added == []
        assert result.scenarios_removed == []
        assert result.scenarios_modified == []

    def test_modified_param_added(self):
        head = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "", False, "."),
            ("b", "B", "string", "", False, "."),
        ])}
        base = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "", False, "."),
        ])}
        result = diff_scenarios(head, base)
        assert len(result.scenarios_modified) == 1
        ms = result.scenarios_modified[0]
        assert ms.name == "x"
        assert len(ms.parameters_added) == 1
        assert ms.parameters_added[0].name == "b"
        assert ms.parameters_removed == []
        assert ms.parameters_modified == []

    def test_modified_param_removed(self):
        head = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "", False, "."),
        ])}
        base = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "", False, "."),
            ("b", "B", "string", "", False, "."),
        ])}
        result = diff_scenarios(head, base)
        ms = result.scenarios_modified[0]
        assert len(ms.parameters_removed) == 1
        assert ms.parameters_removed[0].name == "b"

    def test_modified_param_value_changed(self):
        head = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "newdefault", False, "."),
        ])}
        base = {"x": self._scen("x", "x_s", [
            ("a", "A", "string", "olddefault", False, "."),
        ])}
        result = diff_scenarios(head, base)
        ms = result.scenarios_modified[0]
        assert len(ms.parameters_modified) == 1
        pc = ms.parameters_modified[0]
        assert pc.name == "a"
        assert "default" in pc.fields_changed

    def test_scenario_type_change_shows_up_as_modified(self):
        params = [("a", "A", "string", "", False, ".")]
        head = {"x": self._scen("x", "new_type", params)}
        base = {"x": self._scen("x", "old_type", params)}
        result = diff_scenarios(head, base)
        assert len(result.scenarios_modified) == 1
        assert "scenario_type" in result.scenarios_modified[0].fields_changed


# ─────────────────────────────────────────────────────────────────────────────
# extract — full integration
# ─────────────────────────────────────────────────────────────────────────────

class TestExtract:
    def test_end_to_end_field_added(self):
        head = dedent("""\
            ## scenario: pod-scenarios
            scenario_type: pod_disruption_scenarios

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | namespace | NAMESPACE | string | default | false | ns |
            | new-flag | NEW_FLAG | string |  | false | newly added |
            """)
        base = dedent("""\
            ## scenario: pod-scenarios
            scenario_type: pod_disruption_scenarios

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | namespace | NAMESPACE | string | default | false | ns |
            """)
        result = extract(head_digest=head, base_digest=base)
        assert isinstance(result, ChangeSet)
        assert len(result.scenarios_modified) == 1
        ms = result.scenarios_modified[0]
        assert ms.name == "pod-scenarios"
        assert len(ms.parameters_added) == 1
        assert ms.parameters_added[0].name == "new-flag"

    def test_end_to_end_no_changes(self):
        text = dedent("""\
            ## scenario: x
            scenario_type: x_s

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | a | A | string |  | false | . |
            """)
        result = extract(head_digest=text, base_digest=text)
        assert result.scenarios_added == []
        assert result.scenarios_removed == []
        assert result.scenarios_modified == []

    def test_end_to_end_first_scenario_seen(self):
        head = dedent("""\
            ## scenario: brand-new
            scenario_type: brand_new_s

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | foo | FOO | string |  | false | . |
            """)
        # base is empty (the digest didn't exist yet, e.g. first PR)
        result = extract(head_digest=head, base_digest="")
        assert len(result.scenarios_added) == 1
        assert result.scenarios_added[0].name == "brand-new"
