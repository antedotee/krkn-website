"""Tests for reflection/proposal.py — formats ConsolidatorOutput into the
human-reviewable HARVEST_PROPOSAL.md file.

This is the artifact the harvest cron commits to the proposal PR. A human
reviews it and (manually) folds approved entries into AGENTS.md or
repo-map.yaml — we don't auto-mutate those files because they're
hand-curated and a bad LLM suggestion would corrupt the audit trail.
"""
from datetime import datetime, timezone

from reflection.consolidator import ConsolidatorOutput, ProposedAddition
from reflection.proposal import (
    format_proposal_md,
    write_proposal,
)


def _output(agents=None, skips=None):
    return ConsolidatorOutput(
        agents_rule_additions=agents or [],
        skip_pattern_additions=skips or [],
    )


class TestFormatProposalMd:
    def test_empty_output_yields_empty_message(self):
        md = format_proposal_md(_output(), now="2026-05-11T00:00:00+00:00")
        # Must still be a valid file (header) but body explains no proposals.
        assert "# docs-sync HARVEST PROPOSAL" in md
        assert "No additions" in md or "no additions" in md.lower()

    def test_agents_rule_addition_includes_text_rationale_citations(self):
        out = _output(agents=[
            ProposedAddition(
                text="Always normalize CLI flags before regex match.",
                rationale="Multiple PRs hit false matches on CSS custom properties.",
                source_prs=["o/r#42", "o/r#45"],
            ),
        ])
        md = format_proposal_md(out, now="2026-05-11T00:00:00+00:00")
        # Rule text must be present, rationale must be present, both PRs cited
        assert "Always normalize CLI flags" in md
        assert "CSS custom properties" in md
        assert "o/r#42" in md
        assert "o/r#45" in md

    def test_skip_pattern_addition_shows_glob_and_rationale(self):
        out = _output(skips=[
            ProposedAddition(
                text="docs/internal/*",
                rationale="Repeatedly triggered Stage A on internal-only docs.",
                source_prs=["o/r#50"],
            ),
        ])
        md = format_proposal_md(out, now="2026-05-11T00:00:00+00:00")
        assert "docs/internal/*" in md
        assert "internal-only docs" in md
        assert "o/r#50" in md

    def test_both_sections_present_when_both_have_entries(self):
        out = _output(
            agents=[ProposedAddition("rule A", "why A", ["o/r#1"])],
            skips=[ProposedAddition("pat/*", "why B", ["o/r#2"])],
        )
        md = format_proposal_md(out, now="2026-05-11T00:00:00+00:00")
        # Both section headings
        assert "AGENTS.md" in md
        assert "repo-map.yaml" in md

    def test_timestamp_in_header(self):
        md = format_proposal_md(_output(), now="2026-05-11T12:34:56+00:00")
        assert "2026-05-11" in md


class TestWriteProposal:
    def test_writes_file_to_path(self, tmp_path):
        out = _output(agents=[ProposedAddition("rule", "why", ["o/r#1"])])
        path = tmp_path / "HARVEST_PROPOSAL.md"
        write_proposal(out, path)
        text = path.read_text(encoding="utf-8")
        assert "rule" in text
        assert "o/r#1" in text

    def test_creates_parent_dirs(self, tmp_path):
        out = _output()
        path = tmp_path / "deeply/nested/HARVEST_PROPOSAL.md"
        write_proposal(out, path)
        assert path.is_file()
