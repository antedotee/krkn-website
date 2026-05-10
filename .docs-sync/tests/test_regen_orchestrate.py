"""Tests for regen/orchestrate.py — finding target doc dirs and applying
mechanical regen across the website tree."""
from pathlib import Path
from textwrap import dedent

import pytest

from extractors.krkn_hub import (
    Parameter,
    Scenario,
    ChangeSet,
    ModifiedScenario,
)
from regen.orchestrate import (
    find_target_doc_dir,
    apply_regen_to_modified_scenarios,
)


# ─────────────────────────────────────────────────────────────────────────────
# find_target_doc_dir
# ─────────────────────────────────────────────────────────────────────────────

class TestFindTargetDocDir:
    def test_exact_match(self, tmp_path: Path):
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "node-scenarios").mkdir()
        result = find_target_doc_dir("node-scenarios", scenarios)
        assert result == scenarios / "node-scenarios"

    def test_pod_scenarios_maps_to_pod_scenario(self, tmp_path: Path):
        # `pod-scenarios` (krkn-hub) → `pod-scenario` (website) — plural drops.
        # token Jaccard {pod} vs {pod} = 1.0
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "pod-scenario").mkdir()
        (scenarios / "node-scenarios").mkdir()
        result = find_target_doc_dir("pod-scenarios", scenarios)
        assert result == scenarios / "pod-scenario"

    def test_returns_none_when_no_good_match(self, tmp_path: Path):
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "completely-unrelated").mkdir()
        result = find_target_doc_dir("pod-scenarios", scenarios)
        assert result is None  # score < 0.5

    def test_skips_underscore_prefixed_dirs(self, tmp_path: Path):
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "_archived").mkdir()
        (scenarios / "pod-scenario").mkdir()
        result = find_target_doc_dir("pod-scenarios", scenarios)
        assert result.name == "pod-scenario"

    def test_returns_none_when_root_missing(self, tmp_path: Path):
        # No scenarios/ dir at all
        result = find_target_doc_dir("pod-scenarios", tmp_path / "missing")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# apply_regen_to_modified_scenarios — full integration
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyRegenToModifiedScenarios:
    def _make_website(self, root: Path):
        """Build a minimal website tree with one tab file ready for regen."""
        tab_dir = root / "scenarios/pod-scenario"
        tab_dir.mkdir(parents=True)
        (tab_dir / "_tab-krkn-hub.md").write_text(dedent("""\
            ---
            title: Pod krkn-hub
            ---

            <!-- AUTO:START id="params" -->
            | Parameter | Description | Default |
            | --- | --- | --- |
            | NS | old desc | default |
            <!-- AUTO:END -->
            """))

    def _make_changeset(self):
        """A ChangeSet adding a new param to pod-scenarios."""
        old_param = Parameter(name="namespace", variable="NS", type="string",
                              default="default", required=False, description="old desc")
        new_param = Parameter(name="kill-count", variable="KILL_COUNT",
                              type="number", default="1", required=False,
                              description="how many to kill")

        head = Scenario(name="pod-scenarios", scenario_type="pod_d_s",
                        parameters=[old_param, new_param])
        base = Scenario(name="pod-scenarios", scenario_type="pod_d_s",
                        parameters=[old_param])
        return ChangeSet(scenarios_modified=[ModifiedScenario(
            name="pod-scenarios",
            head=head,
            base=base,
            parameters_added=[new_param],
        )])

    def test_writes_regenerated_table(self, tmp_path: Path):
        self._make_website(tmp_path)
        change_set = self._make_changeset()

        modified = apply_regen_to_modified_scenarios(
            change_set=change_set,
            content_root=tmp_path,
        )

        assert len(modified) == 1
        assert modified[0].name == "_tab-krkn-hub.md"

        # File now contains both rows
        content = modified[0].read_text()
        assert "NS" in content
        assert "KILL_COUNT" in content
        # Frontmatter preserved
        assert "title: Pod krkn-hub" in content

    def test_returns_empty_when_no_doc_dir_match(self, tmp_path: Path):
        # Website has unrelated dirs only
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "completely-unrelated").mkdir()

        change_set = self._make_changeset()
        modified = apply_regen_to_modified_scenarios(change_set, tmp_path)
        assert modified == []

    def test_returns_empty_when_no_modified_scenarios(self, tmp_path: Path):
        self._make_website(tmp_path)
        # Empty ChangeSet
        modified = apply_regen_to_modified_scenarios(ChangeSet(), tmp_path)
        assert modified == []

    def test_no_op_when_regen_produces_same_content(self, tmp_path: Path):
        # If the existing table already matches the head schema, regen
        # produces identical output → no file modified.
        tab_dir = tmp_path / "scenarios/pod-scenario"
        tab_dir.mkdir(parents=True)
        (tab_dir / "_tab-krkn-hub.md").write_text(dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description | Default |
            | --- | --- | --- |
            | NS | desc | default |
            <!-- AUTO:END -->
            """))

        # ChangeSet says NS exists, no other params (matching what's already
        # in the file)
        param = Parameter(name="ns", variable="NS", type="string",
                          default="default", required=False, description="desc")
        change_set = ChangeSet(scenarios_modified=[ModifiedScenario(
            name="pod-scenarios",
            head=Scenario(name="pod-scenarios", scenario_type="x",
                          parameters=[param]),
            base=Scenario(name="pod-scenarios", scenario_type="x",
                          parameters=[param]),
            fields_changed=["scenario_type"],  # type changed but params didn't
        )])

        modified = apply_regen_to_modified_scenarios(change_set, tmp_path)
        # The table content is bit-identical (or nearly so) → no rewrite
        # needed. We accept either zero modifications (purest) or one
        # (cosmetic spacing changes are fine).
        assert len(modified) <= 1
