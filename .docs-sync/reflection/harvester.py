"""Harvest REFLECTION.md files from recent docs-sync PRs on the website repo.

Used by the nightly cron (Slice 3 self-improvement). The harvester is the
deterministic "input layer":

  GitHub PRs  →  this module  →  consolidator (LLM)  →  output PR

We do plumbing only — no LLM calls here. The consolidator owns the
"distill N reflections into M proposals" judgment. Keeping that boundary
clean means this module is unit-testable without an API key.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reflection.writer import Reflection, from_markdown
from security_utils import run_command_safe


@dataclass
class PrSummary:
    """Minimum metadata needed to fetch a PR's REFLECTION.md from disk."""
    number: int
    state: str          # "MERGED" | "CLOSED"
    head_ref: str
    closed_at: str
    url: str


# `gh pr list` JSON fields we ask for. Adding more requires a separate
# call (gh issues one request per fields set) — keep it minimal.
_PR_LIST_FIELDS = "number,state,headRefName,closedAt,url"


def find_recent_docs_sync_prs(
    repo: str,
    days: int = 7,
    *,
    label: str = "docs-sync",
) -> list[PrSummary]:
    """List docs-sync PRs on `repo` closed within the last `days`.

    Includes both MERGED and CLOSED-without-merge. Excludes OPEN — those
    have no final outcome yet, so their REFLECTION.md isn't authoritative.
    """
    result = run_command_safe(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--state", "all",
            "--label", label,
            "--limit", "100",
            "--json", _PR_LIST_FIELDS,
        ],
        check=False,
    )
    if result.returncode != 0:
        return []

    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[PrSummary] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = (row.get("state") or "").upper()
        if state not in {"MERGED", "CLOSED"}:
            continue
        closed_at = row.get("closedAt") or ""
        if not closed_at:
            continue
        try:
            # GitHub returns ISO-8601 with trailing 'Z' on some clients
            ts = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        out.append(PrSummary(
            number=int(row.get("number", 0)),
            state=state,
            head_ref=row.get("headRefName", ""),
            closed_at=closed_at,
            url=row.get("url", ""),
        ))
    return out


def fetch_reflection(repo: str, head_ref: str) -> Reflection | None:
    """Read `.docs-sync/REFLECTION.md` from a branch of `repo` and parse it.

    Returns None if the file doesn't exist (silent — common for older
    runs predating REFLECTION.md) or if the content is unparseable.
    """
    result = run_command_safe(
        [
            "gh", "api",
            f"repos/{repo}/contents/.docs-sync/REFLECTION.md?ref={head_ref}",
            "--jq", ".content",
        ],
        check=False,
    )
    if result.returncode != 0:
        # 404 (missing REFLECTION.md) is normal — silently skip.
        return None
    encoded = (result.stdout or "").strip()
    if not encoded:
        return None
    try:
        text = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return from_markdown(text)


def harvest(repo: str, days: int = 7) -> list[Reflection]:
    """Collect parsed REFLECTION.md files from recent docs-sync PRs.

    Skips PRs whose branch has no REFLECTION.md or whose REFLECTION.md
    failed to parse — those don't contribute to the consolidator's input.
    """
    prs = find_recent_docs_sync_prs(repo, days=days)
    reflections: list[Reflection] = []
    for pr in prs:
        r = fetch_reflection(repo, pr.head_ref)
        if r is None:
            continue
        # The PR number on the source-of-truth is whatever REFLECTION.md
        # recorded (upstream PR), but for harvester traceability we
        # overwrite with the website PR number — that's what the
        # consolidator needs to cite when proposing additions.
        r.pr_number = pr.number
        reflections.append(r)
    return reflections
