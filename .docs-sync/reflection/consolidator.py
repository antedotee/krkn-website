"""Distill a batch of REFLECTION.md objects into ranked, deduped proposals
for AGENTS.md and repo-map.yaml additions.

The ONLY LLM call in the harvest pipeline lives here. Everything else
(harvester, writer) is deterministic plumbing. Keep the boundary clean:
LLM judgment for "what's worth promoting from a week of runs", Python
code for everything else.

Plan locks (from tasks/plan.md and tasks/todo.md):
  - 1-3 AGENTS.md rule additions per run
  - 1-2 repo-map.yaml skip-pattern additions per run
  - Each proposal MUST cite the source PR(s) — full audit trail
  - Single Gemini Flash call (cheapest free-tier model)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agent.llm_client import LLMResponse, chat_completion
from reflection.writer import (
    OUTCOME_REJECTED,
    Reflection,
    SUGGESTION_AGENTS_RULE,
    SUGGESTION_SKIP_PATTERN,
)


# Plan-locked caps. The parser enforces them even if the LLM ignores.
_MAX_AGENTS_RULES = 3
_MAX_SKIP_PATTERNS = 2


@dataclass
class ProposedAddition:
    text: str               # the actual rule/pattern to add
    rationale: str          # 1-2 sentence why
    source_prs: list[str]   # citation list, e.g. ["o/r#42", "o/r#45"]


@dataclass
class ConsolidatorOutput:
    agents_rule_additions: list[ProposedAddition] = field(default_factory=list)
    skip_pattern_additions: list[ProposedAddition] = field(default_factory=list)


def _has_any_suggestion(reflections: list[Reflection]) -> bool:
    return any(r.suggestions for r in reflections)


def _build_consolidator_messages(reflections: list[Reflection]) -> list[dict]:
    """Construct the single-shot prompt for the consolidator LLM call.

    System prompt locks the output schema and the size caps. User message
    feeds in compact summaries of each reflection — outcome, surprises,
    raw suggestions — with REJECTED runs highlighted because those carry
    the strongest signal about what went wrong.
    """
    system_msg = (
        "You are a senior maintainer of an autonomous documentation bot. "
        "Each night you read the past week's run reflections and decide "
        "which 1-3 lessons should be promoted into the bot's persistent "
        "rules file (AGENTS.md) and which 1-2 path patterns should be "
        "added to its skip list (repo-map.yaml). Your output is JSON only, "
        "in this exact shape:\n"
        "{\n"
        '  "agents_rule_additions": [\n'
        '    {"text": "<one-sentence rule>", "rationale": "<why>", '
        '"source_prs": ["owner/repo#NN", ...]}\n'
        '  ],\n'
        '  "skip_pattern_additions": [\n'
        '    {"text": "<glob pattern>", "rationale": "<why>", '
        '"source_prs": ["owner/repo#NN", ...]}\n'
        '  ]\n'
        "}\n"
        "Rules:\n"
        f"- Output AT MOST {_MAX_AGENTS_RULES} agents_rule_additions and "
        f"AT MOST {_MAX_SKIP_PATTERNS} skip_pattern_additions.\n"
        "- Every entry MUST cite >=1 source PR. No anonymous suggestions.\n"
        "- Prefer suggestions that appeared in MULTIPLE reflections — that's "
        "evidence the lesson generalizes.\n"
        "- REJECTED reflections (bot failed) are the strongest signal. "
        "Suggestions extracted from them outrank those from PASS runs.\n"
        "- Output ONLY the JSON. No prose, no preamble, no fences."
    )

    # Build a compact user payload. We include enough context for the LLM
    # to reason about cross-reflection patterns without sending every run's
    # full body (which would blow the budget on a high-traffic week).
    blocks: list[str] = []
    for r in reflections:
        flag = " (REJECTED)" if r.outcome == OUTCOME_REJECTED else ""
        blocks.append(
            f"## Run {r.upstream_repo}#{r.pr_number}{flag}\n"
            f"- outcome: {r.outcome}, retries: {r.retries}\n"
            f"- scenarios: {', '.join(r.scenarios_processed) or '(none)'}\n"
            f"- surprises:\n"
            + ("\n".join(f"  * {s}" for s in r.surprises) or "  (none)")
            + "\n- raw suggestions:\n"
            + ("\n".join(
                f"  * [{s.kind}] {s.text} (from {s.source_pr})"
                for s in r.suggestions
            ) or "  (none)")
        )
    user_msg = (
        "Below are the reflections from the past week's runs. Distill them.\n\n"
        + "\n\n".join(blocks)
        + "\n\n# Output the JSON now."
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _coerce_addition(raw: dict) -> ProposedAddition | None:
    """Validate one proposal dict; return None to drop it."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text", "")).strip()
    rationale = str(raw.get("rationale", "")).strip()
    sources = raw.get("source_prs", [])
    if not isinstance(sources, list):
        return None
    cites = [str(s).strip() for s in sources if isinstance(s, str) and str(s).strip()]
    if not text or not cites:
        return None
    return ProposedAddition(text=text, rationale=rationale, source_prs=cites)


def _parse_consolidator_response(content: str) -> ConsolidatorOutput | None:
    """Parse the LLM's JSON; tolerate code fences and surrounding prose.
    Returns None if no valid JSON can be extracted."""
    if not content:
        return None
    text = content.strip()

    # Strip code-fence wrapper
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    agents = [
        a for a in (
            _coerce_addition(x) for x in data.get("agents_rule_additions", []) or []
        )
        if a is not None
    ]
    skips = [
        a for a in (
            _coerce_addition(x) for x in data.get("skip_pattern_additions", []) or []
        )
        if a is not None
    ]

    # Enforce plan caps. Sort by citation count desc so the most-evidenced
    # proposals survive the truncation.
    agents.sort(key=lambda a: -len(a.source_prs))
    skips.sort(key=lambda a: -len(a.source_prs))
    return ConsolidatorOutput(
        agents_rule_additions=agents[:_MAX_AGENTS_RULES],
        skip_pattern_additions=skips[:_MAX_SKIP_PATTERNS],
    )


def consolidate(reflections: list[Reflection]) -> ConsolidatorOutput:
    """Run the consolidator. Never raises — API failures collapse to empty
    output so the nightly cron simply skips opening a PR.

    Skips the LLM call entirely if there are no reflections, or if no
    reflection contains any suggestions (nothing for the LLM to chew on).
    """
    if not reflections or not _has_any_suggestion(reflections):
        return ConsolidatorOutput()

    messages = _build_consolidator_messages(reflections)
    try:
        response: LLMResponse = chat_completion(messages, temperature=0.0)
    except Exception:
        return ConsolidatorOutput()

    parsed = _parse_consolidator_response(response.content)
    if parsed is None:
        return ConsolidatorOutput()
    return parsed
