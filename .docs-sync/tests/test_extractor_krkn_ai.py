"""Tests for extractors/krkn_ai.py — diff-based ChangeSet extraction from
the krkn-ai upstream's llms-full.txt digest.

krkn-ai's digest format mirrors krkn-hub's intentionally so the parser
can be shared. The differences are semantic: a "scenario" here is a CLI
command, the scenario_type field is always `cli_command` for now (other
entity kinds may come later — pydantic_model, scenario_class, etc.).
"""
from textwrap import dedent

import pytest

from extractors.krkn_ai import extract
from extractors.krkn_hub import Parameter


_HEAD = dedent("""\
    # krkn-ai — full entity details

    ## scenario: run
    scenario_type: cli_command
    description: Run Krkn-AI tests

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | kubeconfig | KUBECONFIG | string | (dynamic) | false | Path to kubeconfig |
    | config | CONFIG | string |  | false | Path to krkn-ai config file. |
    | seed | SEED | number |  | false | Random seed for reproducible runs. |

    ## scenario: discover
    scenario_type: cli_command
    description: Discover components for Krkn-AI tests

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | namespace | NAMESPACE | string | .* | false | Namespace(s) to discover |
    """)


_BASE = dedent("""\
    # krkn-ai — full entity details

    ## scenario: run
    scenario_type: cli_command
    description: Run Krkn-AI tests

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | kubeconfig | KUBECONFIG | string | (dynamic) | false | Path to kubeconfig |
    | config | CONFIG | string |  | false | Path to krkn-ai config file. |
    """)


class TestExtract:
    def test_added_command_surfaces_as_added_scenario(self):
        """A new CLI subcommand appearing in head_digest → scenarios_added."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        names = {s.name for s in change_set.scenarios_added}
        assert "discover" in names
        assert change_set.scenarios_removed == []

    def test_added_option_to_existing_command_surfaces_as_modified(self):
        """`run` gained a `--seed` option → scenarios_modified, with the
        new param in `parameters_added`."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        # `run` should be in modified (added the seed parameter)
        run_modified = [m for m in change_set.scenarios_modified if m.name == "run"]
        assert len(run_modified) == 1
        added_param_names = {p.name for p in run_modified[0].parameters_added}
        assert "seed" in added_param_names

    def test_removed_command_surfaces_as_removed_scenario(self):
        """A subcommand disappearing from head_digest → scenarios_removed."""
        # Swap head/base: now `discover` is in base but not head
        change_set = extract(head_digest=_BASE, base_digest=_HEAD)
        names = {s.name for s in change_set.scenarios_removed}
        assert "discover" in names

    def test_scenario_type_is_cli_command(self):
        """Every extracted entity should carry `scenario_type=cli_command`
        (the per-upstream sentinel that lets downstream routing know what
        kind of doc target to look for)."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        for s in change_set.scenarios_added:
            assert s.scenario_type == "cli_command"

    def test_empty_digests_yield_empty_changeset(self):
        change_set = extract(head_digest="", base_digest="")
        assert change_set.scenarios_added == []
        assert change_set.scenarios_removed == []
        assert change_set.scenarios_modified == []

    def test_same_digest_yields_no_changes(self):
        change_set = extract(head_digest=_HEAD, base_digest=_HEAD)
        assert change_set.scenarios_added == []
        assert change_set.scenarios_removed == []
        assert change_set.scenarios_modified == []

    def test_parameter_modification_detected(self):
        """Changing an existing option's help text should flag the parameter
        as modified."""
        head = _BASE.replace(
            "Path to krkn-ai config file.",
            "Path to krkn-ai config file (YAML or JSON).",
        )
        change_set = extract(head_digest=head, base_digest=_BASE)
        run_mod = [m for m in change_set.scenarios_modified if m.name == "run"]
        assert len(run_mod) == 1
        modified_names = {pc.name for pc in run_mod[0].parameters_modified}
        assert "config" in modified_names
        # And the changed field is `description`
        config_change = next(
            pc for pc in run_mod[0].parameters_modified if pc.name == "config"
        )
        assert "description" in config_change.fields_changed
