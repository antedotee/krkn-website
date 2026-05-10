"""Tests for .docs-sync/migrate/add_auto_markers.py.

One-time migration: wrap existing parameter tables with AUTO markers so
Slice 1's mechanical regen has a target to update.

Real corpus has 6 different parameter-table shapes (see slice-0a-inspection.md).
Detection by first-column header keyword: parameter / option / argument.
Non-parameter tables (e.g. `Component | Description | Working`) must be skipped.
"""
from pathlib import Path
from textwrap import dedent

import pytest

from migrate.add_auto_markers import (
    is_parameter_table_header,
    find_parameter_tables,
    wrap_table_with_markers,
    process_file,
)


# ─────────────────────────────────────────────────────────────────────────────
# is_parameter_table_header
# ─────────────────────────────────────────────────────────────────────────────

class TestIsParameterTableHeader:
    @pytest.mark.parametrize("line", [
        "| Parameter | Description | Default |",
        "| Parameter | Type | Description |",
        "| Option | Type | Description |",
        "| Argument | Type | Description | Required | Default Value |",
        "|Parameter | Description | Default |",  # no leading space
        "| parameter | description | default |",  # lowercase
        "| PARAMETER | DESCRIPTION |",            # uppercase
        "|  Parameter   | Description | Default ",  # trailing space, no closing pipe
    ])
    def test_recognizes_real_param_table_headers(self, line):
        assert is_parameter_table_header(line) is True

    @pytest.mark.parametrize("line", [
        "| Component | Description | Working |",      # scenarios catalog — skip
        "| Name | Address | Phone |",                  # arbitrary table
        "| Step | Action | Result |",                  # workflow table
        "| --- | --- | --- |",                          # separator row, not header
        "Just a regular line, not a table",
        "",
    ])
    def test_rejects_non_parameter_tables(self, line):
        assert is_parameter_table_header(line) is False


# ─────────────────────────────────────────────────────────────────────────────
# find_parameter_tables — locate (start_line, end_line) ranges
# ─────────────────────────────────────────────────────────────────────────────

class TestFindParameterTables:
    def test_finds_single_param_table(self):
        text = dedent("""\
            Some intro paragraph.

            | Parameter | Description | Default |
            | --------- | ----------- | ------- |
            | foo       | does foo    | none    |
            | bar       | does bar    | true    |

            Some outro paragraph.
            """)
        tables = find_parameter_tables(text)
        assert len(tables) == 1
        start, end = tables[0]
        # Lines are 0-indexed. Header at line 2, separator at 3, rows at 4-5.
        # Range covers all 4 table lines inclusive.
        lines = text.splitlines()
        assert lines[start].startswith("| Parameter")
        assert lines[end].startswith("| bar")

    def test_skips_non_parameter_table_among_others(self):
        text = dedent("""\
            | Component | Description | Working |
            | --------- | ----------- | ------- |
            | foo       | does foo    | yes     |

            | Parameter | Description | Default |
            | --------- | ----------- | ------- |
            | x         | does x      | 1       |
            """)
        tables = find_parameter_tables(text)
        # Only the parameter table is reported, NOT the Component table.
        assert len(tables) == 1
        start, end = tables[0]
        lines = text.splitlines()
        assert lines[start].startswith("| Parameter")

    def test_handles_two_param_tables(self):
        text = dedent("""\
            | Parameter | Description |
            | --- | --- |
            | a | first table |

            Some prose.

            | Option | Type |
            | --- | --- |
            | b | string |
            """)
        tables = find_parameter_tables(text)
        assert len(tables) == 2

    def test_returns_empty_when_no_param_tables(self):
        text = "Just some prose. No tables here.\n"
        assert find_parameter_tables(text) == []

    def test_table_must_have_separator_row(self):
        # A "table" without `| --- |` separator isn't a valid markdown table
        # and we shouldn't wrap it.
        text = dedent("""\
            | Parameter | Description |
            | foo       | does foo    |
            """)
        tables = find_parameter_tables(text)
        assert tables == []

    # === Slice 0.5 inspection finding M1 — corpus has pipe-less tables ===

    def test_finds_table_without_leading_or_trailing_pipes(self):
        # The krkn-chaos corpus uses tables WITHOUT leading pipes. First
        # apply of the migration wrapped only the header+separator, missing
        # all data rows because `_TABLE_ROW_RE` required `^\s*\|`.
        # Earned from real-corpus inspection finding M1.
        text = dedent("""\
            Some intro.

            Parameter | Description | Type | Default
            --------- | ----------- | ---- | ------- |
            NAMESPACE | target ns   | str  | default
            POD_LABEL | optional    | str  | ""

            Outro.
            """)
        tables = find_parameter_tables(text)
        assert len(tables) == 1
        start, end = tables[0]
        lines = text.splitlines()
        # Header at index 2, separator at 3, data at 4-5 → end should be 5
        assert lines[start].startswith("Parameter ")
        assert lines[end].startswith("POD_LABEL ")

    def test_wraps_pipeless_table_with_all_data_rows(self):
        text = dedent("""\
            Parameter | Description | Type | Default
            --------- | ----------- | ---- | ------- |
            NAMESPACE | target ns   | str  | default
            POD_LABEL | optional    | str  | ""
            """)
        result = wrap_table_with_markers(text, marker_id="params")
        # Marker should land AFTER the last data row, not after the separator
        assert result.index("AUTO:END") > result.index("POD_LABEL")


# ─────────────────────────────────────────────────────────────────────────────
# wrap_table_with_markers
# ─────────────────────────────────────────────────────────────────────────────

class TestWrapTableWithMarkers:
    def test_wraps_with_auto_markers(self):
        text = dedent("""\
            Before.

            | Parameter | Description |
            | --- | --- |
            | foo | bar |

            After.
            """)
        result = wrap_table_with_markers(text, marker_id="params")
        assert "<!-- AUTO:START id=\"params\" -->" in result
        assert "<!-- AUTO:END -->" in result
        # Original table content preserved verbatim
        assert "| Parameter | Description |" in result
        assert "| foo | bar |" in result
        # Surrounding prose untouched
        assert "Before." in result
        assert "After." in result

    def test_wraps_multiple_tables(self):
        text = dedent("""\
            | Parameter | Description |
            | --- | --- |
            | a | one |

            | Option | Type |
            | --- | --- |
            | b | string |
            """)
        result = wrap_table_with_markers(text, marker_id="params")
        # Two pairs of markers, one per table
        assert result.count("<!-- AUTO:START") == 2
        assert result.count("<!-- AUTO:END -->") == 2

    def test_no_change_when_no_param_tables(self):
        text = "Just prose.\n"
        assert wrap_table_with_markers(text, marker_id="params") == text

    # === Idempotency — must be safe to re-run ===

    # === Slice 0.5 inspection finding M2 — trailing newline preservation ===

    def test_preserves_trailing_double_newline(self):
        # File ending in `\n\n` (blank line + final newline) must keep both.
        # Earned from inspection M2: re-runs were not idempotent because each
        # splitlines+join cycle dropped one trailing `\n` per file.
        text = "Parameter | Description |\n| --- | --- |\n| foo | bar |\n\n"
        result = wrap_table_with_markers(text, marker_id="params")
        # First pass adds markers and preserves count
        assert result.endswith("\n\n")

    def test_idempotency_byte_for_byte_with_trailing_blank_line(self):
        # The real bug: file ends with extra blank line, gets eaten on each run.
        text = "Parameter | Description |\n| --- | --- |\n| foo | bar |\n\n"
        once = wrap_table_with_markers(text, marker_id="params")
        twice = wrap_table_with_markers(once, marker_id="params")
        assert once == twice

    def test_does_not_double_wrap_already_wrapped_table(self):
        text = dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description |
            | --- | --- |
            | foo | bar |
            <!-- AUTO:END -->
            """)
        result = wrap_table_with_markers(text, marker_id="params")
        # Exactly one START and one END — not nested
        assert result.count("AUTO:START") == 1
        assert result.count("AUTO:END") == 1


# ─────────────────────────────────────────────────────────────────────────────
# process_file — full integration
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessFile:
    def test_writes_wrapped_content_back(self, tmp_path: Path):
        page = tmp_path / "tab.md"
        page.write_text(dedent("""\
            ---
            title: T
            ---

            | Parameter | Description |
            | --- | --- |
            | foo | bar |
            """))
        changed = process_file(page, marker_id="params")
        assert changed is True
        text = page.read_text()
        assert 'AUTO:START id="params"' in text
        assert "AUTO:END" in text
        # Frontmatter preserved
        assert "title: T" in text

    def test_returns_false_when_no_param_tables(self, tmp_path: Path):
        page = tmp_path / "tab.md"
        original = "Just prose, no tables.\n"
        page.write_text(original)
        changed = process_file(page, marker_id="params")
        assert changed is False
        assert page.read_text() == original

    def test_idempotent_on_already_processed_file(self, tmp_path: Path):
        page = tmp_path / "tab.md"
        page.write_text(dedent("""\
            <!-- AUTO:START id="params" -->
            | Parameter | Description |
            | --- | --- |
            | foo | bar |
            <!-- AUTO:END -->
            """))
        before = page.read_text()
        changed = process_file(page, marker_id="params")
        # Already wrapped → no change
        assert changed is False
        assert page.read_text() == before
