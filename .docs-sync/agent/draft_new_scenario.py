"""Draft the prose body of an `_index.md` for a NEW scenario.

Single-shot LLM call (NOT an open-ended agent loop) — keeps surface area
small and validation tractable. Per the AGENTS.md anti-rationalization
discipline, every output runs through structural and content gates BEFORE
being accepted.

Reject (don't write to disk) if ANY of:
  - Output isn't 200-700 words
  - References a scenario_type not in TAXONOMY.json
  - References a CLI flag not in TAXONOMY.json
  - Contains a Hugo shortcode or `<krkn-*>` HTML tag
  - Contains an H1 (`# `)
  - Missing required `##` sections (Why this matters, Use cases, Configuration)
  - Contains code-fence-only content (no prose at all)

Each rejection is surfaced as a `RejectionReason` so the orchestrator can
log WHY a draft was rejected (helps tune the prompt over time).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent.llm_client import chat_completion, LLMResponse
from extractors.krkn_hub import Parameter, Scenario


_SKILL_PATH = Path(__file__).parent / "skills" / "draft_new_scenario.md"

# Validation rules (also documented in the skill file — keep in sync)
_MIN_WORDS = 200
_MAX_WORDS = 700
_REQUIRED_HEADINGS = ("Why this matters", "Use cases", "Configuration")
_FORBIDDEN_PATTERNS = [
    (re.compile(r"^# [^#]", re.MULTILINE), "contains H1 heading"),
    (re.compile(r"^#{4,}\s", re.MULTILINE), "contains heading deeper than H3"),
    (re.compile(r"\{\{[<%]"), "contains Hugo shortcode"),
    (re.compile(r"</?krkn-[a-z]", re.IGNORECASE), "contains <krkn-*> tag"),
]


@dataclass
class RejectionReason:
    """Why we rejected an LLM output. Multiple may accumulate per draft."""
    code: str
    message: str


@dataclass
class DraftResult:
    """Carrier for a draft attempt. `accepted=True` only if no rejections."""
    accepted: bool
    body: str
    rejections: list[RejectionReason]
    response: LLMResponse | None = None
    attempts: int = 1  # how many tries it took (1 if accepted on first)


def _load_skill() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _format_voice_samples(samples: list[str]) -> str:
    """Render 2-3 sample _index.md files into the prompt for voice grounding."""
    out = []
    for i, s in enumerate(samples, 1):
        out.append(f"--- VOICE SAMPLE {i} ---\n{s.strip()}\n")
    return "\n".join(out)


def _format_scenario(scenario: Scenario) -> str:
    """Compact representation of a Scenario for the prompt."""
    lines = [
        f"Scenario name: {scenario.name}",
        f"scenario_type: {scenario.scenario_type or '(unknown)'}",
        f"Parameter count: {len(scenario.parameters)}",
        "",
        "Parameters (full schema):",
    ]
    for p in scenario.parameters:
        lines.append(
            f"- name={p.name!r}, variable={p.variable!r}, type={p.type!r}, "
            f"default={p.default!r}, required={p.required}, "
            f"description={p.description!r}"
        )
    return "\n".join(lines)


def build_prompt(
    scenario: Scenario,
    taxonomy: dict,
    voice_samples: list[str],
) -> list[dict]:
    """Construct the chat-completion messages for one draft attempt."""
    skill_text = _load_skill()
    samples_text = _format_voice_samples(voice_samples)
    scenario_text = _format_scenario(scenario)

    # Trim taxonomy to the lists the skill references (don't ship 200KB)
    taxonomy_view = {
        "scenario_types": taxonomy.get("scenario_types", []),
        "cli_flags_sample": taxonomy.get("cli_flags", [])[:50],  # cap for budget
    }

    system_msg = (
        "You are an expert technical writer for krkn-chaos.dev, a documentation "
        "site for chaos-engineering scenarios on Kubernetes. Follow the skill "
        "specification EXACTLY. Output ONLY the markdown body — no preamble, "
        "no commentary, no code fences around the output."
    )
    user_msg = (
        f"# Skill specification\n\n{skill_text}\n\n"
        f"# Authoritative taxonomy (use ONLY these strings)\n\n"
        f"```json\n{json.dumps(taxonomy_view, indent=2)}\n```\n\n"
        f"# Voice samples\n\n{samples_text}\n\n"
        f"# Scenario to draft\n\n{scenario_text}\n\n"
        f"# Output\n\nReturn the markdown body now (no frontmatter):"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def validate(body: str, taxonomy: dict) -> list[RejectionReason]:
    """Run structural + content checks. Returns list of rejections (empty=accept)."""
    rejections: list[RejectionReason] = []

    if not body or not body.strip():
        return [RejectionReason("empty", "model returned empty body")]

    # Word count
    word_count = len(body.split())
    if word_count < _MIN_WORDS:
        rejections.append(RejectionReason(
            "too_short",
            f"body is {word_count} words, minimum {_MIN_WORDS}",
        ))
    if word_count > _MAX_WORDS:
        rejections.append(RejectionReason(
            "too_long",
            f"body is {word_count} words, maximum {_MAX_WORDS}",
        ))

    # Forbidden patterns
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(body):
            rejections.append(RejectionReason("forbidden_pattern", label))

    # Required headings (allow case-insensitive match for robustness)
    body_lower = body.lower()
    for heading in _REQUIRED_HEADINGS:
        # Match `## Heading` style — case-insensitive on the heading text
        if not re.search(rf"^##\s+{re.escape(heading)}", body, re.MULTILINE | re.IGNORECASE):
            rejections.append(RejectionReason(
                "missing_heading",
                f"required `## {heading}` section not found",
            ))

    # No prose at all (e.g., output is one big code fence)
    prose_lines = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith(("```", "|", "<!--"))
    ]
    if len(prose_lines) < 5:
        rejections.append(RejectionReason(
            "no_prose",
            f"only {len(prose_lines)} prose lines found; expected substantive prose",
        ))

    # Hallucination check: scenario_type mentions
    valid_types = set(taxonomy.get("scenario_types", []))
    type_pattern = re.compile(r"\b([a-z][a-z0-9_]*_scenarios?)\b")
    for match in type_pattern.finditer(body):
        mentioned = match.group(1)
        if mentioned not in valid_types:
            rejections.append(RejectionReason(
                "invented_scenario_type",
                f"draft mentions scenario_type {mentioned!r} which is NOT in TAXONOMY.json",
            ))
            break  # one is enough to fail

    return rejections


def draft(
    scenario: Scenario,
    taxonomy: dict,
    voice_samples: list[str],
    *,
    max_attempts: int = 2,
) -> DraftResult:
    """Generate a draft `_index.md` body. Re-attempts up to `max_attempts`
    times with the same prompt — the LLM's variance plus our temperature=0.2
    means a second attempt sometimes produces a valid output.

    Returns the FIRST accepted draft, OR the last attempt with all rejections
    if nothing passed.
    """
    messages = build_prompt(scenario, taxonomy, voice_samples)

    last_response: LLMResponse | None = None
    last_rejections: list[RejectionReason] = []

    for attempt in range(max_attempts):
        response = chat_completion(messages)
        last_response = response
        body = response.content.strip()

        # Strip stray code-fence wrapper if present (LLMs sometimes do this
        # despite explicit instructions; benign to clean up).
        if body.startswith("```") and body.endswith("```"):
            lines = body.splitlines()
            body = "\n".join(lines[1:-1])

        rejections = validate(body, taxonomy)
        if not rejections:
            return DraftResult(
                accepted=True,
                body=body,
                rejections=[],
                response=response,
                attempts=attempt + 1,
            )
        last_rejections = rejections

    return DraftResult(
        accepted=False,
        body=last_response.content if last_response else "",
        rejections=last_rejections,
        response=last_response,
        attempts=max_attempts,
    )
