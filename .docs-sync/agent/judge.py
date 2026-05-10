"""Judge — second-pass review of LLM-generated docs by a DIFFERENT model.

Per Addy's "planner / generator / evaluator" split: the model that writes
prose should NOT be the one that grades it. We use Phi-4-mini via GitHub
Models for judging, while Gemini Flash does the writing. Different
training data + different objective = catches things the writer missed.

What the judge looks for:
  - Hallucinated scenario_type / CLI flag references (already covered by
    deterministic validate(), but the judge re-checks at semantic level)
  - "Plausible but wrong" prose — claims about behavior that can't be
    grounded in the parameter schema we actually shipped
  - Tone or structural issues that the deterministic checks miss

Verdict format: structured JSON so we don't parse free-form prose.
A judge response that fails to parse → treat as "judge unavailable" and
fall back to deterministic-only validation (don't block PRs on judge
infrastructure flakes).

Free-tier safe: Phi-4-mini is cheap on GitHub Models; we cap to one judge
call per draft to stay well within rate limits.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from agent.llm_client import chat_completion, LLMResponse
from extractors.krkn_hub import Scenario


JUDGE_VERDICT_CLEAN = "clean"
JUDGE_VERDICT_FLAGGED = "flagged"
JUDGE_VERDICT_UNAVAILABLE = "unavailable"

# Default judge model — caller can override via JUDGE_MODEL env or kwarg.
# Phi-4-mini is cheap, fast, and trained differently from Gemini.
_DEFAULT_JUDGE_MODEL = "phi-4-mini-instruct"


@dataclass
class JudgeVerdict:
    verdict: str            # clean | flagged | unavailable
    reasoning: str          # one-sentence summary, useful for PR body
    flagged_phrases: list[str]  # specific phrases the judge thought hallucinated
    response: LLMResponse | None = None  # carries token usage for reflection


def _build_judge_messages(
    scenario: Scenario,
    draft_body: str,
    taxonomy: dict,
) -> list[dict]:
    """Construct the prompt asking a different model to judge the draft."""
    valid_types = taxonomy.get("scenario_types", [])
    valid_flags = taxonomy.get("cli_flags", [])

    system_msg = (
        "You are a critical reviewer for documentation drafts. Your job is "
        "to detect HALLUCINATIONS — claims in the draft that are not grounded "
        "in the supplied scenario schema or the authoritative taxonomy. "
        "You output JSON only, in this exact shape:\n"
        '{"verdict": "clean" | "flagged", "reasoning": "<one sentence>", '
        '"flagged_phrases": ["<phrase>", ...]}\n'
        "Rules:\n"
        "- 'clean' means: every concrete claim in the draft is consistent "
        "  with the scenario schema and references only taxonomy entries.\n"
        "- 'flagged' means: at least one specific phrase makes a claim that "
        "  isn't supported. List those phrases (verbatim) in flagged_phrases.\n"
        "- Generic prose ('this scenario tests resilience') is not a "
        "  hallucination if there's nothing to ground it against — leave it.\n"
        "- Do NOT invent issues. If everything checks out, return 'clean' "
        "  with empty flagged_phrases."
    )

    schema_text = (
        f"Scenario name: {scenario.name}\n"
        f"scenario_type: {scenario.scenario_type or '(unknown)'}\n"
        f"Parameters:\n"
        + "\n".join(
            f"- {p.name} ({p.variable}): type={p.type}, default={p.default!r}, "
            f"required={p.required}, description={p.description!r}"
            for p in scenario.parameters
        )
    )

    user_msg = (
        f"# Authoritative scenario schema\n\n{schema_text}\n\n"
        f"# Authoritative taxonomy\n\n"
        f"```json\n{json.dumps({'scenario_types': valid_types, 'cli_flags_sample': valid_flags[:30]}, indent=2)}\n```\n\n"
        f"# Draft to judge\n\n```markdown\n{draft_body}\n```\n\n"
        f"# Output\n\nReturn ONLY the JSON verdict. No prose, no preamble."
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _parse_verdict(content: str) -> JudgeVerdict | None:
    """Extract JSON verdict from LLM response. Returns None on parse failure."""
    if not content or not content.strip():
        return None
    # The model might wrap in code fences; strip them
    text = content.strip()
    if text.startswith("```"):
        # Find first newline after fence open, take until closing fence
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()

    # Try to locate the JSON object — model may emit extra prose around it
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    verdict = parsed.get("verdict", "").strip().lower()
    if verdict not in (JUDGE_VERDICT_CLEAN, JUDGE_VERDICT_FLAGGED):
        return None

    return JudgeVerdict(
        verdict=verdict,
        reasoning=str(parsed.get("reasoning", "")).strip(),
        flagged_phrases=[
            str(p) for p in parsed.get("flagged_phrases", [])
            if isinstance(p, str) and p.strip()
        ],
    )


def judge(
    scenario: Scenario,
    draft_body: str,
    taxonomy: dict,
    *,
    model: str | None = None,
) -> JudgeVerdict:
    """Run the judge. Returns a verdict; never raises (judge infra failures
    return JUDGE_VERDICT_UNAVAILABLE so the orchestrator can decide whether
    to gate on judge-clean or proceed with `judge-flagged` PR label).
    """
    judge_model = model or os.environ.get("JUDGE_MODEL") or _DEFAULT_JUDGE_MODEL
    messages = _build_judge_messages(scenario, draft_body, taxonomy)

    try:
        response: LLMResponse = chat_completion(
            messages, model=judge_model, temperature=0.0,
            max_output_tokens=512,
        )
    except Exception as e:
        return JudgeVerdict(
            verdict=JUDGE_VERDICT_UNAVAILABLE,
            reasoning=f"judge call failed: {type(e).__name__}",
            flagged_phrases=[],
        )

    parsed = _parse_verdict(response.content)
    if parsed is None:
        return JudgeVerdict(
            verdict=JUDGE_VERDICT_UNAVAILABLE,
            reasoning="judge returned unparseable response",
            flagged_phrases=[],
            response=response,
        )
    parsed.response = response
    return parsed
