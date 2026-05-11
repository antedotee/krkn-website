"""Tests for extractors/cerberus.py — diff-based ChangeSet extraction from
the cerberus upstream's llms-full.txt digest.

cerberus's digest format mirrors krkn-hub's intentionally so the parser
can be shared. The differences are semantic: a "scenario" here is a
config section (`cerberus`, `tunings`, `database`), the `scenario_type`
field is always `config_section`. Each section's leaf YAML keys are the
documented "parameters".
"""
from textwrap import dedent

import pytest

from extractors.cerberus import extract


_HEAD = dedent("""\
    # cerberus — full entity details

    ## scenario: cerberus
    scenario_type: config_section

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | distribution | distribution | string | openshift | false | Distribution can be kubernetes or openshift |
    | port | port | number | 8080 | false | http server port |
    | watch_nodes | watch_nodes | bool | True | false | monitor cluster nodes |
    | watch_pods | watch_pods | bool | True | false | NEW knob — monitor pod state |

    ## scenario: tunings
    scenario_type: config_section

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | timeout | timeout | number | 60 | false | request timeout seconds |
    | iterations | iterations | number | 5 | false | loop count |
    """)


_BASE = dedent("""\
    # cerberus — full entity details

    ## scenario: cerberus
    scenario_type: config_section

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | distribution | distribution | string | openshift | false | Distribution can be kubernetes or openshift |
    | port | port | number | 8080 | false | http server port |
    | watch_nodes | watch_nodes | bool | True | false | monitor cluster nodes |

    ## scenario: tunings
    scenario_type: config_section

    ### parameters

    | name | variable | type | default | required | description |
    | ---- | -------- | ---- | ------- | -------- | ----------- |
    | timeout | timeout | number | 60 | false | request timeout seconds |
    | iterations | iterations | number | 5 | false | loop count |
    """)


class TestExtract:
    def test_new_config_key_surfaces_as_modified_section(self):
        """Adding a `watch_pods` key to the `cerberus` section → that
        section appears in scenarios_modified, with the new key in
        parameters_added."""
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        cerberus_mod = [
            m for m in change_set.scenarios_modified if m.name == "cerberus"
        ]
        assert len(cerberus_mod) == 1
        added_keys = {p.name for p in cerberus_mod[0].parameters_added}
        assert "watch_pods" in added_keys

    def test_new_top_level_section_surfaces_as_added_scenario(self):
        head = _BASE + dedent("""\

            ## scenario: database
            scenario_type: config_section

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | database_path | database_path | string | /tmp/cerberus.db | false | sqlite path |
            """)
        change_set = extract(head_digest=head, base_digest=_BASE)
        names = {s.name for s in change_set.scenarios_added}
        assert "database" in names

    def test_removed_section_surfaces(self):
        """Inverse: swapping head/base so cerberus loses the database section."""
        head_with_db = _BASE + dedent("""\

            ## scenario: database
            scenario_type: config_section

            ### parameters

            | name | variable | type | default | required | description |
            | ---- | -------- | ---- | ------- | -------- | ----------- |
            | database_path | database_path | string | /tmp/cerberus.db | false | sqlite path |
            """)
        # head=BASE, base=BASE+database → database appears as removed
        change_set = extract(head_digest=_BASE, base_digest=head_with_db)
        removed = {s.name for s in change_set.scenarios_removed}
        assert "database" in removed

    def test_default_change_surfaces_as_field_modification(self):
        """Changing the default of `port` from 8080 to 9090 should flag
        port as modified with `default` in fields_changed."""
        head = _BASE.replace(
            "| port | port | number | 8080 |",
            "| port | port | number | 9090 |",
        )
        change_set = extract(head_digest=head, base_digest=_BASE)
        cerberus_mod = [
            m for m in change_set.scenarios_modified if m.name == "cerberus"
        ]
        assert len(cerberus_mod) == 1
        port_change = next(
            (pc for pc in cerberus_mod[0].parameters_modified if pc.name == "port"),
            None,
        )
        assert port_change is not None
        assert "default" in port_change.fields_changed

    def test_scenario_type_is_config_section(self):
        change_set = extract(head_digest=_HEAD, base_digest=_BASE)
        for m in change_set.scenarios_modified:
            assert m.head.scenario_type == "config_section"

    def test_no_changes_yields_empty_changeset(self):
        change_set = extract(head_digest=_HEAD, base_digest=_HEAD)
        assert change_set.scenarios_added == []
        assert change_set.scenarios_removed == []
        assert change_set.scenarios_modified == []

    def test_empty_digests_handled(self):
        change_set = extract(head_digest="", base_digest="")
        assert change_set.scenarios_added == []
        assert change_set.scenarios_removed == []
        assert change_set.scenarios_modified == []

    def test_description_change_detected(self):
        head = _BASE.replace(
            "http server port",
            "http server port where cerberus publishes status",
        )
        change_set = extract(head_digest=head, base_digest=_BASE)
        cerberus_mod = [
            m for m in change_set.scenarios_modified if m.name == "cerberus"
        ]
        assert len(cerberus_mod) == 1
        port_change = next(
            pc for pc in cerberus_mod[0].parameters_modified if pc.name == "port"
        )
        assert "description" in port_change.fields_changed
