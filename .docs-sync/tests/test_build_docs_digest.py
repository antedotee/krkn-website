"""Tests for .docs-sync/digest/build_docs_digest.py

These run BEFORE the implementation exists — they should fail first (RED),
then pass after implementation (GREEN). Standard TDD discipline.
"""
import json
from pathlib import Path
from textwrap import dedent

import pytest

from digest.build_docs_digest import (
    strip_frontmatter,
    strip_shortcodes,
    normalize_whitespace,
    process_file,
    build_per_page,
    slug_from_path,
)


# ─────────────────────────────────────────────────────────────────────────────
# strip_frontmatter
# ─────────────────────────────────────────────────────────────────────────────

class TestStripFrontmatter:
    def test_removes_yaml_block_at_top(self):
        text = dedent("""\
            ---
            title: Pod Scenarios
            weight: 3
            ---

            Body content here.
            """)
        assert strip_frontmatter(text).strip() == "Body content here."

    def test_no_frontmatter_returns_unchanged(self):
        text = "Just body content, no frontmatter.\n"
        assert strip_frontmatter(text) == text

    def test_frontmatter_with_complex_values(self):
        text = dedent("""\
            ---
            title: "Quoted: with colon"
            tags:
              - foo
              - bar
            ---
            Body.
            """)
        assert "title:" not in strip_frontmatter(text)
        assert "Body." in strip_frontmatter(text)

    def test_only_strips_top_block_not_horizontal_rules_in_body(self):
        text = dedent("""\
            ---
            title: X
            ---

            Section 1

            ---

            Section 2 (this --- is a horizontal rule, not frontmatter)
            """)
        result = strip_frontmatter(text)
        assert "title:" not in result
        # The horizontal rule between sections is body content, must remain
        assert "Section 1" in result
        assert "Section 2" in result

    def test_handles_no_trailing_newline_after_close(self):
        text = "---\ntitle: X\n---\nBody"
        assert strip_frontmatter(text) == "Body"


# ─────────────────────────────────────────────────────────────────────────────
# strip_shortcodes
# ─────────────────────────────────────────────────────────────────────────────

class TestStripShortcodes:
    def test_strips_alert_shortcode_block(self):
        text = dedent("""\
            Before.
            {{% alert title="Note" %}}
            Important info.
            {{% /alert %}}
            After.
            """)
        result = strip_shortcodes(text)
        assert "{{%" not in result
        assert "{{<" not in result
        # Inner content of alert is preserved (it's still useful prose)
        assert "Important info." in result
        assert "Before." in result
        assert "After." in result

    def test_strips_angle_bracket_shortcode(self):
        text = "Before {{< include file=\"x.md\" >}} After."
        result = strip_shortcodes(text)
        assert "{{<" not in result
        assert "Before" in result and "After." in result

    def test_strips_paired_html_shortcode(self):
        # krkn-hub-scenario is a custom Hugo HTML shortcode
        text = dedent("""\
            ## Use cases
            <krkn-hub-scenario id="pod-scenarios">

            1. Deleting a pod
            - Use case detail

            </krkn-hub-scenario>

            More text.
            """)
        result = strip_shortcodes(text)
        assert "<krkn-hub-scenario" not in result
        assert "</krkn-hub-scenario>" not in result
        # Inner content preserved
        assert "Deleting a pod" in result
        assert "More text." in result

    def test_handles_self_closing_shortcode(self):
        text = "Top.\n{{< youtube id=\"xyz\" />}}\nBottom."
        result = strip_shortcodes(text)
        assert "{{<" not in result
        assert "Top." in result and "Bottom." in result

    def test_no_shortcodes_returns_unchanged(self):
        text = "Plain markdown.\n\n## Heading\n\nParagraph."
        assert strip_shortcodes(text) == text

    # === Run 1 inspection finding A4 — earned from real corpus ===

    def test_strips_krkn_namespace_shortcode(self):
        # Real shortcode found in the krkn-chaos corpus that was leaking
        text = "Before <krkn-namespace>foo</krkn-namespace> after."
        result = strip_shortcodes(text)
        assert "<krkn-namespace" not in result
        assert "</krkn-namespace>" not in result
        assert "foo" in result  # inner content preserved

    def test_strips_krkn_sa_shortcode(self):
        # Another real shortcode that was leaking
        text = "Before <krkn-sa>service-account</krkn-sa> after."
        result = strip_shortcodes(text)
        assert "<krkn-sa" not in result
        assert "</krkn-sa>" not in result
        assert "service-account" in result

    def test_strips_arbitrary_krkn_prefixed_shortcode(self):
        # Future-proofing: any <krkn-*> tag should be stripped (pattern-based,
        # not name-enumerated). Earned from inspection: regex was fragile
        # because it hardcoded specific names.
        text = "<krkn-future-thing attr=\"x\">inside</krkn-future-thing>"
        result = strip_shortcodes(text)
        assert "<krkn-" not in result
        assert "inside" in result

    def test_does_not_strip_non_krkn_html_tags(self):
        # We must not over-match. Standard HTML stays.
        text = "<a href=\"x\">link</a> <strong>bold</strong> <details>d</details>"
        result = strip_shortcodes(text)
        # All preserved — these are real HTML, not Hugo shortcodes
        assert "<a href=\"x\">" in result
        assert "<strong>" in result
        assert "<details>" in result


# ─────────────────────────────────────────────────────────────────────────────
# normalize_whitespace
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeWhitespace:
    def test_collapses_3_or_more_blank_lines_to_2(self):
        text = "A\n\n\n\n\nB"
        # Two blank lines = paragraph break, preserved. More collapses.
        assert normalize_whitespace(text) == "A\n\nB"

    def test_strips_trailing_whitespace_per_line(self):
        text = "A   \nB\t\nC"
        assert normalize_whitespace(text) == "A\nB\nC"

    def test_strips_leading_and_trailing_blank_lines(self):
        text = "\n\n\nA\nB\n\n\n"
        assert normalize_whitespace(text) == "A\nB"


# ─────────────────────────────────────────────────────────────────────────────
# process_file (integration of all three above)
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessFile:
    def test_full_pipeline_on_realistic_page(self, tmp_path: Path):
        page = tmp_path / "scenarios" / "pod-scenario" / "_tab-krkn.md"
        page.parent.mkdir(parents=True)
        page.write_text(dedent("""\
            ---
            title: Pod Scenario - krkn
            weight: 1
            ---

            #### Example Config

            Example scenario file:
            - [pod.yml](https://github.com/krkn-chaos/krkn/blob/main/scenarios/pod.yml)

            ```yaml
            kraken:
              chaos_scenarios:
                - pod_disruption_scenarios:
                  - path/to/scenario.yaml
            ```

            {{% alert title="Note" %}}
            You can specify multiple files.
            {{% /alert %}}
            """))

        result = process_file(page)

        # Frontmatter gone
        assert "title:" not in result
        # Shortcode markers gone, inner text preserved
        assert "{{%" not in result
        assert "You can specify multiple files." in result
        # Code block content survives — it's plain text after stripping
        assert "pod_disruption_scenarios" in result
        # Body prose survives
        assert "Example Config" in result


# ─────────────────────────────────────────────────────────────────────────────
# slug_from_path
# ─────────────────────────────────────────────────────────────────────────────

class TestSlugFromPath:
    def test_uses_parent_dir_name_for_index(self):
        # content/en/docs/scenarios/pod-scenario/_index.md → pod-scenario
        path = Path("content/en/docs/scenarios/pod-scenario/_index.md")
        content_root = Path("content/en/docs")
        assert slug_from_path(path, content_root) == "scenarios/pod-scenario"

    def test_combines_dir_and_tab_name_for_tabs(self):
        path = Path("content/en/docs/scenarios/pod-scenario/_tab-krkn.md")
        content_root = Path("content/en/docs")
        assert slug_from_path(path, content_root) == "scenarios/pod-scenario--tab-krkn"

    def test_uses_basename_for_standalone_md(self):
        path = Path("content/en/docs/getting-started/quickstart.md")
        content_root = Path("content/en/docs")
        assert slug_from_path(path, content_root) == "getting-started/quickstart"

    # === Run 1 inspection finding D2 — root _index.md edge case ===

    def test_root_index_md_uses_root_placeholder(self):
        # content/en/docs/_index.md was producing slug = "" → hidden file
        # named ".txt". Must produce a stable non-empty slug.
        path = Path("content/en/docs/_index.md")
        content_root = Path("content/en/docs")
        assert slug_from_path(path, content_root) == "_root"

    def test_root_tab_file_handled(self):
        # If somehow a tab file ends up at root (unlikely but possible),
        # it should not produce an empty-prefix slug.
        path = Path("content/en/docs/_tab-foo.md")
        content_root = Path("content/en/docs")
        result = slug_from_path(path, content_root)
        assert result  # non-empty
        assert not result.startswith("/")
        assert not result.startswith("-")


# ─────────────────────────────────────────────────────────────────────────────
# build_per_page (full integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPerPage:
    def _make_fixture(self, root: Path):
        """Create a minimal content tree for testing."""
        scenario = root / "content/en/docs/scenarios/pod-scenario"
        scenario.mkdir(parents=True)
        (scenario / "_index.md").write_text(
            "---\ntitle: Pod\n---\n\nPod scenarios overview.\n"
        )
        (scenario / "_tab-krkn.md").write_text(
            "---\ntitle: krkn\n---\n\n```yaml\nkey: value\n```\n"
        )
        nontarget = root / "content/en/blog/post.md"
        nontarget.parent.mkdir(parents=True)
        nontarget.write_text("---\ntitle: Blog post\n---\n\nNot a scenario.\n")

    def test_emits_per_page_files_only_for_docs_tree(self, tmp_path: Path):
        self._make_fixture(tmp_path)
        out = tmp_path / ".docs-sync-digest"

        result = build_per_page(
            content_root=tmp_path / "content/en/docs",
            output_dir=out,
        )

        # Two doc files processed
        per_page = out / "PER_PAGE"
        assert (per_page / "scenarios/pod-scenario.txt").exists()
        assert (per_page / "scenarios/pod-scenario--tab-krkn.txt").exists()
        # Blog post NOT included (outside content/en/docs)
        assert not (per_page / "blog/post.txt").exists()
        # Returns a dict mapping slug → metadata
        assert "scenarios/pod-scenario" in result
        assert "char_count" in result["scenarios/pod-scenario"]

    def test_idempotent_when_content_unchanged(self, tmp_path: Path):
        self._make_fixture(tmp_path)
        out = tmp_path / ".docs-sync-digest"

        r1 = build_per_page(tmp_path / "content/en/docs", out)
        r2 = build_per_page(tmp_path / "content/en/docs", out)

        # Same input → same output (deterministic, no nondeterminism in outputs)
        assert r1 == r2
        # Files exist with same content
        f = out / "PER_PAGE" / "scenarios/pod-scenario.txt"
        content = f.read_text()
        # Re-running doesn't corrupt
        build_per_page(tmp_path / "content/en/docs", out)
        assert f.read_text() == content

    def test_output_files_are_plain_text_no_markup(self, tmp_path: Path):
        self._make_fixture(tmp_path)
        out = tmp_path / ".docs-sync-digest"
        build_per_page(tmp_path / "content/en/docs", out)

        text = (out / "PER_PAGE" / "scenarios/pod-scenario.txt").read_text()
        assert "title:" not in text  # frontmatter stripped
        assert "---" not in text     # frontmatter delimiters stripped
        assert "Pod scenarios overview." in text
