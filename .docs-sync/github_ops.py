"""GitHub + git operations — wraps `gh` CLI and `git` commands through
`run_command_safe` so secrets never leak into logs.

Used by the orchestrator to:
  - Fetch a PR's changed-paths list
  - Fetch upstream's `.docs-sync-digest/llms-full.txt` at head and base refs
  - Open the docs-sync PR via `peter-evans/create-pull-request` step OR
    via raw `gh pr create` (the workflow yields after this module runs)
"""
import json
from pathlib import Path
from typing import Iterable

from security_utils import run_command_safe, sanitize_output


def get_changed_paths(repo: str, pr_number: int) -> list[str]:
    """Return the list of file paths changed in upstream PR `pr_number`.

    Uses `gh api repos/<owner>/<repo>/pulls/<n>/files` and extracts the
    `filename` of each entry. Sorted for determinism.
    """
    result = run_command_safe(
        ["gh", "api", "--paginate", f"repos/{repo}/pulls/{pr_number}/files"],
        check=True,
    )
    data = json.loads(result.stdout) if result.stdout else []
    if not isinstance(data, list):
        return []
    return sorted(item.get("filename", "") for item in data if "filename" in item)


def fetch_upstream_digest(
    repo: str,
    sha: str,
    digest_path: str = ".docs-sync-digest/llms-full.txt",
) -> str:
    """Fetch a file's text content at a specific git ref via `gh api`.

    Returns empty string if the file doesn't exist at that ref (404).
    The base ref of a PR that ADDED the digest won't have it yet — that's
    fine, digest_diff handles the empty case.
    """
    result = run_command_safe(
        [
            "gh", "api",
            f"repos/{repo}/contents/{digest_path}?ref={sha}",
            "--jq", ".content",
        ],
        check=False,
    )

    if result.returncode != 0:
        # 404 (file doesn't exist at this ref) is OK — return empty.
        # Anything else is a real error worth surfacing.
        if "404" in (result.stderr or ""):
            return ""
        raise RuntimeError(
            f"failed to fetch digest from {repo}@{sha}: "
            f"{sanitize_output(result.stderr or '')}"
        )

    # The API returns base64-encoded content. Decode.
    import base64
    encoded = (result.stdout or "").strip()
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def configure_bot_identity() -> None:
    """Set git user.name + user.email to the standard github-actions bot."""
    run_command_safe([
        "git", "config", "user.name", "github-actions[bot]",
    ], check=True)
    run_command_safe([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ], check=True)


def has_unstaged_changes(paths: Iterable[str] | None = None) -> bool:
    """Return True if the working tree has uncommitted changes in `paths`
    (or anywhere if `paths` is None)."""
    cmd = ["git", "status", "--porcelain"]
    if paths:
        cmd.extend(["--", *paths])
    result = run_command_safe(cmd, check=True)
    return bool(result.stdout.strip())
