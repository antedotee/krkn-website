"""Weekly audit checks — coverage gaps, deprecation references, broken links.

Each check is an independent module exporting a single `find_*` function
that returns `list[Finding]`. The audit CLI runs all three and hands the
combined results to `issue_writer` for the GitHub-issue upsert.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One audit observation. Frozen so we can build sets for dedup."""
    category: str        # "coverage_gap" | "deprecation" | "broken_link"
    title: str           # human-readable one-liner
    detail: str          # short markdown explanation
    source: str          # the upstream entity / file path / URL
