#!/usr/bin/env bash
#
# Validate that the Hugo site still builds after mechanical regen.
# Earned-rule from AGENTS.md: "Hugo build must pass before opening any PR.
# Errors are blockers." The orchestrator runs this immediately after Stage 2
# (and later, after Stage 3 prose generation) — fails loud if the regen
# produced markup Hugo can't parse.
#
# Output: success silent (per HumanLayer principle); failures verbose.
#
# Exits 0 on success, non-zero on failure (incl. hugo not installed).

set -e
set -o pipefail

# Hugo's `--quiet` suppresses build-time INFO; we still see WARN and ERROR.
# `--minify` exercises more of the rendering pipeline than a plain build.
# `--gc` prunes orphaned cache entries — defensive against stale cache.
hugo --quiet --minify --gc

# Hugo exits 0 even when it logs warnings about missing pages (e.g. 404).
# We don't fail on those for now — the production site has known broken
# refs that aren't our responsibility to fix in this slice.
