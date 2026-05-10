"""Build INDEX.md — a one-line-per-page index of the entire docs site.

Format follows the [llms.txt convention](https://llmstxt.org/) so the eventual
LLM agent can find "which doc page covers X" in O(1) without reading all 184
PER_PAGE files. Cuts agent context budget by ~10× during the routing stage.

Quality matters: a vague summary makes the agent pick the wrong page.

Source priority for each page:
  1. Frontmatter `description:` field (most explicit)
  2. First H1/H2 heading text in body (titles often duplicate intent)
  3. First non-empty paragraph (last resort)

Pure deterministic Python — no LLM. Run from website repo root:
    python .docs-sync/digest/build_index.py
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml


# Frontmatter at top of file — same regex as build_docs_digest, kept local
# so this script can run independently.
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)

# Strip simple inline markdown so summaries are pure prose.
_MD_INLINE_PATTERNS = [
    (re.compile(r"\*\*([^*]+?)\*\*"), r"\1"),    # **bold**
    (re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)"), r"\1"),  # *italic*
    (re.compile(r"`([^`]+?)`"), r"\1"),           # `code`
    (re.compile(r"\[([^\]]+?)\]\([^)]*?\)"), r"\1"),  # [text](url) → text
    # Hugo shortcodes — same patterns as build_docs_digest. Earned from
    # inspection I5: closing tags like </krkn-hub-scenario> were landing
    # in summaries when they tailed the first prose paragraph.
    (re.compile(r"\{\{[<%]\s*/?[^}]*?[%>]\}\}"), ""),  # {{< ... >}} / {{% ... %}}
    (re.compile(r"</?krkn-[a-z][a-z0-9-]*\b[^>]*/?>", re.IGNORECASE), ""),  # <krkn-*>
]

# Skip these block prefixes when looking for a summary
_SKIP_BLOCK_PREFIXES = ("<", "{{", "```", "    ", "- ", "* ", "|")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (parsed_frontmatter_dict, body_text). Empty dict if no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_yaml = m.group(1)
    body = text[m.end():]
    try:
        fm = yaml.safe_load(fm_yaml) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def _clean_inline_markdown(s: str) -> str:
    """Strip bold/italic/code/link syntax — leave plain text."""
    for pat, repl in _MD_INLINE_PATTERNS:
        s = pat.sub(repl, s)
    return s.strip()


def summarize_body(body: str, max_chars: int = 200) -> str:
    """Pull a one-line summary from a markdown body.

    Skips leading H1/H2 headings, HTML blocks, shortcodes, and code fences.
    Picks the first plain prose paragraph, cleans inline markdown, truncates.
    """
    if not body or not body.strip():
        return ""

    # Walk paragraph blocks (separated by blank lines)
    paragraphs = re.split(r"\n\s*\n", body.strip())
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Skip headings, HTML/shortcode-only blocks, code fences, lists
        if para.startswith("#"):
            continue
        if any(para.startswith(p) for p in _SKIP_BLOCK_PREFIXES):
            continue
        # Found a prose paragraph — clean and return
        cleaned = _clean_inline_markdown(para)
        # Collapse internal newlines to spaces (multi-line paragraph → single line)
        cleaned = " ".join(cleaned.split())
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars - 1].rstrip() + "…"
        return cleaned

    return ""


def _first_heading(body: str) -> str:
    """Return the text of the first H1 or H2 in the body, or empty."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return _clean_inline_markdown(line[2:].strip())
        if line.startswith("## "):
            return _clean_inline_markdown(line[3:].strip())
    return ""


def extract_page_metadata(md_path: Path) -> dict:
    """Read a markdown file and produce {title, summary, source_path}."""
    text = md_path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)

    title = ""
    if isinstance(fm.get("title"), str) and fm["title"].strip():
        title = fm["title"].strip()
    else:
        title = _first_heading(body)

    summary = ""
    if isinstance(fm.get("description"), str) and fm["description"].strip():
        # YAML literal block scalars produce trailing newlines — collapse
        summary = " ".join(fm["description"].split())
    else:
        summary = summarize_body(body)

    summary = _clean_inline_markdown(summary)

    return {
        "title": title,
        "summary": summary,
        "source_path": str(md_path),
    }


def _slug_from_md_path(md_path: Path, content_root: Path) -> str:
    """Same logic as build_docs_digest.slug_from_path — duplicated here so this
    script is independent. Earned constraint from inspection: must always
    return non-empty string (use _root for content_root's _index.md)."""
    rel = md_path.relative_to(content_root)
    parts = list(rel.parts)
    name = parts[-1]
    stem = name[:-3] if name.endswith(".md") else name

    if stem == "_index":
        parents = parts[:-1]
        return "/".join(parents) if parents else "_root"

    if stem.startswith("_tab-"):
        parent = "/".join(parts[:-1]) or "_root"
        tab_part = stem.replace("_tab-", "-tab-")
        return f"{parent}-{tab_part}"

    parents = parts[:-1]
    return "/".join(parents + [stem]) if parents else stem


def group_by_section(pages: list[dict]) -> dict[str, list[dict]]:
    """Group pages by their top-level directory (or `/` for root pages).

    A page's section is the first path component before `/` in its slug.
    Pages with no `/` (bare slugs like `installation`, `_root`) all go into
    the special section `/` (root).
    """
    sections: dict[str, list[dict]] = {}
    for page in pages:
        slug = page["slug"]
        if "/" in slug:
            section = slug.split("/", 1)[0]
        else:
            section = "/"
        sections.setdefault(section, []).append(page)
    # Sort each section's pages by slug for deterministic output
    for section in sections:
        sections[section].sort(key=lambda p: p["slug"])
    return sections


def render_index(grouped: dict[str, list[dict]], project_name: str) -> str:
    """Render the INDEX.md content."""
    lines = [
        f"# {project_name} — Documentation Index",
        "",
        "> Auto-generated by `.docs-sync/digest/build_index.py`. Do not edit by hand.",
        "> Used by the docs-sync agent to locate doc pages in O(1).",
        "",
    ]
    # Sections sorted, root section last (most specific first)
    section_names = sorted(grouped.keys())
    if "/" in section_names:
        section_names.remove("/")
        section_names.append("/")  # root last

    for section in section_names:
        display_name = "root" if section == "/" else section
        lines.append(f"## {display_name}")
        lines.append("")
        for page in grouped[section]:
            title = page["title"] or page["slug"]
            summary = page["summary"]
            entry = f"- [{title}]({page['slug']})"
            if summary:
                entry += f" — {summary}"
            lines.append(entry)
        lines.append("")  # blank line between sections

    return "\n".join(lines).rstrip() + "\n"


def _iter_md_files(content_root: Path) -> Iterable[Path]:
    """Yield .md files under content_root, skipping hidden dirs and tab variants.

    Tab files (e.g., `_tab-krkn.md`) are variants of a parent page reachable
    via the parent's directory. Including them in the index produces noisy
    slug-as-title entries (they typically have no title frontmatter and start
    with H4). The agent reads them when it needs to; they don't need top-level
    index entries.

    Earned from sub-task 4 inspection finding I1.
    """
    for path in content_root.rglob("*.md"):
        rel_parts = path.relative_to(content_root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.name.startswith("_tab-"):
            continue
        yield path


def build_index(
    content_root: Path,
    output_dir: Path,
    project_name: str,
) -> dict:
    """Walk content_root, collect metadata, render INDEX.md."""
    pages = []
    for md in sorted(_iter_md_files(content_root)):
        meta = extract_page_metadata(md)
        meta["slug"] = _slug_from_md_path(md, content_root)
        pages.append(meta)

    grouped = group_by_section(pages)
    markdown = render_index(grouped, project_name)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "INDEX.md").write_text(markdown, encoding="utf-8")

    return {
        "page_count": len(pages),
        "section_count": len(grouped),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--content-root", type=Path, default=Path("content/en/docs"),
        help="Root of the docs content tree (default: content/en/docs)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(".docs-sync-digest"),
        help="Output directory (default: .docs-sync-digest)",
    )
    parser.add_argument(
        "--project-name", default="krkn-chaos.dev",
        help="Project name shown in the INDEX heading (default: krkn-chaos.dev)",
    )
    args = parser.parse_args(argv)

    if not args.content_root.exists():
        print(f"error: content root not found: {args.content_root}", file=sys.stderr)
        return 2

    result = build_index(args.content_root, args.output_dir, args.project_name)
    print(
        f"Wrote INDEX.md: {result['page_count']} pages, "
        f"{result['section_count']} sections."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
