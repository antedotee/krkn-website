"""STATE.md — per-run progress hand-off file.

Serves three purposes:
  1. Observability — a human can read STATE.md on the PR branch and see
     exactly what the bot did, in what order, with what token usage.
  2. Resumability — if a future run sees an existing STATE.md, it can
     skip already-completed work rather than redoing everything. (V1
     only writes STATE.md; full resume logic lands when first needed.)
  3. Audit trail — committed alongside the PR diff as evidence of what
     the deterministic pipeline did vs. what the LLM did.

Format: human-readable markdown with a single JSON-in-fenced-code-block
section that programs can round-trip parse without losing fidelity.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


# Status values for a scenario in the run plan
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE_REGEN = "done_regen"          # mechanical regen succeeded
STATUS_DONE_DRAFT = "done_draft"          # LLM draft accepted + written
STATUS_FAILED_DRAFT = "failed_draft"      # LLM draft rejected after retries
STATUS_FAILED_HUGO = "failed_hugo"        # Hugo build broke after this scenario


@dataclass
class ScenarioProgress:
    name: str                                  # e.g. "pod-scenarios"
    change_type: str                           # "added" | "modified" | "removed"
    status: str = STATUS_PENDING
    target_files: list[str] = field(default_factory=list)
    notes: str = ""                            # one-liner per scenario


@dataclass
class StateMd:
    upstream_repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    started_at: str
    updated_at: str
    scenarios: list[ScenarioProgress] = field(default_factory=list)
    token_usage_total: int = 0                 # sum of LLM call output tokens
    completed: bool = False
    notes: str = ""                            # for free-form summary at end


_FENCE_RE = re.compile(
    r"```json\n(.*?)\n```",
    re.DOTALL,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state(
    upstream_repo: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> StateMd:
    """Initialize a fresh state for a new run."""
    now = _now_iso()
    return StateMd(
        upstream_repo=upstream_repo,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        started_at=now,
        updated_at=now,
    )


def to_markdown(state: StateMd) -> str:
    """Serialize StateMd to the human-readable + machine-parseable format."""
    state.updated_at = _now_iso()

    # Status counts for the summary table
    counts: dict[str, int] = {}
    for s in state.scenarios:
        counts[s.status] = counts.get(s.status, 0) + 1

    parts = [
        f"# docs-sync STATE",
        "",
        f"> **Source:** [{state.upstream_repo}#{state.pr_number}]"
        f"(https://github.com/{state.upstream_repo}/pull/{state.pr_number})",
        f"> **Head:** `{state.head_sha[:12]}` → **Base:** `{state.base_sha[:12]}`",
        f"> **Started:** {state.started_at} · **Updated:** {state.updated_at}",
        "",
        "## Progress",
        "",
        "| Status | Count |",
        "| --- | --- |",
    ]
    for status in (
        STATUS_PENDING, STATUS_IN_PROGRESS,
        STATUS_DONE_REGEN, STATUS_DONE_DRAFT,
        STATUS_FAILED_DRAFT, STATUS_FAILED_HUGO,
    ):
        if counts.get(status, 0):
            parts.append(f"| `{status}` | {counts[status]} |")
    parts.extend(["", "## Scenarios", ""])

    for s in state.scenarios:
        target = ", ".join(s.target_files) if s.target_files else "(none)"
        parts.append(
            f"- **{s.name}** ({s.change_type}) — {s.status}; "
            f"targets: {target}"
            + (f"; {s.notes}" if s.notes else "")
        )

    if state.notes:
        parts.extend(["", "## Notes", "", state.notes])

    parts.extend([
        "",
        f"**Total LLM output tokens:** {state.token_usage_total}",
        f"**Run complete:** {'yes' if state.completed else 'no'}",
        "",
        "<!-- BEGIN MACHINE STATE — do not edit by hand -->",
        "```json",
        json.dumps(asdict(state), indent=2, sort_keys=True),
        "```",
        "<!-- END MACHINE STATE -->",
        "",
    ])
    return "\n".join(parts)


def from_markdown(text: str) -> StateMd | None:
    """Parse a STATE.md back into a StateMd. None if the JSON block is
    missing or malformed — callers fall back to creating a fresh state.
    """
    if not text:
        return None
    match = _FENCE_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    try:
        scenarios = [
            ScenarioProgress(**s) for s in data.get("scenarios", [])
            if isinstance(s, dict)
        ]
        return StateMd(
            upstream_repo=data.get("upstream_repo", ""),
            pr_number=int(data.get("pr_number", 0)),
            head_sha=data.get("head_sha", ""),
            base_sha=data.get("base_sha", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            scenarios=scenarios,
            token_usage_total=int(data.get("token_usage_total", 0)),
            completed=bool(data.get("completed", False)),
            notes=data.get("notes", ""),
        )
    except (TypeError, ValueError):
        return None


def save(state: StateMd, path: Path) -> None:
    """Write STATE.md to disk. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(state), encoding="utf-8")


def load(path: Path) -> StateMd | None:
    """Read STATE.md from disk. None if file missing or unparseable."""
    if not path.is_file():
        return None
    return from_markdown(path.read_text(encoding="utf-8"))


def mark_scenario(
    state: StateMd,
    name: str,
    *,
    status: str | None = None,
    target_files: list[str] | None = None,
    notes: str | None = None,
) -> None:
    """Update an existing scenario's progress entry IN PLACE."""
    for s in state.scenarios:
        if s.name == name:
            if status is not None:
                s.status = status
            if target_files is not None:
                s.target_files = target_files
            if notes is not None:
                s.notes = notes
            state.updated_at = _now_iso()
            return
    raise KeyError(f"scenario {name!r} not in state — call add_scenario first")


def add_scenario(
    state: StateMd,
    name: str,
    change_type: str,
) -> None:
    """Append a new scenario entry (status=pending)."""
    if any(s.name == name for s in state.scenarios):
        return  # idempotent
    state.scenarios.append(ScenarioProgress(
        name=name,
        change_type=change_type,
    ))
    state.updated_at = _now_iso()


def is_scenario_done(state: StateMd, name: str) -> bool:
    """True if the scenario already completed (regen OR draft)."""
    for s in state.scenarios:
        if s.name == name and s.status in (STATUS_DONE_REGEN, STATUS_DONE_DRAFT):
            return True
    return False


def pending_scenarios(state: StateMd) -> list[ScenarioProgress]:
    """All scenarios that haven't completed yet (for resume logic)."""
    return [s for s in state.scenarios if s.status not in (
        STATUS_DONE_REGEN, STATUS_DONE_DRAFT,
    )]
