"""docs-sync HARVEST entrypoint.

Triggered nightly by `.github/workflows/harvest-reflections.yml`. Reads
the last N days of docs-sync PRs on this repo, parses each branch's
REFLECTION.md, sends the batch to the consolidator (one Gemini Flash
call), and writes a proposal file the harvest workflow opens as a PR.

Exit codes (mirror orchestrator.py conventions):
  0  proposals generated — workflow opens the PR
  1  no proposals — workflow skips the PR step (nothing to review)
  20 unrecoverable error
"""
import argparse
import sys
from pathlib import Path

from reflection.consolidator import consolidate
from reflection.harvester import harvest
from reflection.proposal import write_proposal


EXIT_PASS = 0
EXIT_NO_PROPOSALS = 1
EXIT_ERROR = 20


_DEFAULT_PROPOSAL_PATH = Path(".docs-sync/HARVEST_PROPOSAL.md")


def run_harvest(
    repo: str,
    days: int,
    output_path: Path,
) -> int:
    """Pull → distill → write. Returns one of the EXIT_* codes."""
    try:
        reflections = harvest(repo, days=days)
    except Exception as e:
        print(f"[harvest] error fetching reflections: {type(e).__name__}: {e}",
              file=sys.stderr)
        return EXIT_ERROR

    print(f"[harvest] read {len(reflections)} REFLECTION.md from "
          f"last {days} days of docs-sync PRs on {repo}.")

    output = consolidate(reflections)
    print(f"[consolidate] proposed "
          f"{len(output.agents_rule_additions)} AGENTS.md rule(s), "
          f"{len(output.skip_pattern_additions)} skip pattern(s).")

    if not output.agents_rule_additions and not output.skip_pattern_additions:
        # Don't pollute the repo with empty proposal PRs.
        print("[harvest] nothing to propose this cycle.")
        return EXIT_NO_PROPOSALS

    write_proposal(output, output_path)
    print(f"[harvest] wrote proposal to {output_path}")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True,
                        help="owner/repo for the website (where docs-sync PRs live)")
    parser.add_argument("--days", type=int, default=7,
                        help="lookback window in days (default: 7)")
    parser.add_argument("--output", type=Path, default=_DEFAULT_PROPOSAL_PATH,
                        help="where to write HARVEST_PROPOSAL.md")
    args = parser.parse_args(argv)
    return run_harvest(args.repo, args.days, args.output)


if __name__ == "__main__":
    sys.exit(main())
