"""pytest config — add `.docs-sync/` to sys.path so tests can import top-level modules.

The directory `.docs-sync/` is dot-prefixed (Hugo convention for hidden infra),
which makes it an invalid Python module name. This shim lets tests use clean
imports like `from digest.build_docs_digest import strip_frontmatter`.
"""
import sys
from pathlib import Path

DOCS_SYNC_ROOT = Path(__file__).parent.resolve()
if str(DOCS_SYNC_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCS_SYNC_ROOT))
