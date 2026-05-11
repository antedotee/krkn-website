"""Extractor for cerberus upstream.

cerberus is a Python health monitor; its user-facing surface is the
top-level sections of `config/config.yaml` (cerberus / tunings / database).
The upstream digest builder
(`/cerberus/.docs-sync/build_upstream_digest.py`) parses that file and
emits each section as a separate `## scenario:` block with
`scenario_type: config_section`, where each leaf YAML key becomes a
documented parameter.

Format-identical to krkn-hub → we delegate parsing to the shared parser
in `extractors.krkn_hub`. This module exists as the per-upstream
entry point in the orchestrator's `_EXTRACTORS` table; any
cerberus-specific post-processing lands here rather than polluting
the krkn-hub extractor.
"""
from __future__ import annotations

from extractors.krkn_hub import ChangeSet, extract as _shared_extract


def extract(head_digest: str, base_digest: str) -> ChangeSet:
    """Parse both digests and produce a ChangeSet for cerberus changes."""
    return _shared_extract(head_digest=head_digest, base_digest=base_digest)
