"""Extractor for krknctl upstream.

krknctl is the Go cobra CLI. The upstream digest builder
(`/krknctl/.docs-sync/build_upstream_digest.py`) shells out to the
built binary, walks every subcommand's `--help` output, and emits a
`## scenario:` block per subcommand with `scenario_type: cli_command`.

Nested cobra subcommands use underscore-joined composite names
(`graph_run`, `list_available`) so siblings under different parents
don't collide on leaf names. Flags are emitted as parameters with
`name` being the long flag (without `--`) and `variable` being the
ALL_CAPS_UNDERSCORE form.

Format-identical to krkn-hub → delegate to the shared parser. This
module exists as the per-upstream entry point; krknctl-specific
post-processing (e.g. annotating long-vs-short flag pairs) lands here.
"""
from __future__ import annotations

from extractors.krkn_hub import ChangeSet, extract as _shared_extract


def extract(head_digest: str, base_digest: str) -> ChangeSet:
    """Parse both digests and produce a ChangeSet for krknctl changes."""
    return _shared_extract(head_digest=head_digest, base_digest=base_digest)
