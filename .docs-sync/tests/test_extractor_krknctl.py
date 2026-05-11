"""Tests for extractors/krknctl.py — diff-based ChangeSet extraction from
the krknctl upstream's llms-full.txt digest.

krknctl's digest format mirrors krkn-hub's (intentionally — every
upstream emits the same shape so the parser is shared). The semantics
here: each "scenario" is a krknctl CLI subcommand (run, attach,
graph_run, list_available, etc.), `scenario_type` is always
`cli_command`, and each flag is a documented parameter. Nested
subcommands use underscore-joined names so siblings with the same leaf
name (e.g. `list_available` vs `random_available`) stay distinct.
"""
from textwrap import dedent

import pytest

from extractors.krknctl import extract


_HEAD = dedent("""\
    # krknctl — full entity details

    ## scenario: run
    scenario_type: cli_command
    description: runs a scenario

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | alerts-profile | ALERTS_PROFILE | string |  | false | custom alerts profile file path |
    | detached | DETACHED | bool |  | false | run in detached mode |
    | kubeconfig | KUBECONFIG | string |  | false | kubeconfig path |
    | metrics-profile | METRICS_PROFILE | string |  | false | custom metrics profile file path |

    ## scenario: list_available
    scenario_type: cli_command
    description: lists available scenarios

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | format | FORMAT | string |  | false | output format (table/json) |
    """)


_BASE = dedent("""\
    # krknctl — full entity details

    ## scenario: run
    scenario_type: cli_command
    description: runs a scenario

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | alerts-profile | ALERTS_PROFILE | string |  | false | custom alerts profile file path |
    | kubeconfig | KUBECONFIG | string |  | false | kubeconfig path |
    | metrics-profile | METRICS_PROFILE | string |  | false | custom metrics profile file path |
    """)


class TestExtract:
    def test_new_subcommand_surfaces_as_added(self):
        """`list_available` exists in head but not base."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        names = {s.name for s in change_set.scenarios_added}
        assert "list_available" in names

    def test_new_flag_on_existing_subcommand_surfaces_as_modified(self):
        """`run` gained `--detached` in head → scenarios_modified, with
        the new flag in parameters_added."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        run_mod = [m for m in change_set.scenarios_modified if m.name == "run"]
        assert len(run_mod) == 1
        added = {p.name for p in run_mod[0].parameters_added}
        assert "detached" in added

    def test_removed_subcommand_surfaces(self):
        change_set = extract(head_digest=_BASE, base_digest=_HEAD)
        names = {s.name for s in change_set.scenarios_removed}
        assert "list_available" in names

    def test_nested_subcommand_naming_preserved(self):
        """The digest builder uses `parent_child` composite names for
        nested cobra subcommands so siblings don't collide. The extractor
        must round-trip those names without mangling."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        added = next(
            (s for s in change_set.scenarios_added if s.name == "list_available"),
            None,
        )
        assert added is not None
        assert added.scenario_type == "cli_command"

    def test_flag_type_change_detected(self):
        """Changing a flag's type from string → int should flag the
        parameter as modified with `type` in fields_changed."""
        head = _BASE.replace(
            "| alerts-profile | ALERTS_PROFILE | string |",
            "| alerts-profile | ALERTS_PROFILE | int |",
        )
        change_set = extract(head_digest=head, base_digest=_BASE)
        run_mod = [m for m in change_set.scenarios_modified if m.name == "run"]
        assert len(run_mod) == 1
        ap_change = next(
            pc for pc in run_mod[0].parameters_modified if pc.name == "alerts-profile"
        )
        assert "type" in ap_change.fields_changed

    def test_scenario_type_is_cli_command(self):
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        for s in change_set.scenarios_added:
            assert s.scenario_type == "cli_command"

    def test_empty_digests_handled(self):
        change_set = extract(head_digest="", base_digest="")
        assert change_set.scenarios_added == []
        assert change_set.scenarios_modified == []
        assert change_set.scenarios_removed == []

    def test_no_changes_yields_empty_changeset(self):
        change_set = extract(head_digest=_HEAD, base_digest=_HEAD)
        assert change_set.scenarios_added == []
        assert change_set.scenarios_modified == []
        assert change_set.scenarios_removed == []

    def test_description_change_detected(self):
        head = _BASE.replace(
            "description: runs a scenario",
            "description: runs a single scenario (synchronous)",
        )
        change_set = extract(head_digest=head, base_digest=_BASE)
        # Scenario-level description isn't a tracked field in the diff
        # (scenarios_modified only fires on parameter changes OR
        # scenario_type changes per the existing diff_scenarios logic).
        # So an isolated description-only change SHOULD produce an
        # empty changeset — the digest_diff at Stage B will catch
        # it via raw text comparison, but we don't want it polluting
        # the ChangeSet with empty modifications.
        assert change_set.scenarios_modified == []
