"""docs-sync main entrypoint.

Triggered by `repository_dispatch` from an upstream merge. Pipeline:
  1. Fetch the upstream PR's changed paths (gh api)
  2. Stage A — path_gate (~1ms): exit if no doc-affecting paths
  3. Stage B — digest_diff (~10ms + 2 gh api calls): exit if upstream
     `.docs-sync-digest/llms-full.txt` didn't change between base and head
  4. Stage 1 (Slice 1) — emit a structured plan.json (deterministic Python)
  5. Stage 2 (Slice 1) — mechanical regen of parameter tables between
     AUTO:START/AUTO:END markers
  6. Stage 3 (Slice 2) — prose generation via Gemini Flash, only when needed
  7. Stage 4 (Slice 2) — judge: detect hallucinated scenario_type strings
  8. Stage 5 — open the docs PR (peter-evans action, in the workflow YAML)
  9. Stage 6 — write REFLECTION.md to the PR branch

In Slice 0c, only Stages 1-2-3-4-5 are STUBS. We exit cleanly after
Stages A+B with a structured exit status the workflow uses to decide
whether to open a PR.

Exit codes:
  0  passed both stages, would (or did) open a PR
  10 skipped at Stage A (no doc-affecting paths)
  11 skipped at Stage B (digest unchanged)
  20 unrecoverable error (config/runtime bug)

CLI:
  python .docs-sync/orchestrator.py \\
      --upstream-repo antedotee/krkn-hub \\
      --pr-number 42 \\
      --head-sha abc123 \\
      --base-sha def456 \\
      [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from agent.draft_new_scenario import draft as draft_new_scenario_prose
from agent.judge import judge as judge_draft, JUDGE_VERDICT_FLAGGED
from discovery import path_gate, digest_diff, GateResult
from extractors.cerberus import extract as extract_cerberus
from extractors.krkn_ai import extract as extract_krkn_ai
from extractors.krkn_hub import extract as extract_krkn_hub, Scenario
from extractors.krknctl import extract as extract_krknctl
from github_ops import fetch_upstream_digest, get_changed_paths
from regen.orchestrate import apply_regen_to_modified_scenarios
from regen.parameter_table import regenerate_table
from security_utils import run_command_safe, sanitize_output
from reflection.writer import (
    OUTCOME_HUGO_FAILED,
    OUTCOME_PASS,
    OUTCOME_REJECTED,
    OUTCOME_SKIPPED,
    Reflection,
    new_reflection,
    save as save_reflection,
)
from state.state_md import (
    STATUS_DONE_DRAFT,
    STATUS_DONE_REGEN,
    STATUS_FAILED_DRAFT,
    STATUS_FAILED_HUGO,
    STATUS_IN_PROGRESS,
    StateMd,
    add_scenario,
    mark_scenario,
    new_state,
    save as save_state,
)


# Exit codes (also written to GHA $GITHUB_OUTPUT for the workflow to read)
EXIT_PASS = 0
EXIT_NO_FILES_MODIFIED = 1     # gates passed, regen ran, but produced no diff
EXIT_HUGO_FAILED = 2           # mechanical regen broke Hugo build
EXIT_SKIP_STAGE_A = 10
EXIT_SKIP_STAGE_B = 11
EXIT_DRAFT_REJECTED = 12       # all attempts at LLM draft failed validation
EXIT_EXTRACTOR_DEFERRED = 13   # upstream registered but extractor not built yet
EXIT_ERROR = 20


# Map upstream short-name to its extractor entry point.
# Adding an extractor here is the final wiring step for a new upstream
# (after repo-map.yaml entry + build_upstream_digest.py on the fork).
_EXTRACTORS = {
    "krkn-hub": extract_krkn_hub,
    "krkn-ai": extract_krkn_ai,
    "cerberus": extract_cerberus,
    "krknctl": extract_krknctl,
}

# Upstreams registered in repo-map.yaml but whose extractor is still TODO.
# Dispatches from these exit cleanly with EXIT_EXTRACTOR_DEFERRED instead
# of EXIT_ERROR — the path gate matters even when we can't yet sync, so
# we don't want noisy workflow failures during the rollout window.
_DEFERRED_UPSTREAMS = frozenset({"krkn"})

# Upstreams whose docs live under `content/en/docs/scenarios/<slug>/` with
# the `_tab-krkn-hub.md` / `_tab-krknctl.md` split. Only these are eligible
# for Stage 1.5 (LLM prose drafting for added scenarios) and Stage 2
# (mechanical table regen for modified scenarios) — both stages have
# krkn-hub-specific assumptions baked into their target-directory logic.
#
# For other upstreams (krkn-ai, etc.), the orchestrator still extracts a
# ChangeSet, writes STATE.md/REFLECTION.md (observability + harvest signal),
# and exits with EXIT_NO_FILES_MODIFIED. Generalizing prose/regen to those
# upstreams' doc layouts is a separate slice — see tasks/todo.md.
_KRKN_HUB_STYLE_UPSTREAMS = frozenset({"krkn-hub"})


def _emit_status(stage: str, result: GateResult) -> None:
    """Print a single-line status report. The workflow YAML may parse this
    via `::notice` markers; humans can scan it during local dry-runs."""
    verdict = "PASS" if result.passed else "SKIP"
    paths_summary = (
        f" ({len(result.paths)} doc-affecting)" if result.paths else ""
    )
    print(f"[{stage}] {verdict}: {result.reason}{paths_summary}")


def _load_taxonomy(content_root: Path) -> dict:
    """Load TAXONOMY.json from .docs-sync-digest/. Empty dict if not found."""
    path = content_root.parent.parent / ".docs-sync-digest" / "TAXONOMY.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_voice_samples(content_root: Path, count: int = 2) -> list[str]:
    """Pull a few existing scenario `_index.md` files for voice grounding.

    Heuristic: pick larger pages (more than 1KB) since tiny ones are stubs.
    Sorted alphabetically so output is deterministic across runs.
    """
    scenarios_root = content_root / "scenarios"
    if not scenarios_root.is_dir():
        return []
    candidates = []
    for d in sorted(scenarios_root.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        idx = d / "_index.md"
        if idx.is_file() and idx.stat().st_size > 1024:
            candidates.append(idx.read_text(encoding="utf-8"))
        if len(candidates) >= count:
            break
    return candidates


def _scenario_to_dir_name(name: str) -> str:
    """Convert a krkn-hub scenario name (`pod-scenarios`) to a website dir
    name (`pod-scenarios`). For now identity — we trust upstream naming.
    Future slices may add a normalization step if needed."""
    return name


# Where STATE.md gets written on the PR branch. Lives under .docs-sync/ so
# it's visually adjacent to repo-map.yaml and AGENTS.md when humans browse.
_STATE_MD_PATH = Path(".docs-sync/STATE.md")
_REFLECTION_MD_PATH = Path(".docs-sync/REFLECTION.md")


def _accumulate_tokens(reflection: Reflection, response) -> None:
    """Add this LLM call's output tokens to the reflection's running totals."""
    if response is None:
        return
    model = getattr(response, "model", "") or "unknown"
    tokens = int(getattr(response, "completion_tokens", 0) or 0)
    reflection.token_usage_total += tokens
    reflection.token_usage_by_model[model] = (
        reflection.token_usage_by_model.get(model, 0) + tokens
    )


def _draft_added_scenarios(
    scenarios: list[Scenario],
    content_root: Path,
    state: StateMd | None = None,
    state_path: Path | None = None,
    reflection: Reflection | None = None,
) -> list[Path] | None:
    """For each added scenario, draft an `_index.md` body via LLM, validate,
    judge, and (if all checks pass) write the file.

    Returns the list of files written, or None if ANY draft failed
    irrecoverably — the orchestrator treats `None` as a fatal error and
    aborts WITHOUT opening a partial PR.
    """
    taxonomy = _load_taxonomy(content_root)
    voice_samples = _load_voice_samples(content_root)

    written: list[Path] = []
    for scenario in scenarios:
        print(f"[Stage 1.5] drafting prose for added scenario: {scenario.name}")
        if state is not None:
            mark_scenario(state, scenario.name, status=STATUS_IN_PROGRESS)
            if state_path is not None:
                save_state(state, state_path)

        result = draft_new_scenario_prose(
            scenario=scenario,
            taxonomy=taxonomy,
            voice_samples=voice_samples,
            max_attempts=2,
        )
        if reflection is not None:
            _accumulate_tokens(reflection, result.response)
            # `attempts > 1` means at least one retry happened — surface it.
            reflection.retries += max(0, result.attempts - 1)
        if not result.accepted:
            rejection_summary = ", ".join(
                f"{r.code}({r.message})" for r in result.rejections
            )
            print(
                f"[Stage 1.5] REJECTED draft for {scenario.name}: "
                + rejection_summary,
                file=sys.stderr,
            )
            if state is not None:
                mark_scenario(
                    state, scenario.name,
                    status=STATUS_FAILED_DRAFT,
                    notes=f"draft rejected: {rejection_summary}",
                )
                if state_path is not None:
                    save_state(state, state_path)
            return None  # abort the whole run — don't ship partial output

        # Run the judge on the accepted draft for an independent check.
        verdict = judge_draft(scenario, result.body, taxonomy)
        if reflection is not None:
            _accumulate_tokens(reflection, verdict.response)
        print(f"[Stage 1.5] judge verdict for {scenario.name}: "
              f"{verdict.verdict} — {verdict.reasoning}")
        if verdict.verdict == JUDGE_VERDICT_FLAGGED:
            print(
                f"[Stage 1.5] judge flagged {scenario.name}; "
                f"phrases: {verdict.flagged_phrases}",
                file=sys.stderr,
            )
            # We DON'T abort — flagged drafts get the `judge-flagged` label
            # on the PR (workflow YAML wires this) so a human can review.

        # Write the file. Frontmatter is minimal — Hugo derives the page
        # title from `title:`. We pick a Title-Case form of the name.
        title = " ".join(w.capitalize() for w in scenario.name.split("-"))
        target_dir = content_root / "scenarios" / _scenario_to_dir_name(scenario.name)
        target_dir.mkdir(parents=True, exist_ok=True)
        index_md = target_dir / "_index.md"

        frontmatter = (
            "---\n"
            f"title: {title}\n"
            "description:\n"
            "weight: 99\n"  # new scenarios sort to end; maintainer sets weight
            "---\n\n"
        )
        index_md.write_text(frontmatter + result.body.rstrip() + "\n", encoding="utf-8")
        written.append(index_md)

        if state is not None:
            notes = "judge-flagged" if verdict.verdict == JUDGE_VERDICT_FLAGGED else ""
            mark_scenario(
                state, scenario.name,
                status=STATUS_DONE_DRAFT,
                target_files=[str(index_md)],
                notes=notes,
            )
            if state_path is not None:
                save_state(state, state_path)

    return written


def run_pipeline(
    upstream_repo: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    repo_map_path: Path,
    dry_run: bool = False,
) -> int:
    """Execute the relevance gate pipeline. Returns one of the EXIT_* codes."""

    # --- Stage A: path_gate (deterministic, free) -----------------------
    try:
        changed_paths = get_changed_paths(upstream_repo, pr_number)
    except Exception as e:
        print(f"[Stage A] error fetching changed paths: {sanitize_output(str(e))}",
              file=sys.stderr)
        return EXIT_ERROR

    print(f"PR {upstream_repo}#{pr_number}: {len(changed_paths)} files changed")

    # The repo-map.yaml uses the bare upstream name, not "owner/repo"
    upstream_short = upstream_repo.split("/", 1)[-1] if "/" in upstream_repo else upstream_repo

    try:
        a_result = path_gate(
            changed_paths=changed_paths,
            upstream_repo=upstream_short,
            repo_map_path=repo_map_path,
        )
    except KeyError as e:
        print(f"[Stage A] config error: {e}", file=sys.stderr)
        return EXIT_ERROR

    _emit_status("Stage A", a_result)
    if not a_result.passed:
        return EXIT_SKIP_STAGE_A

    # --- Stage B: digest_diff (deterministic, ~2 gh api calls) -----------
    try:
        head_digest = fetch_upstream_digest(upstream_repo, head_sha)
        base_digest = fetch_upstream_digest(upstream_repo, base_sha)
    except Exception as e:
        print(f"[Stage B] error fetching digests: {sanitize_output(str(e))}",
              file=sys.stderr)
        return EXIT_ERROR

    b_result = digest_diff(head_digest=head_digest, base_digest=base_digest)
    _emit_status("Stage B", b_result)
    if not b_result.passed:
        return EXIT_SKIP_STAGE_B

    # --- Stage 1: Extract structured ChangeSet -------------------------
    extractor = _EXTRACTORS.get(upstream_short)
    if extractor is None:
        if upstream_short in _DEFERRED_UPSTREAMS:
            print(f"[Stage 1] upstream {upstream_short!r} is registered in "
                  f"repo-map.yaml but the extractor is not yet implemented. "
                  f"Path gate fired correctly — exiting cleanly. "
                  f"See tasks/todo.md for the slice that wires this upstream.")
            return EXIT_EXTRACTOR_DEFERRED
        print(f"[Stage 1] no extractor for upstream {upstream_short!r} — "
              f"add an entry to repo-map.yaml and an extractor in "
              f".docs-sync/extractors/ if this is a new upstream.",
              file=sys.stderr)
        return EXIT_ERROR

    change_set = extractor(head_digest=head_digest, base_digest=base_digest)
    print(
        f"[Stage 1] ChangeSet: "
        f"{len(change_set.scenarios_added)} added, "
        f"{len(change_set.scenarios_removed)} removed, "
        f"{len(change_set.scenarios_modified)} modified."
    )

    # Initialize STATE.md now that we know the scenario list. We commit
    # this file alongside the regen output so the PR shows what the bot
    # planned to do and where it got stuck if anything failed.
    state = new_state(
        upstream_repo=upstream_repo,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
    )
    for s in change_set.scenarios_modified:
        add_scenario(state, s.name, "modified")
    for s in change_set.scenarios_added:
        add_scenario(state, s.name, "added")
    for s in change_set.scenarios_removed:
        add_scenario(state, s.name, "removed")
    save_state(state, _STATE_MD_PATH)

    # Initialize REFLECTION.md — outcome starts as PASS, downgraded if
    # anything fails. The harvester picks up the final file from this branch.
    reflection = new_reflection(
        upstream_repo=upstream_repo,
        pr_number=pr_number,
        head_sha=head_sha,
        outcome=OUTCOME_PASS,
    )
    reflection.scenarios_processed = (
        [s.name for s in change_set.scenarios_modified]
        + [s.name for s in change_set.scenarios_added]
        + [s.name for s in change_set.scenarios_removed]
    )

    # Slice 1 covers MODIFIED only — added/removed scenarios need prose
    # generation (Slice 2). Log them for visibility.
    if change_set.scenarios_added:
        print(f"[Stage 1] note: {len(change_set.scenarios_added)} added scenario(s) "
              "deferred to Slice 2 (prose): "
              f"{', '.join(s.name for s in change_set.scenarios_added)}")
    if change_set.scenarios_removed:
        print(f"[Stage 1] note: {len(change_set.scenarios_removed)} removed scenario(s) "
              "deferred to Slice 2 (deprecation prose): "
              f"{', '.join(s.name for s in change_set.scenarios_removed)}")

    # --- Stage 1.5: Draft prose for ADDED scenarios (Slice 2) ----------
    # Only krkn-hub-style upstreams get prose drafting — Stage 1.5's target
    # directory logic assumes the `content/en/docs/scenarios/<slug>/` layout.
    # For other upstreams (krkn-ai), the ChangeSet still gets extracted
    # (visible in STATE.md / REFLECTION.md) but no auto-draft fires.
    drafted_files: list[Path] = []
    if change_set.scenarios_added and upstream_short in _KRKN_HUB_STYLE_UPSTREAMS:
        drafted_files = _draft_added_scenarios(
            scenarios=change_set.scenarios_added,
            content_root=Path("content/en/docs"),
            state=state,
            state_path=_STATE_MD_PATH,
            reflection=reflection,
        )
        if drafted_files is None:
            save_state(state, _STATE_MD_PATH)
            reflection.outcome = OUTCOME_REJECTED
            save_reflection(reflection, _REFLECTION_MD_PATH)
            return EXIT_DRAFT_REJECTED  # all draft attempts failed validation
        print(f"[Stage 1.5] drafted {len(drafted_files)} new scenario page(s).")
    elif change_set.scenarios_added:
        print(f"[Stage 1.5] {len(change_set.scenarios_added)} added entity(ies) "
              f"detected for {upstream_short!r}, but Stage 1.5 prose drafting "
              f"is only wired for krkn-hub-style upstreams. ChangeSet recorded "
              f"in STATE.md for human review.")

    if not change_set.scenarios_modified and not drafted_files:
        print("[Stage 1] no modified or added scenarios — nothing to do.")
        state.completed = True
        save_state(state, _STATE_MD_PATH)
        reflection.outcome = OUTCOME_SKIPPED
        save_reflection(reflection, _REFLECTION_MD_PATH)
        return EXIT_NO_FILES_MODIFIED

    # --- Stage 2: Mechanical regen -------------------------------------
    content_root = Path("content/en/docs")
    if not content_root.is_dir():
        print(f"[Stage 2] content root {content_root} not found — wrong CWD?",
              file=sys.stderr)
        return EXIT_ERROR

    if upstream_short in _KRKN_HUB_STYLE_UPSTREAMS:
        modified_files = apply_regen_to_modified_scenarios(
            change_set=change_set,
            content_root=content_root,
        )
    else:
        # Non-krkn-hub upstreams don't yet have an AUTO-markered regen target;
        # the ChangeSet is still recorded in STATE.md/REFLECTION.md so humans
        # see what changed and the harvest loop has signal.
        print(f"[Stage 2] mechanical regen only wired for krkn-hub-style "
              f"upstreams. {upstream_short!r} change recorded; no auto-edit.")
        modified_files = []
    print(f"[Stage 2] regen wrote {len(modified_files)} file(s).")
    for f in modified_files:
        print(f"  modified: {f}")

    # Group regen-modified files by scenario name (dir-name segment between
    # `scenarios/` and the file) so we can report per-scenario in STATE.md.
    regen_files_by_scenario: dict[str, list[str]] = {}
    for f in modified_files:
        parts = f.parts
        if "scenarios" in parts:
            i = parts.index("scenarios")
            if i + 1 < len(parts):
                regen_files_by_scenario.setdefault(parts[i + 1], []).append(str(f))
    for scenario in change_set.scenarios_modified:
        files = regen_files_by_scenario.get(scenario.name, [])
        if files:
            mark_scenario(
                state, scenario.name,
                status=STATUS_DONE_REGEN,
                target_files=files,
            )
    save_state(state, _STATE_MD_PATH)

    if not modified_files:
        print("[Stage 2] no files needed updating (already up to date).")
        state.completed = True
        save_state(state, _STATE_MD_PATH)
        reflection.outcome = OUTCOME_SKIPPED
        save_reflection(reflection, _REFLECTION_MD_PATH)
        return EXIT_NO_FILES_MODIFIED

    # --- Stage 3: Hugo validate (success silent, failure verbose) ------
    hook_path = Path(".docs-sync/hooks/hugo_validate.sh")
    if hook_path.is_file():
        print("[Stage 3] running Hugo validation...")
        result = run_command_safe(["bash", str(hook_path)], check=False)
        if result.returncode != 0:
            print(f"[Stage 3] FAIL — Hugo build broke after regen.", file=sys.stderr)
            # Hugo writes most errors (build/template/PostCSS) to STDOUT, not
            # stderr, especially when running with --quiet. Dump both so the
            # CI log shows the real failure cause.
            print(f"  stdout: {sanitize_output(result.stdout)}", file=sys.stderr)
            print(f"  stderr: {sanitize_output(result.stderr)}", file=sys.stderr)
            # Mark every regen-done scenario as failed_hugo so the PR shows
            # the build-break, then persist before returning.
            for s in state.scenarios:
                if s.status == STATUS_DONE_REGEN:
                    mark_scenario(state, s.name, status=STATUS_FAILED_HUGO,
                                  notes="Hugo build broke after this scenario")
            state.notes = "Hugo build failed after mechanical regen; see CI logs."
            save_state(state, _STATE_MD_PATH)
            reflection.outcome = OUTCOME_HUGO_FAILED
            save_reflection(reflection, _REFLECTION_MD_PATH)
            return EXIT_HUGO_FAILED
        print("[Stage 3] Hugo build OK.")
    else:
        print(f"[Stage 3] hook not found at {hook_path} — skipping (slice 0c).")

    if dry_run:
        print("[Stage 5] dry-run — not opening PR. Modified files are on disk; "
              "the workflow's peter-evans step would commit and open the PR here.")
    else:
        print(f"[Stage 5] modified {len(modified_files)} file(s); "
              "the workflow's peter-evans step picks up from here.")

    state.completed = True
    save_state(state, _STATE_MD_PATH)
    reflection.outcome = OUTCOME_PASS
    save_reflection(reflection, _REFLECTION_MD_PATH)
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-repo", required=True,
                        help="owner/repo of the upstream that merged the PR")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--repo-map", type=Path,
                        default=Path(".docs-sync/repo-map.yaml"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't open a PR even if the pipeline would")
    args = parser.parse_args(argv)

    return run_pipeline(
        upstream_repo=args.upstream_repo,
        pr_number=args.pr_number,
        head_sha=args.head_sha,
        base_sha=args.base_sha,
        repo_map_path=args.repo_map,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
