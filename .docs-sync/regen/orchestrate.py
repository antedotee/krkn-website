"""Orchestration helpers — find target doc files and apply mechanical regen.

Used by the main `orchestrator.py` between Stage 1 (extractor produces a
ChangeSet) and Stage 5 (workflow opens a PR).
"""
from __future__ import annotations

from pathlib import Path

from digest.extract_coverage import jaccard, tokenize_directory
from extractors.krkn_hub import ChangeSet, ModifiedScenario
from regen.parameter_table import regenerate_table


# Score below which we won't claim a confident match (defensive against
# unrelated dirs accidentally being picked as targets).
_MATCH_THRESHOLD = 0.5

# Doc tabs the krkn-hub extractor's data populates.
# Keep this list explicit — different upstreams target different tabs.
KRKN_HUB_TARGET_TABS = ("_tab-krkn-hub.md", "_tab-krknctl.md")


def find_target_doc_dir(
    upstream_scenario_name: str,
    scenarios_root: Path,
) -> Path | None:
    """Map an upstream scenario name (e.g. `pod-scenarios`) to a website
    scenario directory (e.g. `content/en/docs/scenarios/pod-scenario`).

    Reuses the same token-Jaccard matcher that powers the coverage analysis
    in Slice 0a/3 — krkn-hub names don't have a deterministic transform to
    website slugs (`pod-scenarios` → `pod-scenario`, plural drops; some
    scenarios have wholly different names like `power-outages` →
    `power-outage-scenarios`).

    Returns None if no candidate scores ≥ 0.5.
    """
    upstream_tokens = tokenize_directory(upstream_scenario_name)
    if not upstream_tokens or not scenarios_root.is_dir():
        return None

    best_dir: Path | None = None
    best_score = 0.0
    for entry in sorted(scenarios_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        score = jaccard(upstream_tokens, tokenize_directory(entry.name))
        if score > best_score:
            best_score = score
            best_dir = entry

    if best_score < _MATCH_THRESHOLD:
        return None
    return best_dir


def apply_regen_to_modified_scenarios(
    change_set: ChangeSet,
    content_root: Path,
    target_tabs: tuple[str, ...] = KRKN_HUB_TARGET_TABS,
) -> list[Path]:
    """For each modified scenario in the ChangeSet, regenerate parameter
    tables in the matching website doc tab files. Returns the list of
    files that were actually changed (files where regen produced a diff).
    """
    scenarios_root = content_root / "scenarios"
    if not scenarios_root.is_dir():
        return []

    modified_files: list[Path] = []
    skipped_unmatched: list[str] = []

    for modified in change_set.scenarios_modified:
        target_dir = find_target_doc_dir(modified.name, scenarios_root)
        if target_dir is None:
            skipped_unmatched.append(modified.name)
            continue

        for tab_name in target_tabs:
            tab_file = target_dir / tab_name
            if not tab_file.is_file():
                continue
            original = tab_file.read_text(encoding="utf-8")
            new_content = regenerate_table(
                original,
                params=modified.head.parameters,
                marker_id="params",
            )
            if new_content != original:
                tab_file.write_text(new_content, encoding="utf-8")
                modified_files.append(tab_file)

    if skipped_unmatched:
        # Surface for the orchestrator's run summary — not an error,
        # but worth flagging so a human can decide to add a manual mapping.
        print(
            f"warn: {len(skipped_unmatched)} scenario(s) had no doc-dir "
            f"match (score < {_MATCH_THRESHOLD}): "
            f"{', '.join(skipped_unmatched[:5])}"
            + (f" (+{len(skipped_unmatched) - 5} more)"
               if len(skipped_unmatched) > 5 else "")
        )

    return modified_files
