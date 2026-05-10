"""Broken link audit — parses lychee's `--format json` output into Finding
objects.

We don't shell out to lychee from this module — the audit workflow YAML
invokes [`lycheeverse/lychee-action`](https://github.com/lycheeverse/lychee-action)
directly and pipes its JSON report to `parse_lychee_output`. Keeps the
parser unit-testable without network or external binaries.
"""
from __future__ import annotations

import json

from audit import Finding


def parse_lychee_output(payload: str) -> list[Finding]:
    """Translate lychee's JSON report into Finding objects.

    Lychee's schema: `{"success": bool, "fail_map": {source_file: [{status, url}, ...]}}`
    """
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    fail_map = data.get("fail_map") or {}
    if not isinstance(fail_map, dict):
        return []

    findings: list[Finding] = []
    for source_file, failures in fail_map.items():
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            url = str(failure.get("url", "")).strip()
            status = str(failure.get("status", "")).strip()
            if not url:
                continue
            findings.append(Finding(
                category="broken_link",
                title=f"Broken link `{url}` ({status or 'unknown status'})",
                detail=(
                    f"Link `{url}` returned status `{status or 'unknown'}` "
                    f"when checked from `{source_file}`. Either update the "
                    f"link, archive the target via archive.org, or remove "
                    f"the reference if the resource is gone."
                ),
                source=url,
            ))
    return findings
