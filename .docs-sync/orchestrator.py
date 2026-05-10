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
from github_ops import fetch_upstream_digest, get_changed_paths
from security_utils import sanitize_output


# Exit codes (also written to GHA $GITHUB_OUTPUT for the workflow to read)
EXIT_PASS = 0
EXIT_SKIP_STAGE_A = 10
EXIT_SKIP_STAGE_B = 11
EXIT_ERROR = 20


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

    # --- Beyond Stage B is Slice 1+ work — STUB in Slice 0c -----------
    print(
        "[Stage 1+] (stub) — would now run plan, mechanical regen, "
        "prose generation, judge, and open PR. "
        f"Slice 0c verifies that the gate routes correctly."
    )

    if dry_run:
        print("[dry-run] not opening PR")
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
