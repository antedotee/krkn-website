"""Tests for .docs-sync/regen/parameter_table.py.

The mechanical regen module rewrites parameter tables between AUTO:START
and AUTO:END markers. Critical constraints:
  - Must PRESERVE the original column layout (corpus has 6 distinct shapes)
  - Must ONLY touch content between markers — leave everything else alone
  - Must produce stable output (re-running on same input is a no-op)
  - Must select the right "first column" semantics per tab type:
      _tab-krkn-hub.md → first col = variable (env var name like NAMESPACE)
      _tab-krknctl.md → first col = name (kebab-case like namespace)
"""
from textwrap import dedent

import pytest

from extractors.krkn_hub import Parameter, Scenario
from regen.parameter_table import (
    parse_existing_table_columns,
    map_field_to_column,
    render_table_rows,
    regenerate_table,
)


# ─────────────────────────────────────────────────────────────────────────────
# parse_existing_table_columns
# ─────────────────────────────────────────────────────────────────────────────

class TestParseExistingTableColumns:
    def test_parses_simple_3col_header(self):
        text = dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description | Default |
            | --- | --- | --- |
            | OLD | old desc | none |
            <!-- AUTO:END -->
            """)
        cols = parse_existing_table_columns(text)
        assert cols == ["Parameter", "Description", "Default"]

    def test_parses_4col_header_no_outer_pipes(self):
        # Real krkn-hub corpus: tables WITHOUT leading/trailing pipes
        text = dedent("""\
            <!-- AUTO:START id="params" -->
            Parameter               | Description | Type | Default
            ----------------------- | ----------- | ---- | ------- |
            OLD_VAR                 | old desc    | str  | foo
            <!-- AUTO:END -->
            """)
        cols = parse_existing_table_columns(text)
        assert cols == ["Parameter", "Description", "Type", "Default"]

    def test_returns_none_when_no_table(self):
        text = "<!-- AUTO:START id=\"params\" -->\nNo table here.\n<!-- AUTO:END -->\n"
        assert parse_existing_table_columns(text) is None

    def test_returns_none_when_no_markers(self):
        text = "Just plain content, no markers.\n"
        assert parse_existing_table_columns(text) is None


# ─────────────────────────────────────────────────────────────────────────────
# map_field_to_column — schema field → which Parameter attribute
# ─────────────────────────────────────────────────────────────────────────────

class TestMapFieldToColumn:
    @pytest.mark.parametrize("col,attr", [
        ("Parameter", "variable"),         # env var name (krkn-hub-style tabs)
        ("parameter", "variable"),         # case insensitive
        ("Argument", "name"),              # CLI flag name (krknctl-style)
        ("Option", "name"),                # also CLI flag
        ("Description", "description"),
        ("Type", "type"),
        ("Default", "default"),
        ("Default Value", "default"),      # corpus has both forms
        ("Required", "required"),
    ])
    def test_known_columns_mapped(self, col, attr):
        assert map_field_to_column(col) == attr

    def test_unknown_column_returns_none(self):
        # Real corpus has `Possible Values`, `Example Values` — we don't
        # track those, so leave the column untouched (preserve old data).
        assert map_field_to_column("Possible Values") is None
        assert map_field_to_column("Random Header") is None


# ─────────────────────────────────────────────────────────────────────────────
# render_table_rows
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderTableRows:
    def _params(self):
        return [
            Parameter(name="namespace", variable="NAMESPACE", type="string",
                      default="openshift-*", required=False, description="Target ns"),
            Parameter(name="kill-count", variable="KILL_COUNT", type="number",
                      default="1", required=False, description="Pods to kill"),
        ]

    def test_renders_3col_param_desc_default(self):
        rows = render_table_rows(
            params=self._params(),
            columns=["Parameter", "Description", "Default"],
        )
        # Two rows
        assert len(rows) == 2
        # First column = variable (NAMESPACE), per the krkn-hub-style mapping
        assert "NAMESPACE" in rows[0]
        assert "Target ns" in rows[0]
        assert "openshift-*" in rows[0]

    def test_renders_argument_column_uses_name(self):
        # krknctl-style table: first col is "Argument" → use `name` not variable
        rows = render_table_rows(
            params=self._params(),
            columns=["Argument", "Type", "Description"],
        )
        # First col should be `namespace` (kebab-case name), not NAMESPACE
        assert "namespace" in rows[0]
        assert "NAMESPACE" not in rows[0]

    def test_required_rendered_as_lowercase(self):
        params = [Parameter(name="x", variable="X", type="string",
                            default="", required=True, description=".")]
        rows = render_table_rows(params, ["Parameter", "Required"])
        # required=True → "true" (matches corpus convention)
        assert "true" in rows[0]

    def test_unknown_column_left_blank(self):
        # If the existing table has a column we don't track, output an
        # empty cell rather than guessing or crashing.
        rows = render_table_rows(
            params=self._params(),
            columns=["Parameter", "Description", "Possible Values"],
        )
        # Two rows, last cell empty (just spaces between pipes)
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            assert cells[2] == ""  # Possible Values column is blank

    def test_pipe_in_value_escaped(self):
        params = [Parameter(name="x", variable="X", type="enum",
                            default="a|b|c", required=False, description=".")]
        rows = render_table_rows(params, ["Parameter", "Default"])
        # `a|b|c` becomes `a\|b\|c` so the table doesn't break
        assert "\\|" in rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# regenerate_table — full file edit
# ─────────────────────────────────────────────────────────────────────────────

class TestRegenerateTable:
    def _params_old(self):
        return [
            Parameter(name="ns", variable="NS", type="string",
                      default="default", required=False, description="old"),
        ]

    def _params_new(self):
        return [
            Parameter(name="ns", variable="NS", type="string",
                      default="default", required=False, description="old"),
            Parameter(name="new-flag", variable="NEW_FLAG", type="string",
                      default="", required=False, description="new"),
        ]

    def test_replaces_data_rows_inside_markers(self):
        original = dedent("""\
            Some intro text.

            <!-- AUTO:START id="params" -->
            | Parameter | Description | Default |
            | --- | --- | --- |
            | NS | old | default |
            <!-- AUTO:END -->

            Outro text.
            """)
        result = regenerate_table(original, params=self._params_new(), marker_id="params")
        # New row added, NS row preserved
        assert "NEW_FLAG" in result
        assert "NS" in result
        # Markers untouched
        assert 'AUTO:START id="params"' in result
        assert "AUTO:END" in result
        # Surrounding prose untouched
        assert "Some intro text." in result
        assert "Outro text." in result

    def test_preserves_column_layout(self):
        # Original has 4 cols; output should also have 4 cols
        original = dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description | Type | Default |
            | --- | --- | --- | --- |
            | NS | old | str | default |
            <!-- AUTO:END -->
            """)
        result = regenerate_table(original, params=self._params_new(), marker_id="params")
        # Header preserved verbatim
        assert "| Parameter | Description | Type | Default |" in result
        # Both rows have 4 data cells (5 pipe boundaries)
        for line in result.splitlines():
            if "NEW_FLAG" in line or line.startswith("| NS"):
                assert line.count("|") >= 5  # 5 pipes in 4-col with outer

    def test_idempotent_on_unchanged_input(self):
        original = dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description | Default |
            | --- | --- | --- |
            | NS | desc | default |
            <!-- AUTO:END -->
            """)
        params = [Parameter(name="ns", variable="NS", type="string",
                            default="default", required=False, description="desc")]
        once = regenerate_table(original, params=params, marker_id="params")
        twice = regenerate_table(once, params=params, marker_id="params")
        assert once == twice

    def test_no_change_if_markers_missing(self):
        # If a file has no AUTO markers, regen is a no-op (don't invent
        # markers — they need to be placed manually via Slice 0.5 first).
        original = "Just prose. No markers.\n"
        result = regenerate_table(original, params=self._params_new(), marker_id="params")
        assert result == original

    def test_handles_pipeless_table(self):
        # Real corpus has tables without leading/trailing pipes.
        original = dedent("""\
            <!-- AUTO:START id="params" -->
            Parameter | Description | Default
            --------- | ----------- | -------
            NS | old | default
            <!-- AUTO:END -->
            """)
        result = regenerate_table(original, params=self._params_new(), marker_id="params")
        # Header preserved (still pipeless)
        assert "Parameter | Description | Default" in result
        # New row added
        assert "NEW_FLAG" in result

    # === Slice 1B inspection finding R1 — empty trailing cell ===

    def test_empty_trailing_cell_preserves_column_count(self):
        # Real corpus has tables with 4 columns where some rows have empty
        # last cell. The pipeless-stripping logic was losing the trailing
        # empty cell, breaking column alignment.
        # Earned from real-corpus inspection R1.
        params = [
            Parameter(name="x", variable="X", type="string",
                      default="", required=False, description="."),  # empty default
        ]
        original = dedent("""\
            <!-- AUTO:START id="params" -->
            Parameter | Description | Type | Default
            --------- | ----------- | ---- | -------
            <!-- AUTO:END -->
            """)
        result = regenerate_table(original, params=params, marker_id="params")
        # Find the data row in the result
        for line in result.splitlines():
            if line.startswith("X "):
                # 4-column table → row must have 3 pipes (= 4 cells)
                assert line.count("|") >= 3, (
                    f"row has only {line.count('|')} pipes (expected 3 for 4 cols): {line!r}"
                )
                break
        else:
            pytest.fail("no data row starting with X found")
