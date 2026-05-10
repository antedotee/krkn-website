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

from discovery import path_gate, digest_diff, GateResult
from extractors.krkn_hub import extract as extract_krkn_hub
from github_ops import fetch_upstream_digest, get_changed_paths
from regen.orchestrate import apply_regen_to_modified_scenarios
from security_utils import run_command_safe, sanitize_output


# Exit codes (also written to GHA $GITHUB_OUTPUT for the workflow to read)
EXIT_PASS = 0
EXIT_NO_FILES_MODIFIED = 1     # gates passed, regen ran, but produced no diff
EXIT_HUGO_FAILED = 2           # mechanical regen broke Hugo build
EXIT_SKIP_STAGE_A = 10
EXIT_SKIP_STAGE_B = 11
EXIT_ERROR = 20


# Map upstream short-name to its extractor entry point
_EXTRACTORS = {
    "krkn-hub": extract_krkn_hub,
    # krkn, krkn-ai, cerberus, krknctl extractors come in later slices.
    # See tasks/todo.md "DEFERRED — krkn upstream integration".
}


def _emit_status(stage: str, result: GateResult) -> None:
    """Print a single-line status report. The workflow YAML may parse this
    via `::notice` markers; humans can scan it during local dry-runs."""
    verdict = "PASS" if result.passed else "SKIP"
    paths_summary = (
        f" ({len(result.paths)} doc-affecting)" if result.paths else ""
    )
    print(f"[{stage}] {verdict}: {result.reason}{paths_summary}")


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
        print(f"[Stage 1] no extractor for upstream {upstream_short!r} — "
              f"see tasks/todo.md DEFERRED section. Stopping.",
              file=sys.stderr)
        return EXIT_ERROR

    change_set = extractor(head_digest=head_digest, base_digest=base_digest)
    print(
        f"[Stage 1] ChangeSet: "
        f"{len(change_set.scenarios_added)} added, "
        f"{len(change_set.scenarios_removed)} removed, "
        f"{len(change_set.scenarios_modified)} modified."
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

    if not change_set.scenarios_modified:
        print("[Stage 1] no modified scenarios — Slice 1 has nothing to do.")
        return EXIT_NO_FILES_MODIFIED

    # --- Stage 2: Mechanical regen -------------------------------------
    content_root = Path("content/en/docs")
    if not content_root.is_dir():
        print(f"[Stage 2] content root {content_root} not found — wrong CWD?",
              file=sys.stderr)
        return EXIT_ERROR

    modified_files = apply_regen_to_modified_scenarios(
        change_set=change_set,
        content_root=content_root,
    )
    print(f"[Stage 2] regen wrote {len(modified_files)} file(s).")
    for f in modified_files:
        print(f"  modified: {f}")

    if not modified_files:
        print("[Stage 2] no files needed updating (already up to date).")
        return EXIT_NO_FILES_MODIFIED

    # --- Stage 3: Hugo validate (success silent, failure verbose) ------
    hook_path = Path(".docs-sync/hooks/hugo_validate.sh")
    if hook_path.is_file():
        print("[Stage 3] running Hugo validation...")
        result = run_command_safe(["bash", str(hook_path)], check=False)
        if result.returncode != 0:
            print(f"[Stage 3] FAIL — Hugo build broke after regen.", file=sys.stderr)
            print(f"  stderr: {sanitize_output(result.stderr)}", file=sys.stderr)
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
