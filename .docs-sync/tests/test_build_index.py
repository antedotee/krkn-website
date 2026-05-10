"""Tests for .docs-sync/digest/build_index.py.

INDEX.md is the llms.txt-style site index. The eventual LLM agent uses it to
locate "which doc page covers X" in O(1) without reading all 184 PER_PAGE files.
Quality matters: a vague or wrong summary makes the agent pick the wrong page.
"""
from pathlib import Path
from textwrap import dedent

import pytest

from digest.build_index import (
    extract_page_metadata,
    group_by_section,
    summarize_body,
    render_index,
    build_index,
)


# ─────────────────────────────────────────────────────────────────────────────
# extract_page_metadata — title + summary from .md source
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractPageMetadata:
    def test_uses_frontmatter_title_and_description(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: Pod Scenarios
            description: Disrupts pods matching label or namespace regex.
            weight: 3
            ---

            Body text here.
            """))
        meta = extract_page_metadata(md)
        assert meta["title"] == "Pod Scenarios"
        assert meta["summary"] == "Disrupts pods matching label or namespace regex."

    def test_falls_back_to_first_h1_for_title(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            description: Some summary
            ---

            # The Real Title

            Body.
            """))
        meta = extract_page_metadata(md)
        assert meta["title"] == "The Real Title"

    def test_falls_back_to_first_paragraph_for_summary(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: T
            ---

            This is the first paragraph that becomes the summary.

            Second paragraph ignored.
            """))
        meta = extract_page_metadata(md)
        assert meta["summary"].startswith("This is the first paragraph")

    def test_summary_truncated_to_one_line(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: T
            description: |
              This is the first sentence.
              This is the second sentence.
            ---

            body
            """))
        meta = extract_page_metadata(md)
        # YAML literal block should still produce a single-line summary
        # (multi-line frontmatter values get collapsed)
        assert "\n" not in meta["summary"]

    def test_handles_no_frontmatter(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            # Just a title

            Some text.
            """))
        meta = extract_page_metadata(md)
        assert meta["title"] == "Just a title"
        assert "Some text" in meta["summary"]

    def test_handles_no_summary_available(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: T\n---\n")  # only frontmatter, empty body
        meta = extract_page_metadata(md)
        assert meta["title"] == "T"
        # Use a stable empty placeholder rather than None
        assert meta["summary"] == ""

    # === Sub-task 4 inspection finding I5 — Hugo shortcodes in summary ===

    def test_strips_html_shortcode_close_tag_from_summary(self, tmp_path: Path):
        # Service Hijacking scenario had `The web service's source code is
        # available here. </krkn-hub-scenario>` as its summary because the
        # closing shortcode tag landed at the end of the first prose paragraph.
        # Earned from sub-task 4 inspection finding I5.
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: T
            ---

            <krkn-hub-scenario id="x">
            </krkn-hub-scenario>

            The real summary text is here. </krkn-hub-scenario>
            """))
        meta = extract_page_metadata(md)
        assert "krkn-hub-scenario" not in meta["summary"]
        assert "<" not in meta["summary"]
        assert "real summary" in meta["summary"]

    def test_strips_hugo_shortcodes_from_summary(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: T
            ---

            Real text {{< include file="x.md" >}} more text.
            """))
        meta = extract_page_metadata(md)
        assert "{{<" not in meta["summary"]
        assert "Real text" in meta["summary"]

    def test_strips_markdown_syntax_from_summary(self, tmp_path: Path):
        md = tmp_path / "page.md"
        md.write_text(dedent("""\
            ---
            title: T
            ---

            See **bold** and `code` and [link](url) and *italic*.
            """))
        meta = extract_page_metadata(md)
        # Bold/italic markers, code ticks, link brackets — gone
        s = meta["summary"]
        assert "**" not in s
        assert "`" not in s
        assert "[" not in s
        assert "*" not in s


# ─────────────────────────────────────────────────────────────────────────────
# summarize_body — extracts summary from a markdown body
# ─────────────────────────────────────────────────────────────────────────────

class TestSummarizeBody:
    def test_first_paragraph_no_heading(self):
        body = "This is paragraph 1.\n\nThis is paragraph 2."
        assert summarize_body(body).startswith("This is paragraph 1.")

    def test_skips_leading_heading(self):
        body = "# Heading\n\nFirst real paragraph.\n\nSecond."
        assert summarize_body(body).startswith("First real paragraph")

    def test_skips_leading_html_or_shortcode(self):
        body = "<krkn-namespace>\n\nReal content here.\n"
        assert "Real content here" in summarize_body(body)

    def test_returns_empty_string_when_body_is_empty(self):
        assert summarize_body("") == ""

    def test_truncates_long_paragraphs(self):
        # >200 chars should be truncated with ellipsis
        long_text = "Short start. " + ("filler " * 100)
        result = summarize_body(long_text)
        assert len(result) <= 200
        # First sentence preserved if short enough
        assert result.startswith("Short start.")


# ─────────────────────────────────────────────────────────────────────────────
# group_by_section
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupBySection:
    def test_groups_by_top_level_dir(self):
        pages = [
            {"slug": "scenarios/pod-scenario", "title": "Pod", "summary": "x"},
            {"slug": "scenarios/node-scenarios", "title": "Node", "summary": "y"},
            {"slug": "cerberus/installation", "title": "Install", "summary": "z"},
        ]
        result = group_by_section(pages)
        assert set(result.keys()) == {"scenarios", "cerberus"}
        assert len(result["scenarios"]) == 2
        assert len(result["cerberus"]) == 1

    def test_root_level_pages_get_their_own_section(self):
        pages = [
            {"slug": "_root", "title": "Home", "summary": "x"},
            {"slug": "installation", "title": "Install", "summary": "y"},
            {"slug": "debugging", "title": "Debug", "summary": "z"},
            {"slug": "scenarios/pod", "title": "Pod", "summary": "w"},
        ]
        result = group_by_section(pages)
        # Root + bare-slug pages all live in same section name
        assert "scenarios" in result
        # Root section contains 3 entries
        root_section = result.get("/", []) or result.get("root", [])
        assert len(root_section) == 3 or "/" in result

    def test_pages_within_section_sorted_alphabetically_by_slug(self):
        pages = [
            {"slug": "scenarios/zone-outage", "title": "Z", "summary": "x"},
            {"slug": "scenarios/aurora-disruption", "title": "A", "summary": "y"},
            {"slug": "scenarios/pod-scenario", "title": "P", "summary": "z"},
        ]
        result = group_by_section(pages)
        slugs = [p["slug"] for p in result["scenarios"]]
        assert slugs == sorted(slugs)


# ─────────────────────────────────────────────────────────────────────────────
# render_index — produce the markdown
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderIndex:
    def test_top_level_structure(self):
        grouped = {
            "scenarios": [
                {"slug": "scenarios/pod-scenario", "title": "Pod Scenarios",
                 "summary": "Kills pods."},
            ],
        }
        out = render_index(grouped, project_name="krkn-chaos.dev")
        assert "# krkn-chaos.dev" in out
        assert "## scenarios" in out
        assert "[Pod Scenarios](scenarios/pod-scenario)" in out
        assert "Kills pods." in out

    def test_sections_sorted_alphabetically(self):
        grouped = {
            "z-section": [{"slug": "z/x", "title": "ZX", "summary": "."}],
            "a-section": [{"slug": "a/x", "title": "AX", "summary": "."}],
            "m-section": [{"slug": "m/x", "title": "MX", "summary": "."}],
        }
        out = render_index(grouped, project_name="X")
        a_pos = out.find("## a-section")
        m_pos = out.find("## m-section")
        z_pos = out.find("## z-section")
        assert a_pos < m_pos < z_pos

    def test_handles_empty_summary_gracefully(self):
        grouped = {
            "x": [{"slug": "x/page", "title": "Page", "summary": ""}],
        }
        out = render_index(grouped, project_name="X")
        # No trailing colon or em-dash with empty summary
        assert "[Page](x/page)" in out
        assert "[Page](x/page) — " not in out  # no orphan delimiter

    def test_header_includes_generated_marker(self):
        out = render_index({}, project_name="X")
        assert "auto-generated" in out.lower() or "do not edit" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# build_index — full integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildIndex:
    def test_end_to_end_writes_index_md(self, tmp_path: Path):
        # Set up fixture
        scen_dir = tmp_path / "content/en/docs/scenarios/pod-scenario"
        scen_dir.mkdir(parents=True)
        (scen_dir / "_index.md").write_text(dedent("""\
            ---
            title: Pod Scenarios
            description: Disrupts pods matching label or namespace.
            ---

            Body.
            """))

        out_dir = tmp_path / ".docs-sync-digest"
        out_dir.mkdir()

        result = build_index(
            content_root=tmp_path / "content/en/docs",
            output_dir=out_dir,
            project_name="krkn-chaos.dev",
        )

        index_path = out_dir / "INDEX.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "# krkn-chaos.dev" in content
        assert "Pod Scenarios" in content
        assert "Disrupts pods" in content
        # Returns metadata about what was written
        assert "page_count" in result
        assert result["page_count"] >= 1

    # === Sub-task 4 inspection finding I1 — skip tab files ===

    def test_tab_files_excluded_from_index(self, tmp_path: Path):
        # Tab files (e.g. _tab-krkn.md) are variants of a parent page.
        # The parent's index entry leads the agent to the directory; tab
        # files inside don't need their own top-level index entries.
        # Including them produced ugly slug-as-title entries because tab
        # files often have no title frontmatter and start with H4 (####).
        # Earned from sub-task 4 critical-lens inspection finding I1.
        scen = tmp_path / "content/en/docs/scenarios/pod-scenario"
        scen.mkdir(parents=True)
        (scen / "_index.md").write_text(
            "---\ntitle: Pod\ndescription: Kills pods.\n---\nbody"
        )
        (scen / "_tab-krkn.md").write_text("#### Example Config\nbody")
        (scen / "_tab-krkn-hub.md").write_text("#### Hub usage\nbody")

        out = tmp_path / ".docs-sync-digest"
        out.mkdir()
        result = build_index(tmp_path / "content/en/docs", out, "X")

        index_text = (out / "INDEX.md").read_text()
        # Parent entry present
        assert "[Pod](scenarios/pod-scenario)" in index_text
        # Tab variants NOT present as separate entries
        assert "_tab-krkn" not in index_text
        assert "tab-krkn-hub" not in index_text
        # Page count reflects parent only, not tabs
        assert result["page_count"] == 1

    def test_deterministic(self, tmp_path: Path):
        scen_dir = tmp_path / "content/en/docs/scenarios/pod-scenario"
        scen_dir.mkdir(parents=True)
        (scen_dir / "_index.md").write_text("---\ntitle: P\n---\n\nbody\n")

        out = tmp_path / ".docs-sync-digest"
        out.mkdir()

        build_index(tmp_path / "content/en/docs", out, "X")
        first = (out / "INDEX.md").read_text()

        build_index(tmp_path / "content/en/docs", out, "X")
        second = (out / "INDEX.md").read_text()

        assert first == second
