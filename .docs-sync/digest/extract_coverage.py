"""Find coverage gaps between scenario_type config keys (from TAXONOMY.json)
and the doc directory tree. Outputs `.docs-sync-digest/COVERAGE.json`.

Two failure modes this catches:
  1. Drift in: scenario_type appears in YAML config blocks but no doc directory
     exists for it. Users following the config example would land on a 404.
  2. Drift out: doc directory exists but no scenario_type points at it.
     Either the page is orphaned, or the YAML config docs need updating.

Why it's not a simple s/_/-/ transform:
  pod_disruption_scenarios   → pod-scenario             (drops "disruption", -s)
  network_chaos_scenarios    → network-chaos-scenario   (just plural shift)
  service_disruption_scenarios → service-disruption-scenarios  (exact transform)
  application_outages_scenarios → application-outage    (drops both "scenarios" and -s)
  cluster_shut_down_scenarios → (no match)              (genuine gap)

Token-Jaccard handles all of these without per-pair manual mapping.

Pure deterministic Python — no LLM, bit-identical output for same input.

Run as a CLI from the website repo root:
    python .docs-sync/digest/extract_coverage.py
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# Match threshold — Jaccard similarity at or above this is a match.
# Empirically, 0.5 pairs the obvious cases (pod_disruption_scenarios → pod-scenario)
# without producing false positives between unrelated names.
MATCH_THRESHOLD = 0.5


def _normalize_token(t: str) -> str:
    """Map known plural tokens to their singular forms.

    Earned from sub-task 3 inspection finding C1: `outages` and `outage` are
    the same concept, but Jaccard saw them as different tokens.

    Earned from Run 2 regression: a generic "drop trailing -s if >=4 chars and
    not -ss" heuristic mangled `chaos` → `chao`, breaking the network-chaos
    matches. Greek/Latin roots and singulars-ending-in-s exist; safer to use
    an explicit allowlist of known krkn-corpus plurals.

    Add new entries here as inspection finds new false-negative pairs.
    """
    return _KNOWN_PLURAL_TO_SINGULAR.get(t, t)


# Plurals observed in the krkn-chaos corpus. Conservative allowlist —
# only entries proven necessary by inspection.
_KNOWN_PLURAL_TO_SINGULAR: dict[str, str] = {
    "outages": "outage",
}


def tokenize_scenario_type(name: str) -> set[str]:
    """Strip _scenario(s) suffix, split on _, normalize plurals.

    Examples:
        pod_disruption_scenarios       → {pod, disruption}
        node_scenarios                 → {node}
        http_load_scenario             → {http, load}
        application_outages_scenarios  → {application, outage}  (plural normalized)
    """
    s = name
    if s.endswith("_scenarios"):
        s = s[: -len("_scenarios")]
    elif s.endswith("_scenario"):
        s = s[: -len("_scenario")]
    if not s:
        return set()
    return {_normalize_token(t) for t in s.split("_") if t}


def tokenize_directory(name: str) -> set[str]:
    """Strip -scenario(s) suffix, split on -, normalize plurals.

    Examples:
        pod-scenario              → {pod}
        network-chaos-scenarios   → {network, chaos}
        aurora-disruption         → {aurora, disruption}  (no scenario suffix)
        zone-outage-scenarios     → {zone, outage}
    """
    s = name
    if s.endswith("-scenarios"):
        s = s[: -len("-scenarios")]
    elif s.endswith("-scenario"):
        s = s[: -len("-scenario")]
    if not s:
        return set()
    return {_normalize_token(t) for t in s.split("-") if t}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity: |intersection| / |union|. Empty sets → 0."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_best_match(scenario_type: str, directories: list[str]) -> tuple[Optional[str], float]:
    """Find the directory most similar to scenario_type by token Jaccard.

    Returns (best_directory_or_None, score). On ties, sorted alphabetical wins
    so the result is deterministic.
    """
    if not directories:
        return None, 0.0

    st_tokens = tokenize_scenario_type(scenario_type)
    if not st_tokens:
        return None, 0.0

    best_dir = None
    best_score = -1.0
    # Sort directories so ties resolve alphabetically (deterministic output).
    for d in sorted(directories):
        score = jaccard(st_tokens, tokenize_directory(d))
        if score > best_score:
            best_score = score
            best_dir = d

    return best_dir, max(best_score, 0.0)


def build_coverage(
    scenario_types: list[str],
    scenario_directories: list[str],
) -> dict:
    """Cross-reference scenario_types with scenario_directories.

    Returns a dict with:
      - matched: list of {scenario_type, directory, score}
      - scenario_types_without_directory: list of {scenario_type, best_candidate, best_score}
      - directories_without_scenario_type: list of directory names (orphans)
      - stats: counts
    """
    matched: list[dict] = []
    unmatched: list[dict] = []
    matched_dirs: set[str] = set()

    for st in sorted(set(scenario_types)):
        best_dir, best_score = find_best_match(st, scenario_directories)
        if best_score >= MATCH_THRESHOLD and best_dir is not None:
            matched.append({
                "scenario_type": st,
                "directory": best_dir,
                "score": round(best_score, 3),
            })
            matched_dirs.add(best_dir)
        else:
            unmatched.append({
                "scenario_type": st,
                "best_candidate": best_dir,
                "best_score": round(best_score, 3),
            })

    orphans = sorted(set(scenario_directories) - matched_dirs)

    return {
        "matched": matched,
        "scenario_types_without_directory": unmatched,
        "directories_without_scenario_type": orphans,
        "stats": {
            "total_scenario_types": len(set(scenario_types)),
            "total_directories": len(set(scenario_directories)),
            "matched": len(matched),
            "unmatched_scenario_types": len(unmatched),
            "orphan_directories": len(orphans),
            "match_threshold": MATCH_THRESHOLD,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--digest-dir",
        type=Path,
        default=Path(".docs-sync-digest"),
        help="Digest directory — must contain TAXONOMY.json (default: .docs-sync-digest)",
    )
    args = parser.parse_args(argv)

    taxonomy_path = args.digest_dir / "TAXONOMY.json"
    if not taxonomy_path.is_file():
        print(
            f"error: TAXONOMY.json not found at {taxonomy_path}. "
            "Run extract_taxonomy.py first.",
            file=sys.stderr,
        )
        return 2

    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))

    coverage = build_coverage(
        scenario_types=taxonomy.get("scenario_types", []),
        scenario_directories=taxonomy.get("scenario_directories", []),
    )

    out_path = args.digest_dir / "COVERAGE.json"
    out_path.write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    s = coverage["stats"]
    print(
        f"Wrote COVERAGE.json: "
        f"{s['matched']} matched, "
        f"{s['unmatched_scenario_types']} scenario_types without doc, "
        f"{s['orphan_directories']} orphan directories."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
