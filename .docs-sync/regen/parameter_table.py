"""Mechanical regen of parameter tables between AUTO:START / AUTO:END markers.

Critical constraints earned from inspections:
  - Must preserve the original column layout (corpus has 6 distinct shapes)
  - Must only touch content between markers — leave everything else alone
  - Must produce stable output (re-running on same input is a no-op)
  - Must select first-column semantics from the existing header:
      "Parameter" → variable (env var, krkn-hub-style)
      "Argument" / "Option" → name (CLI flag, krknctl-style)
"""
from __future__ import annotations

import re

from extractors.krkn_hub import Parameter


# Map from existing column header (case-insensitive, normalized) → which
# Parameter attribute to source the cell value from.
_COLUMN_TO_FIELD: dict[str, str] = {
    "parameter": "variable",     # env var name (krkn-hub tabs)
    "argument": "name",           # CLI flag, kebab-case (krknctl tabs)
    "option": "name",             # also CLI flag style
    "description": "description",
    "type": "type",
    "default": "default",
    "default value": "default",
    "required": "required",
}

# Match the AUTO:START / AUTO:END region. Both markers may have whitespace
# variations, but the marker_id must match exactly.
def _marker_re(marker_id: str) -> re.Pattern:
    return re.compile(
        rf'(<!-- AUTO:START id="{re.escape(marker_id)}" -->)(.*?)(<!-- AUTO:END -->)',
        re.DOTALL,
    )

# Match a markdown table SEPARATOR row (`| --- | --- | ... |` with optional
# outer pipes). Used to find the boundary between header and data rows.
_SEPARATOR_RE = re.compile(
    r"""
    ^\s*\|?\s*
    :?-+:?\s*
    (?:\|\s*:?-+:?\s*)+
    \|?\s*$
    """,
    re.VERBOSE,
)


def map_field_to_column(column_header: str) -> str | None:
    """Given an existing column header, return the Parameter attribute to
    use as the cell value (or None if we don't track this column)."""
    return _COLUMN_TO_FIELD.get(column_header.strip().lower())


def _split_table_row(line: str) -> list[str]:
    """Split a table row on `|` (respecting `\\|` escapes), strip outer pipes."""
    sentinel = "\x00ESC_PIPE\x00"
    safe = line.replace("\\|", sentinel)
    cells = [c.strip().replace(sentinel, "\\|") for c in safe.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def parse_existing_table_columns(text: str) -> list[str] | None:
    """Find the table inside the AUTO marker region and return its column
    headers as a list. Returns None if no table or no markers."""
    # Find any AUTO marker block — we don't yet care which id
    for line_block in re.findall(
        r'<!-- AUTO:START id="[^"]*" -->(.*?)<!-- AUTO:END -->',
        text, re.DOTALL,
    ):
        lines = line_block.splitlines()
        for i, line in enumerate(lines):
            if "|" not in line:
                continue
            # Header row is followed immediately by the separator
            if i + 1 < len(lines) and _SEPARATOR_RE.match(lines[i + 1]):
                return _split_table_row(line)
    return None


def _escape_pipe(s: str) -> str:
    """Escape `|` as `\\|` so multi-line cell content doesn't break the table."""
    return str(s).replace("|", "\\|")


def _value_for_column(param: Parameter, column_header: str) -> str:
    """Return the cell value for `param` in the column named `column_header`.

    Empty string for columns we don't track (caller decides what to do —
    we render an empty cell to preserve column count).
    """
    attr = map_field_to_column(column_header)
    if attr is None:
        return ""
    val = getattr(param, attr)
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def render_table_rows(
    params: list[Parameter],
    columns: list[str],
    has_outer_pipes: bool = True,
) -> list[str]:
    """Render data rows (NOT header or separator) for the given column layout.

    Always emits exactly `len(columns)` cells per row, even when some are
    empty — earned from inspection R1: stripping outer pipes via string
    rstrip lost trailing empty cells, breaking column alignment.

    `has_outer_pipes=True` produces `| a | b | c |`; False produces `a | b | c`.
    """
    rows = []
    for p in params:
        cells = [_escape_pipe(_value_for_column(p, c)) for c in columns]
        # Build a guaranteed N-cell row using explicit cell-by-cell joining
        if has_outer_pipes:
            row = "| " + " | ".join(cells) + " |"
        else:
            row = " | ".join(cells)
        rows.append(row)
    return rows


def _detect_outer_pipe_style(header_line: str) -> bool:
    """True if the header line starts with `|`, False otherwise.

    We use this to mirror the original table's pipe convention when
    rendering replacement rows.
    """
    return header_line.lstrip().startswith("|")


def regenerate_table(
    text: str,
    params: list[Parameter],
    marker_id: str = "params",
) -> str:
    """Rewrite the parameter table inside `<!-- AUTO:START id="..." -->`
    with rows derived from `params`, preserving the original column layout.

    Returns the original text unchanged if:
      - markers don't exist
      - no table is found inside the markers
    """
    pattern = _marker_re(marker_id)
    match = pattern.search(text)
    if not match:
        return text

    region = match.group(2)

    # Capture the EXACT leading/trailing newline run so re-running on the
    # output is bit-identical. Then operate on the trimmed body only.
    leading_nl = len(region) - len(region.lstrip("\n"))
    trailing_nl = len(region) - len(region.rstrip("\n"))
    body = region.strip("\n")
    body_lines = body.split("\n") if body else []

    # Find the header row (first row that has a separator on the next line)
    header_idx = None
    for i, line in enumerate(body_lines):
        if "|" not in line:
            continue
        if i + 1 < len(body_lines) and _SEPARATOR_RE.match(body_lines[i + 1]):
            header_idx = i
            break

    if header_idx is None:
        return text  # no table inside markers

    header_line = body_lines[header_idx]
    separator_line = body_lines[header_idx + 1]
    columns = _split_table_row(header_line)
    has_outer_pipes = _detect_outer_pipe_style(header_line)

    # Render new data rows, matching the outer-pipe style of the header.
    new_rows = render_table_rows(params, columns, has_outer_pipes=has_outer_pipes)

    pre_table = body_lines[:header_idx]

    # Find where the original data rows end: first non-`|` line after separator
    data_end = header_idx + 2
    while data_end < len(body_lines) and "|" in body_lines[data_end]:
        data_end += 1
    post_table = body_lines[data_end:]

    new_body_lines = (
        pre_table + [header_line, separator_line] + new_rows + post_table
    )
    new_region = (
        ("\n" * leading_nl)
        + "\n".join(new_body_lines)
        + ("\n" * trailing_nl)
    )

    return text[: match.start(2)] + new_region + text[match.end(2):]
