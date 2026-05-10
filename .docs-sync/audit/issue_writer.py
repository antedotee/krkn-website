"""Format audit findings into a GitHub issue body and upsert via `gh`.

Idempotency contract:
  - First run with findings → opens a new issue with `docs-sync-audit` label
  - Subsequent runs with findings → EDITS the same issue (no duplicates)
  - First run with no findings AND no open issue → no-op
  - First run with no findings AND an open issue → closes it

The `<!-- docs-sync:audit -->` marker in the body is the stable identifier.
Even if a maintainer renames the issue, we find it by the label + marker.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from audit import Finding
from security_utils import run_command_safe


AUDIT_ISSUE_LABEL = "docs-sync-audit"
_BODY_MARKER = "<!-- docs-sync:audit -->"

_CATEGORY_ORDER = [
    ("coverage_gap", "Coverage gaps", "scenario_types missing a doc page"),
    ("deprecation", "Deprecation references", "doc pages mentioning removed upstream entities"),
    ("broken_link", "Broken links", "links returning 4xx/5xx/timeout"),
]


def format_issue_body(findings: Iterable[Finding]) -> str:
    """Render findings into a markdown body. Stable marker first, then
    grouped sections (only for categories that have findings)."""
    findings = list(findings)
    parts = [_BODY_MARKER, ""]

    if not findings:
        parts.extend([
            "# docs-sync weekly audit",
            "",
            "**No findings this cycle.**",
            "",
            "Every scenario_type has a doc, no deprecated upstream references "
            "are present in the corpus, and every link in the rendered site "
            "resolves. Audit will run again next week.",
            "",
        ])
        return "\n".join(parts)

    parts.extend(["# docs-sync weekly audit", "", ""])
    parts.append(f"Found **{len(findings)}** issue(s) across the corpus.")
    parts.append("")

    for category, heading, blurb in _CATEGORY_ORDER:
        in_cat = [f for f in findings if f.category == category]
        if not in_cat:
            continue
        parts.append(f"## {heading} ({len(in_cat)})")
        parts.append("")
        parts.append(f"_{blurb}._")
        parts.append("")
        for f in in_cat:
            parts.append(f"### {f.title}")
            parts.append("")
            parts.append(f.detail)
            parts.append("")

    return "\n".join(parts)


def find_existing_audit_issue(repo: str) -> int | None:
    """Return the issue number of the open audit issue, or None."""
    result = run_command_safe(
        [
            "gh", "issue", "list",
            "--repo", repo,
            "--state", "open",
            "--label", AUDIT_ISSUE_LABEL,
            "--limit", "10",
            "--json", "number,title,state,labels",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (row.get("state") or "").upper() != "OPEN":
            continue
        labels = row.get("labels") or []
        names = {
            (lbl.get("name") or "") for lbl in labels if isinstance(lbl, dict)
        }
        if AUDIT_ISSUE_LABEL in names:
            try:
                return int(row.get("number"))
            except (TypeError, ValueError):
                continue
    return None


def _gh_create_issue(repo: str, title: str, body: str) -> int | None:
    """Create a fresh audit issue; return its number from the URL `gh` prints."""
    result = run_command_safe(
        [
            "gh", "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body", body,
            "--label", AUDIT_ISSUE_LABEL,
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    # gh prints e.g. "https://github.com/o/r/issues/100"
    match = re.search(r"/issues/(\d+)", result.stdout or "")
    if not match:
        return None
    return int(match.group(1))


def _gh_edit_issue(repo: str, number: int, title: str, body: str) -> None:
    run_command_safe(
        [
            "gh", "issue", "edit", str(number),
            "--repo", repo,
            "--title", title,
            "--body", body,
        ],
        check=False,
    )


def _gh_close_issue(repo: str, number: int, body: str) -> None:
    """Close the audit issue with a closing comment containing the final body."""
    run_command_safe(
        [
            "gh", "issue", "close", str(number),
            "--repo", repo,
            "--comment", body,
        ],
        check=False,
    )


def upsert_audit_issue(repo: str, findings: list[Finding]) -> int | None:
    """Idempotently reconcile the audit issue with the current findings.

    Returns the issue number after the operation (None when no issue
    existed and findings was empty).
    """
    body = format_issue_body(findings)
    title = (
        "docs-sync weekly audit — clean"
        if not findings
        else f"docs-sync weekly audit — {len(findings)} finding(s)"
    )

    existing = find_existing_audit_issue(repo)

    if not findings:
        if existing is None:
            return None
        _gh_close_issue(repo, existing, body)
        return existing

    if existing is None:
        return _gh_create_issue(repo, title, body)

    _gh_edit_issue(repo, existing, title, body)
    return existing
