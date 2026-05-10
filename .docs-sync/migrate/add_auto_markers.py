"""One-time migration: wrap parameter tables in scenario tab files with
`<!-- AUTO:START id="params" -->` ... `<!-- AUTO:END -->` markers.

Slice 1's mechanical regen replaces content BETWEEN markers. Without this
migration, Slice 1 would have nowhere to put its output. Run once to
prepare the corpus, then commit.

Real corpus has 6 distinct parameter-table header shapes (see
slice-0a-inspection.md). Detection by first-column header keyword
(parameter / option / argument), case-insensitive. Other tables
(e.g. `Component | Description | Working` scenarios catalogs) are skipped.

The migration is idempotent — re-running on already-wrapped tables is a no-op.

Run as a CLI from the website repo root:
    python .docs-sync/migrate/add_auto_markers.py
"""
import argparse
import re
import sys
from pathlib import Path

# Match a table header row whose first cell is Parameter/Option/Argument.
# Allows variable whitespace, optional leading/trailing pipes.
# Case-insensitive — corpus has both `| Parameter |` and `|parameter|`.
_PARAM_HEADER_RE = re.compile(
    r"""
    ^\s*\|?\s*                # optional leading pipe + spaces
    (parameter|option|argument)  # the keyword
    \s*\|                     # at least one column separator follows
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match a markdown table separator row: `| --- | --- | ... |`
# Each cell is dashes (with optional alignment colons), separated by pipes.
# Outer pipes are optional — the krkn corpus often omits them.
_SEPARATOR_RE = re.compile(
    r"""
    ^\s*\|?\s*                # optional leading pipe
    :?-+:?\s*                 # first separator cell
    (?:\|\s*:?-+:?\s*)+       # one or more more cells
    \|?\s*$                   # optional trailing pipe
    """,
    re.VERBOSE,
)

_MARKER_START = '<!-- AUTO:START id="{marker_id}" -->'
_MARKER_END = "<!-- AUTO:END -->"


def is_parameter_table_header(line: str) -> bool:
    """Return True if `line` is a markdown table header row whose first cell
    is Parameter/Option/Argument."""
    return bool(_PARAM_HEADER_RE.match(line))


def _is_separator(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line))


def _is_continuation_row(line: str) -> bool:
    """A row that continues a table: non-blank and contains at least one pipe.

    We do NOT require leading or trailing pipes because the krkn-chaos corpus
    often writes tables in pipeless form:
        Parameter | Description | Type | Default
        --------- | ----------- | ---- | ------- |
        NAMESPACE | the ns      | str  | default
    Earned from Slice 0.5 inspection finding M1.
    """
    if not line.strip():
        return False
    return "|" in line


def find_parameter_tables(text: str) -> list[tuple[int, int]]:
    """Find (start_line, end_line) inclusive ranges for each parameter table.

    A parameter table is:
      - A header row matching `is_parameter_table_header`
      - Immediately followed by a markdown separator row (`| --- | ... |`)
      - Then zero or more data rows

    Returns: list of (start, end) line index pairs (0-indexed, end inclusive).
    Tables without a separator row are NOT considered valid markdown tables
    and are skipped.
    """
    lines = text.splitlines()
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if is_parameter_table_header(lines[i]):
            # Need a separator on the next line, else this isn't a real table.
            if i + 1 < len(lines) and _is_separator(lines[i + 1]):
                start = i
                end = i + 1
                # Walk forward through data rows. Stop at first blank line
                # or first line without a pipe (which can't be a table row).
                j = i + 2
                while j < len(lines) and _is_continuation_row(lines[j]):
                    end = j
                    j += 1
                ranges.append((start, end))
                i = j
                continue
        i += 1
    return ranges


def _is_already_wrapped(text: str, table_start: int, table_end: int) -> bool:
    """True if the previous non-blank line before table_start is an AUTO:START
    marker (and there's a corresponding AUTO:END after table_end).

    Idempotency check — don't double-wrap.
    """
    lines = text.splitlines()
    # Look backward for AUTO:START on the immediately preceding non-blank line
    k = table_start - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k < 0:
        return False
    if "AUTO:START" not in lines[k]:
        return False
    # Look forward for AUTO:END after the table
    k = table_end + 1
    while k < len(lines) and not lines[k].strip():
        k += 1
    if k >= len(lines):
        return False
    return "AUTO:END" in lines[k]


def wrap_table_with_markers(text: str, marker_id: str = "params") -> str:
    """Insert AUTO:START / AUTO:END markers around each parameter table.

    Idempotent: tables already wrapped are left untouched.
    Other tables (non-parameter) are not wrapped.

    Trailing-newline preservation: `splitlines()` collapses any trailing
    blank-line sequence to a single trailing newline on join. We count the
    exact trailing newline run on input and preserve it on output. Earned
    from Slice 0.5 inspection finding M2: re-running the migration was not
    a no-op because each pass dropped one trailing `\\n` per file.
    """
    ranges = find_parameter_tables(text)
    if not ranges:
        return text

    # Short-circuit: if every found table is already wrapped, return the
    # original text byte-for-byte unchanged. Avoids splitlines round-trip.
    if all(_is_already_wrapped(text, s, e) for s, e in ranges):
        return text

    # Capture original trailing-newline run so we can restore it later.
    trailing_newlines = len(text) - len(text.rstrip("\n"))

    lines = text.splitlines()
    # Process from bottom up so earlier indices remain valid as we insert.
    for start, end in reversed(ranges):
        if _is_already_wrapped(text, start, end):
            continue
        lines.insert(end + 1, _MARKER_END)
        lines.insert(start, _MARKER_START.format(marker_id=marker_id))

    return "\n".join(lines).rstrip("\n") + "\n" * trailing_newlines


def process_file(path: Path, marker_id: str = "params") -> bool:
    """Wrap parameter tables in `path` with markers if not already wrapped.

    Returns True if the file was modified, False otherwise.
    """
    original = path.read_text(encoding="utf-8")
    updated = wrap_table_with_markers(original, marker_id=marker_id)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--content-root",
        type=Path,
        default=Path("content/en/docs"),
        help="Root of the docs content tree (default: content/en/docs)",
    )
    parser.add_argument(
        "--glob",
        default="scenarios/**/_tab-*.md",
        help="Glob (relative to content-root) of files to process. "
             "Default: scenarios/**/_tab-*.md",
    )
    parser.add_argument(
        "--marker-id",
        default="params",
        help="ID written into AUTO:START marker (default: params)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args(argv)

    if not args.content_root.exists():
        print(f"error: content root not found: {args.content_root}", file=sys.stderr)
        return 2

    targets = sorted(args.content_root.glob(args.glob))
    if not targets:
        print(f"warning: no files matched glob {args.glob} under {args.content_root}", file=sys.stderr)
        return 0

    changed_count = 0
    skipped_count = 0
    for path in targets:
        if args.dry_run:
            original = path.read_text(encoding="utf-8")
            updated = wrap_table_with_markers(original, marker_id=args.marker_id)
            if updated != original:
                changed_count += 1
                print(f"would update: {path}")
            else:
                skipped_count += 1
        else:
            if process_file(path, marker_id=args.marker_id):
                changed_count += 1
                print(f"updated: {path}")
            else:
                skipped_count += 1

    print(
        f"\nProcessed {len(targets)} files: "
        f"{changed_count} {'would be modified' if args.dry_run else 'modified'}, "
        f"{skipped_count} unchanged."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
