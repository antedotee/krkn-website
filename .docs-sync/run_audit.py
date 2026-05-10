"""docs-sync AUDIT entrypoint — weekly health check.

Runs three independent checks against the current corpus:
  1. coverage gaps    — upstream scenario_types without a doc directory
  2. deprecation refs — doc references to entities upstream has removed
  3. broken links     — lychee report on the rendered Hugo site

Findings are folded into a single GitHub issue via `upsert_audit_issue`
which is idempotent: it edits the existing issue on subsequent runs and
closes it when everything is clean.

Exit codes:
  0  audit ran cleanly (no findings — issue closed if it existed)
  1  audit ran cleanly but with findings (issue created/edited)
  20 unrecoverable error
"""
import argparse
import sys
from pathlib import Path

from audit import Finding
from audit.coverage_check import find_coverage_gaps
from audit.deprecation_check import find_deprecated_references
from audit.issue_writer import upsert_audit_issue
from audit.link_check import parse_lychee_output


EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_ERROR = 20

_DEFAULT_COVERAGE = Path(".docs-sync-digest/COVERAGE.json")
_DEFAULT_WEBSITE_TAXONOMY = Path(".docs-sync-digest/TAXONOMY.json")
_DEFAULT_PER_PAGE = Path(".docs-sync-digest/PER_PAGE")


def run_audit(
    repo: str,
    coverage_path: Path,
    website_taxonomy_path: Path,
    upstream_taxonomy_path: Path | None,
    per_page_dir: Path,
    lychee_output: Path | None,
) -> int:
    """Run all three checks, post the issue, return exit code."""
    findings: list[Finding] = []

    # 1. Coverage gaps
    coverage = find_coverage_gaps(coverage_path)
    print(f"[audit] coverage gaps: {len(coverage)}")
    findings.extend(coverage)

    # 2. Deprecation refs (requires an upstream taxonomy snapshot to diff)
    if upstream_taxonomy_path:
        depr = find_deprecated_references(
            website_taxonomy=website_taxonomy_path,
            upstream_taxonomy=upstream_taxonomy_path,
            per_page_dir=per_page_dir,
        )
        print(f"[audit] deprecation refs: {len(depr)}")
        findings.extend(depr)
    else:
        print("[audit] deprecation check skipped (no upstream taxonomy provided)")

    # 3. Broken links (lychee output is optional — workflow may run the
    # lychee step first and feed us its JSON; locally we skip)
    if lychee_output and lychee_output.is_file():
        payload = lychee_output.read_text(encoding="utf-8")
        bl = parse_lychee_output(payload)
        print(f"[audit] broken links: {len(bl)}")
        findings.extend(bl)
    else:
        print("[audit] link check skipped (no lychee output provided)")

    upsert_audit_issue(repo, findings)
    print(f"[audit] {len(findings)} total finding(s) — "
          f"{'issue updated' if findings else 'issue closed (if open)'}")

    return EXIT_FOUND if findings else EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True,
                        help="owner/repo for issue posting (website repo)")
    parser.add_argument("--coverage", type=Path, default=_DEFAULT_COVERAGE)
    parser.add_argument("--website-taxonomy", type=Path,
                        default=_DEFAULT_WEBSITE_TAXONOMY)
    parser.add_argument("--upstream-taxonomy", type=Path,
                        help="path to a freshly-built upstream TAXONOMY.json "
                             "(if omitted, deprecation check is skipped)")
    parser.add_argument("--per-page", type=Path, default=_DEFAULT_PER_PAGE)
    parser.add_argument("--lychee-output", type=Path,
                        help="path to lychee --format json output "
                             "(if omitted, link check is skipped)")
    args = parser.parse_args(argv)

    try:
        return run_audit(
            repo=args.repo,
            coverage_path=args.coverage,
            website_taxonomy_path=args.website_taxonomy,
            upstream_taxonomy_path=args.upstream_taxonomy,
            per_page_dir=args.per_page,
            lychee_output=args.lychee_output,
        )
    except Exception as e:
        print(f"[audit] unrecoverable error: {type(e).__name__}: {e}",
              file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
