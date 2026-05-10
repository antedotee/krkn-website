"""Extractor for krkn-ai upstream.

krkn-ai is a Python project with a Click CLI. The upstream digest builder
(`/krkn-ai/.docs-sync/build_upstream_digest.py`) walks `krkn_ai/cli/cmd.py`
via AST and emits `llms-full.txt` in the SAME format as krkn-hub: each
documented entity is a `## scenario: <name>` block with a parameter table.

The "scenario_type" field on each entity is set to `cli_command` so the
downstream routing can pick the right doc target. Future extensions
(Pydantic config models, scenario classes) will use different
`scenario_type` sentinels.

Format-identical to krkn-hub → we delegate parsing to the shared parser
in `extractors.krkn_hub`. This module exists so the orchestrator's
`_EXTRACTORS` table has a clean per-upstream entry point, and so any
krkn-ai-specific post-processing can live here without polluting the
krkn-hub extractor.
"""
from __future__ import annotations

from extractors.krkn_hub import ChangeSet, extract as _shared_extract


def extract(head_digest: str, base_digest: str) -> ChangeSet:
    """Parse both digests and produce a ChangeSet for krkn-ai changes."""
    return _shared_extract(head_digest=head_digest, base_digest=base_digest)
