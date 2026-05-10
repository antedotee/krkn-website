"""Format the consolidator's output into the human-reviewable
HARVEST_PROPOSAL.md file that gets committed to the harvest PR branch.

We deliberately do NOT auto-edit AGENTS.md or repo-map.yaml — those are
hand-curated artifacts with intentional section structure, and a bad LLM
suggestion folded in automatically would corrupt the audit trail. Instead
the cron commits a proposal file that the maintainer reviews; approved
additions get manually copied into the canonical files on merge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reflection.consolidator import ConsolidatorOutput, ProposedAddition


def _format_addition(addition: ProposedAddition) -> str:
    cites = ", ".join(f"[{p}](https://github.com/{p.replace('#', '/pull/')})"
                      for p in addition.source_prs)
    return (
        f"- **{addition.text}**\n"
        f"  - _Rationale:_ {addition.rationale}\n"
        f"  - _Cited in:_ {cites}"
    )


def format_proposal_md(output: ConsolidatorOutput, now: str | None = None) -> str:
    """Render a ConsolidatorOutput as the proposal markdown."""
    timestamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = [
        "# docs-sync HARVEST PROPOSAL",
        "",
        f"> Generated {timestamp} from the last week of docs-sync PR reflections.",
        "> A maintainer reviews each proposal below. Approved entries get manually",
        "> folded into AGENTS.md / repo-map.yaml in a follow-up commit on this PR.",
        "",
    ]

    if not output.agents_rule_additions and not output.skip_pattern_additions:
        parts.extend([
            "_No additions proposed this cycle._",
            "",
            "Either the week was quiet, every run looked the same, or the consolidator",
            "couldn't extract suggestions strong enough to promote. Nothing to do.",
            "",
        ])
        return "\n".join(parts)

    if output.agents_rule_additions:
        parts.extend([
            "## Proposed additions to `AGENTS.md`",
            "",
            "_These rules would extend the bot's persistent ruleset. Move approved",
            "entries into the appropriate `## Earned from ...` section in AGENTS.md._",
            "",
        ])
        for a in output.agents_rule_additions:
            parts.append(_format_addition(a))
            parts.append("")

    if output.skip_pattern_additions:
        parts.extend([
            "## Proposed additions to `repo-map.yaml`",
            "",
            "_These glob patterns would join the per-repo skip list, tightening Stage A._",
            "",
        ])
        for a in output.skip_pattern_additions:
            parts.append(_format_addition(a))
            parts.append("")

    return "\n".join(parts)


def write_proposal(output: ConsolidatorOutput, path: Path) -> None:
    """Write HARVEST_PROPOSAL.md to disk. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_proposal_md(output), encoding="utf-8")
