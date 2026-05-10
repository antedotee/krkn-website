"""Deprecation reference audit — flags doc pages that mention upstream
entities (scenario_types, CLI flags) which no longer exist in upstream's
current taxonomy.

The signal: an entity is present in the website's TAXONOMY snapshot but
absent from the freshly-fetched upstream taxonomy → it was removed
upstream. If any PER_PAGE digest contains a verbatim mention of that
entity, the doc page is referencing something that no longer exists.

Per-entity dedup: if 5 doc pages mention the same removed entity, that's
ONE finding listing all 5 pages — not 5 findings (just noise).
"""
from __future__ import annotations

import json
from pathlib import Path

from audit import Finding


def _load_taxonomy(path: Path) -> tuple[set[str], set[str]]:
    """Return (scenario_types, cli_flags) sets. Empty if file missing/bad."""
    if not path.is_file():
        return set(), set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set(), set()
    if not isinstance(data, dict):
        return set(), set()
    return (
        set(s for s in data.get("scenario_types", []) or [] if isinstance(s, str)),
        set(f for f in data.get("cli_flags", []) or [] if isinstance(f, str)),
    )


def _find_pages_mentioning(needle: str, per_page_dir: Path) -> list[str]:
    """Return doc slugs (file stems) whose PER_PAGE digest contains `needle`."""
    if not per_page_dir.is_dir():
        return []
    hits: list[str] = []
    for path in sorted(per_page_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle in text:
            hits.append(path.stem)
    return hits


def find_deprecated_references(
    website_taxonomy: Path,
    upstream_taxonomy: Path,
    per_page_dir: Path,
) -> list[Finding]:
    """Detect doc references to upstream entities removed since last sync.

    Args:
        website_taxonomy: the website's stored `TAXONOMY.json` snapshot
        upstream_taxonomy: a fresh build from upstream's current `llms-full.txt`
        per_page_dir: the website's `PER_PAGE/` directory (per-doc text dumps)
    """
    website_types, website_flags = _load_taxonomy(website_taxonomy)
    upstream_types, upstream_flags = _load_taxonomy(upstream_taxonomy)

    findings: list[Finding] = []

    for removed in sorted(website_types - upstream_types):
        pages = _find_pages_mentioning(removed, per_page_dir)
        if not pages:
            continue
        findings.append(Finding(
            category="deprecation",
            title=f"Deprecated upstream scenario_type `{removed}` still referenced in docs",
            detail=(
                f"Upstream no longer ships `{removed}` but the following "
                f"doc page(s) still mention it:\n"
                + "\n".join(f"  - `{p}`" for p in pages)
                + "\n\nEither remove the references or restore the scenario "
                f"upstream. Reference removed during the period between the "
                f"last TAXONOMY snapshot and this audit run."
            ),
            source=removed,
        ))

    for removed in sorted(website_flags - upstream_flags):
        pages = _find_pages_mentioning(removed, per_page_dir)
        if not pages:
            continue
        findings.append(Finding(
            category="deprecation",
            title=f"Deprecated upstream CLI flag `{removed}` still referenced in docs",
            detail=(
                f"Upstream no longer accepts `{removed}` but it appears in "
                f"doc page(s):\n"
                + "\n".join(f"  - `{p}`" for p in pages)
                + "\n\nUpdate or remove these references."
            ),
            source=removed,
        ))

    return findings
