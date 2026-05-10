# Skill: draft_new_scenario

Triggered when the krkn-hub extractor produces a `Scenario` in
`change_set.scenarios_added` — i.e., a scenario that exists in the head
digest but not the base.

## Goal

Produce the prose body of a new `_index.md` page for the scenario. The
parameter-table tabs (`_tab-krkn-hub.md`, `_tab-krknctl.md`) are NOT this
skill's job — Slice 1's mechanical regen handles them deterministically.

## Hard rules

1. Output is the markdown body ONLY. No frontmatter (handled separately).
2. No H1 (`# Title`) — Hugo derives the page title from frontmatter.
3. Maximum 2 levels of nesting (`## Heading`, `### Subheading`). No `#### `.
4. ONLY reference `scenario_type` strings present in TAXONOMY.json
   (passed to the prompt). Inventing one is a hallucination → reject.
5. ONLY reference CLI flags present in TAXONOMY.json. Same rule.
6. No Hugo shortcodes (`{{< ... >}}`, `{{% ... %}}`, `<krkn-*>`). The
   site author can add them later if appropriate.
7. Length: 200-700 words. Outside this range → reject.

## Required sections

Every scenario page must have these `##` sections, in this order:

1. **Overview** (or no heading — first paragraph): one sentence describing
   what the scenario does.
2. `## Why this matters`: 2-4 sentences on the resilience question this
   scenario answers.
3. `## Use cases`: bulleted list of concrete situations where this
   scenario is relevant.
4. `## Configuration`: a brief paragraph pointing to the per-tool tabs
   (krkn / krkn-hub / krknctl) that follow this page. Do NOT inline
   the parameter table — that's mechanical.

## Anti-rationalization (block predictable shortcuts)

- "I'll just describe what the scenario_type implies" → No. Use the
  parameter list to ground the description in actual behavior.
- "I'll add a fictional Use Case to make the section feel fuller" → No.
  Three real, parameter-grounded use cases > five made-up ones.
- "The params don't have docstrings, I'll guess defaults" → No. If a
  default isn't given, omit it from the prose. Mechanical regen will
  show it in the table.

## Voice samples

The prompt includes 2-3 existing scenario `_index.md` files as voice
samples. Match THEIR prose density, paragraph length, and tone — don't
add personality.
