# AGENTS.md (.docs-sync/AGENTS.md)

> Earned constraints. Each line traces to a real prior failure or hard
> external constraint. New entries get added only via the harvest workflow
> (Slice 3) — every rule must cite the inspection finding that produced it.

## Hard rules — never break these
- Hugo build (`hugo --quiet --minify`) MUST pass before opening any PR.
- Auto-regenerated regions live ONLY between `<!-- AUTO:START id="..." -->`
  and `<!-- AUTO:END -->` markers. Never edit outside markers.
- Never modify `layouts/`, `assets/`, `netlify/`, `static/`. Hand-tuned only.
- Scenario page directories use kebab-case (`pod-scenario/`, not `pod_scenario/`).

## Earned from Slice 0a (docs digest builder)

- Hugo shortcode regexes are pattern-based, not name-enumerated. Use
  `<krkn-[a-z][a-z0-9-]*` for the krkn-chaos namespace. *Earned:
  Slice 0a/1 finding A4 — hardcoding `krkn-hub-scenario` missed
  `<krkn-namespace>` and `<krkn-sa>` in the corpus.*

- Slug generation must always produce a non-empty value. Root `_index.md`
  → `_root`. *Earned: Slice 0a/1 finding D2 — empty slug produced
  hidden `.txt` file.*

- CLI-flag extraction must reject trailing-hyphen tokens (`--aws-` is never
  a real flag). *Earned: Slice 0a/2 finding T2.*

- CLI-flag extraction must NOT match CSS custom properties — exclude
  `var(--foo)` and `--foo:` declaration patterns. *Earned: Slice 0a/2
  finding T1.*

- Coverage matching uses an explicit plural-normalization allowlist, not
  generic stemming. Generic stemming mangled `chaos` → `chao`. Add to
  allowlist only when inspection finds a real false-negative pair.
  *Earned: Slice 0a/3 finding C1 + Run 2 regression.*

- Tab files (`_tab-*.md`) are NOT separate index entries. They're page
  variants reachable via the parent's directory. *Earned: Slice 0a/4
  finding I1.*

- Summary extraction must apply the same shortcode/HTML-tag stripping
  that PER_PAGE digests do. *Earned: Slice 0a/4 finding I5.*

## Earned from Slice 0.5 (AUTO marker migration)

- Markdown table parsers must accept tables WITHOUT leading/trailing
  pipes — the krkn-chaos corpus uses pipeless form: `Parameter |
  Description` (no leading `|`) is valid. *Earned: Slice 0.5 M1.*

- File-rewrite utilities must preserve trailing newlines exactly.
  `splitlines() + "\n".join()` collapses trailing blank lines. Capture
  the trailing-newline run length and restore it. *Earned: Slice 0.5 M2.*

- `strip_shortcodes` must also strip HTML comments. AUTO markers (and
  any other `<!-- ... -->` meta) are tooling, not content. *Earned:
  Slice 0.5 M3.*

- Parameter tables use one of 6 documented header shapes; first column
  is one of `Parameter`, `Option`, `Argument`. The `Component | Description
  | Working` table on `pod-scenario/_tab-krkn.md` is a scenarios catalog,
  not a parameter table — don't wrap it.

## Earned from Slice 0b (krkn-hub upstream digest)

- env.sh `SCENARIO_TYPE` assignments come in two forms in the krkn-hub
  corpus. Both must be parsed. *Earned: Slice 0b U1.*
    ```bash
    export SCENARIO_TYPE=${SCENARIO_TYPE:=pod_disruption_scenarios}  # bash-default
    export SCENARIO_TYPE="service_hijacking_scenarios"               # direct
    ```

## Anti-rationalization (per Addy's pattern)

- "I'll skip hugo validation, it's just a doc change" → No. Hugo errors
  silently 404 pages in prod. Always run.
- "The new field probably has a sensible default" → No. Read llms-full.txt
  for the actual default. No guessing.
- "I can write the whole prose section freshly" → No. Edit only the
  minimal span needed. Preserve existing voice.
- "This regex looks fine, ship it" → No. Run it against the real corpus
  before claiming green. The 11 bugs in Slices 0a-0b were all caught by
  post-implementation inspection on the actual files, not by tests alone.

## Pointers — lazy-load, do NOT preload

- Upstream schema reference: `<upstream>/.docs-sync-digest/llms-full.txt`
- Approved scenario_types/CLI flags/CRDs: `.docs-sync-digest/TAXONOMY.json`
- Per-page digests: `.docs-sync-digest/PER_PAGE/<slug>.txt`
- Site index: `.docs-sync-digest/INDEX.md`
- Repo paths config: `.docs-sync/repo-map.yaml`
- Inspection trail: `~/.../tasks/slice-0a-inspection.md`
