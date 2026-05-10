"""Build the docs digest — Layer 2 of the two-layer digest model.

Walks `content/en/docs/**/*.md`, strips Hugo frontmatter and shortcodes, and emits
plain-text per-page files under `.docs-sync-digest/PER_PAGE/<slug>.txt`.

Pure deterministic Python — no LLM calls, bit-identical output for the same input.
The agent later reads these PER_PAGE files instead of raw markdown to save tokens
(~5-15× compression vs. raw .md with shortcodes and frontmatter).

Run as a CLI from the website repo root:
    python .docs-sync/digest/build_docs_digest.py

Or import in tests:
    from digest.build_docs_digest import process_file, build_per_page
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable


# Hugo frontmatter is YAML between two `---` lines at the top of a file.
# We deliberately match only at the start (^) — `---` later in the doc is a
# horizontal rule and stays.
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)

# Hugo shortcodes:
#   {{< name args >}} ... {{< /name >}}    — angle-bracket form
#   {{% name args %}} ... {{% /name %}}    — percent form (markdown-aware)
#   {{< name args />}}                     — self-closing
# We strip the markers themselves but preserve any inner content (it's prose).
_SHORTCODE_OPEN_CLOSE_RE = re.compile(r"\{\{[<%]\s*/?[^}]*?[%>]\}\}")

# HTML comments — including the AUTO:START/AUTO:END markers added by
# .docs-sync/migrate/add_auto_markers.py. Markers are meta tooling, not
# content the LLM should see. Earned from Slice 0.5 finding M3.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Custom HTML-style shortcodes used by the krkn-chaos site, e.g.
#   <krkn-hub-scenario id="pod-scenarios"> ... </krkn-hub-scenario>
#   <krkn-namespace>...</krkn-namespace>
#   <krkn-sa>...</krkn-sa>
# Pattern-based, not name-enumerated — new shortcodes get added over time.
# Earned from inspection finding A4 (Run 1): hardcoded names missed two real
# shortcodes in the corpus. Match anything in the `<krkn-*>` namespace.
# We deliberately do NOT touch standard HTML (a, br, code, details, strong, etc.).
_HTML_SHORTCODE_TAG_RE = re.compile(
    r"</?krkn-[a-z][a-z0-9-]*\b[^>]*/?>",
    re.IGNORECASE,
)


def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block. Body --- delimiters preserved."""
    return _FRONTMATTER_RE.sub("", text, count=1)


def strip_shortcodes(text: str) -> str:
    """Remove Hugo shortcode markers, custom HTML shortcode tags, and HTML
    comments (which include AUTO:START/AUTO:END markers from Slice 0.5).

    Inner prose between paired markers is preserved — it's still useful context.
    """
    text = _HTML_COMMENT_RE.sub("", text)
    text = _SHORTCODE_OPEN_CLOSE_RE.sub("", text)
    text = _HTML_SHORTCODE_TAG_RE.sub("", text)
    return text


def normalize_whitespace(text: str) -> str:
    """Strip trailing whitespace per line and collapse runs of blank lines.

    Two consecutive newlines (one blank line) is preserved — that's a paragraph
    break in markdown. Anything more collapses to two.
    """
    # Strip trailing spaces and tabs per line
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    # Collapse 3+ consecutive newlines into exactly 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading and trailing blank lines from the document
    return text.strip("\n")


def process_file(path: Path) -> str:
    """Read a markdown file and return its plain-text digest form."""
    text = path.read_text(encoding="utf-8")
    text = strip_frontmatter(text)
    text = strip_shortcodes(text)
    text = normalize_whitespace(text)
    return text


_ROOT_SLUG = "_root"


def slug_from_path(path: Path, content_root: Path) -> str:
    """Compute a stable, non-empty slug for a doc file relative to content_root.

    Examples (with content_root = content/en/docs):
        _index.md                             → _root              (D2 fix)
        scenarios/pod-scenario/_index.md      → scenarios/pod-scenario
        scenarios/pod-scenario/_tab-krkn.md   → scenarios/pod-scenario--tab-krkn
        _tab-foo.md                           → _root--tab-foo     (D2 fix)
        getting-started/quickstart.md         → getting-started/quickstart

    The `_root` placeholder is reserved for the content_root's own _index.md.
    Earned from inspection finding D2 (Run 1): empty slug produced a hidden
    `.txt` file at the digest root.
    """
    rel = path.relative_to(content_root)
    parts = list(rel.parts)
    name = parts[-1]
    stem = name[:-3] if name.endswith(".md") else name  # drop ".md"

    if stem == "_index":
        # Use the parent directory chain as the slug
        parents = parts[:-1]
        return "/".join(parents) if parents else _ROOT_SLUG

    if stem.startswith("_tab-"):
        # parent-dir + tab marker, e.g. pod-scenario--tab-krkn
        parent = "/".join(parts[:-1]) or _ROOT_SLUG
        tab_part = stem.replace("_tab-", "-tab-")
        return f"{parent}-{tab_part}"

    parents = parts[:-1]
    return "/".join(parents + [stem]) if parents else stem


def _iter_md_files(content_root: Path) -> Iterable[Path]:
    """Yield all .md files under content_root, skipping hidden dirs."""
    for path in content_root.rglob("*.md"):
        # Skip hidden directories (e.g., .docs-sync-digest if it lands here)
        if any(part.startswith(".") for part in path.relative_to(content_root).parts):
            continue
        yield path


def build_per_page(content_root: Path, output_dir: Path) -> dict:
    """Walk content_root and emit one .txt per .md under output_dir/PER_PAGE/.

    Returns: dict mapping slug → {char_count, source_path}.
    """
    per_page_dir = output_dir / "PER_PAGE"
    per_page_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    for md_path in sorted(_iter_md_files(content_root)):
        slug = slug_from_path(md_path, content_root)
        digest_text = process_file(md_path)

        out_path = per_page_dir / f"{slug}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(digest_text + "\n", encoding="utf-8")

        result[slug] = {
            "char_count": len(digest_text),
            "source_path": str(md_path.relative_to(content_root)),
        }

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--content-root",
        type=Path,
        default=Path("content/en/docs"),
        help="Root of the docs content tree to process (default: content/en/docs)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".docs-sync-digest"),
        help="Output directory (default: .docs-sync-digest)",
    )
    args = parser.parse_args(argv)

    if not args.content_root.exists():
        print(f"error: content root does not exist: {args.content_root}", file=sys.stderr)
        return 2

    result = build_per_page(args.content_root, args.output_dir)

    # Emit a manifest for downstream tools (extract_taxonomy, extract_coverage)
    manifest_path = args.output_dir / "PER_PAGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Wrote {len(result)} per-page digests to {args.output_dir}/PER_PAGE/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
