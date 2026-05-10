"""REFLECTION.md — per-run learning record.

Every docs-sync orchestrator run writes one of these to its PR branch.
The nightly harvester reads N reflections from recent merged + closed
docs-sync PRs and consolidates them into proposed AGENTS.md /
repo-map.yaml additions (Slice 3 self-improvement loop).

Format mirrors STATE.md: human-readable markdown with one JSON block
that programs can round-trip parse losslessly.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# Outcomes are the harvester's primary classification axis. Stable strings.
OUTCOME_PASS = "pass"
OUTCOME_SKIPPED = "skipped"
OUTCOME_REJECTED = "rejected"          # LLM draft failed validation
OUTCOME_HUGO_FAILED = "hugo_failed"    # mechanical regen broke Hugo

_VALID_OUTCOMES = {
    OUTCOME_PASS, OUTCOME_SKIPPED, OUTCOME_REJECTED, OUTCOME_HUGO_FAILED,
}

# Suggestion kinds — extend by adding a constant and the consolidator
# learns to consume it. Unknown kinds in stored reflections are dropped
# on parse (forward-compatibility).
SUGGESTION_AGENTS_RULE = "agents_rule"     # → propose addition to AGENTS.md
SUGGESTION_SKIP_PATTERN = "skip_pattern"   # → propose addition to repo-map.yaml

_VALID_SUGGESTION_KINDS = {
    SUGGESTION_AGENTS_RULE, SUGGESTION_SKIP_PATTERN,
}


@dataclass
class Suggestion:
    kind: str
    text: str
    source_pr: str          # e.g. "antedotee/krkn-hub#42"


@dataclass
class Reflection:
    upstream_repo: str
    pr_number: int
    head_sha: str
    outcome: str
    run_at: str
    scenarios_processed: list[str] = field(default_factory=list)
    token_usage_total: int = 0
    token_usage_by_model: dict[str, int] = field(default_factory=dict)
    retries: int = 0
    surprises: list[str] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)


_FENCE_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_reflection(
    upstream_repo: str,
    pr_number: int,
    head_sha: str,
    outcome: str,
) -> Reflection:
    """Initialize a fresh reflection."""
    return Reflection(
        upstream_repo=upstream_repo,
        pr_number=pr_number,
        head_sha=head_sha,
        outcome=outcome,
        run_at=_now_iso(),
    )


def to_markdown(reflection: Reflection) -> str:
    """Serialize Reflection to the human + machine format."""
    parts = [
        "# docs-sync REFLECTION",
        "",
        f"> **Source:** [{reflection.upstream_repo}#{reflection.pr_number}]"
        f"(https://github.com/{reflection.upstream_repo}/pull/{reflection.pr_number})",
        f"> **Head:** `{reflection.head_sha[:12]}`",
        f"> **Outcome:** `{reflection.outcome}` · **Run at:** {reflection.run_at}",
        "",
    ]

    if reflection.scenarios_processed:
        parts.extend([
            "## Scenarios processed",
            "",
            *[f"- `{s}`" for s in reflection.scenarios_processed],
            "",
        ])

    parts.extend([
        "## Token usage",
        "",
        "| Model | Output tokens |",
        "| --- | ---: |",
    ])
    for model, tokens in sorted(reflection.token_usage_by_model.items()):
        parts.append(f"| `{model}` | {tokens} |")
    parts.append(f"| **Total** | **{reflection.token_usage_total}** |")
    parts.extend(["", f"**Retries:** {reflection.retries}", ""])

    parts.extend(["## Surprises", ""])
    if reflection.surprises:
        for s in reflection.surprises:
            parts.append(f"- {s}")
    else:
        parts.append("_None._")
    parts.append("")

    parts.extend(["## Suggestions", ""])
    if reflection.suggestions:
        for s in reflection.suggestions:
            parts.append(f"- **{s.kind}** ({s.source_pr}): {s.text}")
    else:
        parts.append("_None._")
    parts.append("")

    parts.extend([
        "<!-- BEGIN MACHINE REFLECTION — do not edit by hand -->",
        "```json",
        json.dumps(asdict(reflection), indent=2, sort_keys=True),
        "```",
        "<!-- END MACHINE REFLECTION -->",
        "",
    ])
    return "\n".join(parts)


def from_markdown(text: str) -> Reflection | None:
    """Parse a REFLECTION.md back. None if missing/malformed/unknown outcome."""
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

    outcome = data.get("outcome", "")
    if outcome not in _VALID_OUTCOMES:
        return None

    try:
        # Drop suggestions with unknown kinds — forward compatibility.
        raw_sugs = data.get("suggestions", []) or []
        suggestions = [
            Suggestion(kind=s["kind"], text=s.get("text", ""),
                       source_pr=s.get("source_pr", ""))
            for s in raw_sugs
            if isinstance(s, dict) and s.get("kind") in _VALID_SUGGESTION_KINDS
        ]
        return Reflection(
            upstream_repo=data.get("upstream_repo", ""),
            pr_number=int(data.get("pr_number", 0)),
            head_sha=data.get("head_sha", ""),
            outcome=outcome,
            run_at=data.get("run_at", ""),
            scenarios_processed=list(data.get("scenarios_processed", []) or []),
            token_usage_total=int(data.get("token_usage_total", 0)),
            token_usage_by_model=dict(data.get("token_usage_by_model", {}) or {}),
            retries=int(data.get("retries", 0)),
            surprises=list(data.get("surprises", []) or []),
            suggestions=suggestions,
        )
    except (TypeError, ValueError, KeyError):
        return None


def save(reflection: Reflection, path: Path) -> None:
    """Write REFLECTION.md to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(reflection), encoding="utf-8")


def load(path: Path) -> Reflection | None:
    """Read REFLECTION.md. None if missing/unparseable."""
    if not path.is_file():
        return None
    return from_markdown(path.read_text(encoding="utf-8"))
