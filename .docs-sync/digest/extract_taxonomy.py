"""Extract a structured taxonomy of every scenario type, CLI flag, and scenario
directory the docs reference. Outputs `.docs-sync-digest/TAXONOMY.json`.

This is the source of truth the eventual judge stage uses to detect LLM
hallucination — "did the agent invent a scenario_type that isn't real?"
Bias toward precision (false positives weaken the judge) over recall.

Pure deterministic Python — no LLM, bit-identical output for the same input.

Run as a CLI from the website repo root:
    python .docs-sync/digest/extract_taxonomy.py

Depends on `build_docs_digest.py` having run first (reads from PER_PAGE/).
"""
import argparse
import json
import re
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# scenario_directories — filesystem walk of content/en/docs/scenarios/
# ─────────────────────────────────────────────────────────────────────────────

def extract_scenario_directories(content_root: Path) -> list[str]:
    """Return all directory names under content/en/docs/scenarios/, recursively.

    Skips dirs starting with `_` (e.g., `_archived`) or `.` (hidden).
    Returns just the basenames, sorted.
    """
    scenarios_root = content_root / "scenarios"
    if not scenarios_root.is_dir():
        return []

    found: set[str] = set()
    for path in scenarios_root.rglob("*"):
        if not path.is_dir():
            continue
        # Skip if any path component starts with _ or .
        rel_parts = path.relative_to(scenarios_root).parts
        if any(p.startswith(("_", ".")) for p in rel_parts):
            continue
        found.add(path.name)
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# scenario_types — regex on PER_PAGE digest text
# ─────────────────────────────────────────────────────────────────────────────

# Match YAML list items like `  - pod_disruption_scenarios:` (with variable
# indentation and optional whitespace before colon). Bias toward precision:
# only accept identifiers ending in `_scenario` or `_scenarios` — that's the
# convention the krkn config uses.
_SCENARIO_TYPE_RE = re.compile(
    r"""
    ^\s*-\s*           # YAML list bullet, any indent
    ([a-z][a-z0-9_]*   # identifier — must start with lowercase letter
        _scenarios?    # ends in _scenario or _scenarios
    )
    \s*:               # YAML key colon, optional whitespace before
    """,
    re.MULTILINE | re.VERBOSE,
)


def extract_scenario_types(per_page_dir: Path) -> list[str]:
    """Return sorted unique scenario_type identifiers from all PER_PAGE files."""
    if not per_page_dir.is_dir():
        return []
    found: set[str] = set()
    for txt in per_page_dir.rglob("*.txt"):
        text = txt.read_text(encoding="utf-8", errors="replace")
        for match in _SCENARIO_TYPE_RE.finditer(text):
            found.add(match.group(1))
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# cli_flags — regex on PER_PAGE digest text
# ─────────────────────────────────────────────────────────────────────────────

# Match `--name` or `--multi-word-name` flags. Constraints:
#  - At least 2 chars after `--` (rules out `--x`, `--ab` is OK)
#  - Must end in alphanumeric — `--aws-` is never a real flag (T2)
#  - Must NOT be preceded by `var(` or `(` — kills CSS `var(--foo)` (T1)
#  - Must NOT be immediately followed by `:` — kills CSS declarations
#    `--foo: value;` and YAML keys `--foo:` (T1)
#  - Word-boundary at start — kills `path--with--dashes` URLs
# We deliberately do NOT match short flags like `-h` (V1 — too noisy in prose).
_CLI_FLAG_RE = re.compile(
    r"""
    (?<![A-Za-z0-9/(-])    # not preceded by alphanumeric, slash, hyphen, or `(`
                           # (kills `path--with--dashes`, `var(--foo)`, `(--foo`)
    (--[a-z][a-z0-9-]*[a-z0-9])  # body: 2+ chars, ends in alphanumeric (T2)
    (?![A-Za-z0-9/:])      # not followed by alphanumeric, slash, or `:`
                           # (kills `--fooBar`, `--foo: css-value;` (T1))
    """,
    re.VERBOSE,
)

# Reject obvious false positives. Most filtering is structural via the regex;
# this is for residual cases.
_FLAG_BLACKLIST = frozenset({
    "--",      # bare double-dash
})


def extract_cli_flags(per_page_dir: Path) -> list[str]:
    """Return sorted unique CLI flags (long form, e.g. --config) from PER_PAGE."""
    if not per_page_dir.is_dir():
        return []
    found: set[str] = set()
    for txt in per_page_dir.rglob("*.txt"):
        text = txt.read_text(encoding="utf-8", errors="replace")
        for match in _CLI_FLAG_RE.finditer(text):
            flag = match.group(1)
            if flag in _FLAG_BLACKLIST:
                continue
            # Body length check: 2+ chars after --
            if len(flag) < 4:  # `--` + 2 chars
                continue
            found.add(flag)
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# build_taxonomy — integration
# ─────────────────────────────────────────────────────────────────────────────

def build_taxonomy(content_root: Path, per_page_dir: Path) -> dict:
    """Build the full taxonomy dictionary for TAXONOMY.json output."""
    return {
        "scenario_directories": extract_scenario_directories(content_root),
        "scenario_types": extract_scenario_types(per_page_dir),
        "cli_flags": extract_cli_flags(per_page_dir),
        # Reserved for Slice 7 (krkn-operator) — not extracted in V1 here.
        "crd_names": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--content-root",
        type=Path,
        default=Path("content/en/docs"),
        help="Root of the docs content tree (default: content/en/docs)",
    )
    parser.add_argument(
        "--digest-dir",
        type=Path,
        default=Path(".docs-sync-digest"),
        help="Digest output dir, contains PER_PAGE/ (default: .docs-sync-digest)",
    )
    args = parser.parse_args(argv)

    per_page_dir = args.digest_dir / "PER_PAGE"
    if not per_page_dir.is_dir():
        print(
            f"error: PER_PAGE not found at {per_page_dir}. "
            "Run build_docs_digest.py first.",
            file=sys.stderr,
        )
        return 2

    taxonomy = build_taxonomy(args.content_root, per_page_dir)

    out_path = args.digest_dir / "TAXONOMY.json"
    out_path.write_text(
        json.dumps(taxonomy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote TAXONOMY.json: "
        f"{len(taxonomy['scenario_directories'])} scenario dirs, "
        f"{len(taxonomy['scenario_types'])} scenario types, "
        f"{len(taxonomy['cli_flags'])} CLI flags."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
