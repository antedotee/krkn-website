"""Coverage gap audit — flags scenario_types in the upstream taxonomy
that have no corresponding doc directory on the website.

Input: `.docs-sync-digest/COVERAGE.json` produced by extract_coverage.
That file already does the heavy lifting (token-Jaccard match between
scenario_type names and directory names); we just translate the
"unmatched scenario_types" section into Finding objects.

The doc-without-scenario direction lives in `deprecation_check`.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit import Finding


def find_coverage_gaps(coverage_path: Path) -> list[Finding]:
    """Return a Finding per scenario_type that has no doc directory."""
    if not coverage_path.is_file():
        return []
    try:
        data = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []

    raw = data.get("scenario_types_without_directory", []) or []
    findings: list[Finding] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        scenario_type = str(entry.get("scenario_type", "")).strip()
        if not scenario_type:
            continue
        best_candidate = entry.get("best_candidate")
        best_score = entry.get("best_score", 0.0)

        if best_candidate:
            detail = (
                f"Upstream scenario_type `{scenario_type}` has no doc directory. "
                f"Closest existing directory is `{best_candidate}` "
                f"(similarity {best_score:.2f}, below threshold). Either:\n"
                f"  - Add the missing doc page, or\n"
                f"  - Rename `{best_candidate}` so it matches the new naming convention, or\n"
                f"  - Add this scenario_type to the known-orphan allowlist if intentional."
            )
        else:
            detail = (
                f"Upstream scenario_type `{scenario_type}` has no doc directory "
                f"and no close-name candidate. A new doc page needs writing — "
                f"the scenario is currently invisible to users."
            )

        findings.append(Finding(
            category="coverage_gap",
            title=f"Missing doc directory for upstream scenario_type `{scenario_type}`",
            detail=detail,
            source=scenario_type,
        ))
    return findings
