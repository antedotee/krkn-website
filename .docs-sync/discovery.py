"""Discovery — the 2-stage relevance gate.

Stage A (path_gate): pure-Python glob match against `repo-map.yaml`.
  Most-common case: PR touched only tests/ → exit early. Cost: ~1ms.
  Per D11, no LLM voice classifier — just deterministic rules.

Stage B (digest_diff): compare upstream `llms-full.txt` at head vs base ref.
  If the structured surface didn't change, exit silently. Cost: ~10ms +
  two `gh api` round trips (cached behind GitHub Actions runner network).

Most upstream PRs (~85% in our estimate) exit at Stage A. The rest pass
through Stage B; some still exit (refactor with no surface change). Only
the remainder reach Stage 1 (plan) and beyond.
"""
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml


@dataclass
class PathClassification:
    """Result of bucketing changed paths by repo-map.yaml rules."""
    doc_affecting: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    """Result of either Stage A or Stage B. `passed` means continue."""
    passed: bool
    reason: str
    paths: list[str] = field(default_factory=list)


def load_repo_map(path: Path | str) -> dict:
    """Load and parse `.docs-sync/repo-map.yaml`."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"repo map not found: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _match_any(path: str, patterns: list[str]) -> bool:
    """True if `path` matches any of the glob `patterns` (POSIX-style)."""
    for pattern in patterns:
        # fnmatch handles `*` and `**` via path component matching, but
        # `**/foo` requires checking against both the full path and any
        # trailing-suffix view. We do a simple two-pass check:
        #   1. Direct match via fnmatch
        #   2. If pattern starts with `**/`, also check against suffix
        if fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch(path, pattern[3:]):
            return True
    return False


def classify_paths(changed_paths: list[str], upstream_cfg: dict) -> PathClassification:
    """Bucket each changed path into doc_affecting / skipped / other.

    Skip-list takes precedence over doc-affecting list — defensive against
    sneaky overlaps like `tests/scenario/env.sh` matching both `tests/**`
    and `*/env.sh`.
    """
    skip_patterns = upstream_cfg.get("always_skip_paths", []) or []
    doc_patterns = upstream_cfg.get("doc_affecting_paths", []) or []

    result = PathClassification()
    for path in changed_paths:
        if _match_any(path, skip_patterns):
            result.skipped.append(path)
        elif _match_any(path, doc_patterns):
            result.doc_affecting.append(path)
        else:
            result.other.append(path)
    return result


def path_gate(
    changed_paths: list[str],
    upstream_repo: str,
    repo_map_path: Path | str,
) -> GateResult:
    """Stage A: deterministic relevance check.

    Returns `GateResult.passed=True` iff the PR touched at least one
    doc-affecting path that wasn't simultaneously a skip path.
    """
    if not changed_paths:
        return GateResult(passed=False, reason="no changed paths in PR")

    repo_map = load_repo_map(repo_map_path)
    if upstream_repo not in repo_map:
        raise KeyError(
            f"unknown upstream {upstream_repo!r} — not present in {repo_map_path}. "
            f"Add a config entry for it before processing PRs from this repo."
        )

    classification = classify_paths(changed_paths, repo_map[upstream_repo])

    if classification.doc_affecting:
        return GateResult(
            passed=True,
            reason=(
                f"doc-affecting paths changed: "
                f"{', '.join(classification.doc_affecting[:3])}"
                + (f" (+{len(classification.doc_affecting) - 3} more)"
                   if len(classification.doc_affecting) > 3 else "")
            ),
            paths=classification.doc_affecting,
        )

    # Nothing doc-affecting — skip with a reason that helps tune the rules.
    if classification.skipped and not classification.other:
        reason = "all paths in always_skip_paths (tests/CI/docs/etc.)"
    elif classification.other and not classification.skipped:
        reason = (
            f"none of {len(classification.other)} changed paths matched "
            f"doc_affecting_paths"
        )
    else:
        reason = "no doc-affecting paths in changed set (mix of skip + other)"
    return GateResult(passed=False, reason=f"skip — {reason}")


def digest_diff(head_digest: str, base_digest: str) -> GateResult:
    """Stage B: did the upstream surface change between base and head?

    Compares the raw text of `llms-full.txt` at the two refs. A bytes-
    identical match means no structural change — skip even though Stage A
    let the PR through.
    """
    if head_digest == base_digest:
        return GateResult(
            passed=False,
            reason="skip — upstream digest unchanged between head and base",
        )

    if not base_digest and head_digest:
        return GateResult(
            passed=True,
            reason="upstream surface added (no prior digest)",
        )

    if not head_digest and base_digest:
        return GateResult(
            passed=True,
            reason="upstream surface removed (no head digest)",
        )

    # Both present, different — surface changed.
    return GateResult(
        passed=True,
        reason="upstream digest differs between head and base",
    )
