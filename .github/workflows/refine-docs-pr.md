---
name: Refine docs PR on @mention
description: React to @krkn-docs-sync mentions on automated docs PRs and push refinements
on:
  issue_comment:
    types: [created]

if: >
  github.event.issue.pull_request != null &&
  contains(github.event.issue.labels.*.name, 'automated-docs')

permissions:
  contents: read
  pull-requests: read
  issues: read

engine: copilot
strict: true

network:
  allowed:
    - defaults
    - github

tools:
  github:
    toolsets: [default]
  edit:
  bash:
    - "git diff *"
    - "git log *"
    - "git show *"
    - "find . *"
    - "cat *"
    - "grep *"

timeout-minutes: 15

safe-outputs:
  push-to-pull-request-branch:
    target: "triggering"
    title-prefix: "[docs-sync] "
    labels: [automated-docs]
    if-no-changes: "warn"
  add-comment:
    target: "triggering"
---

# Refine docs PR

A reviewer mentioned `@krkn-docs-sync` on PR #${{ github.event.issue.number }} of `${{ github.repository }}`.

## Triggering PR

- **Number:** #${{ github.event.issue.number }}
- **Title:** ${{ github.event.issue.title }}
- **Repository:** ${{ github.repository }}
- **Triggering comment ID:** ${{ github.event.comment.id }}

## How to read the comment

You do NOT have the comment text pre-substituted into this prompt (security). The workflow runs on EVERY comment to an `automated-docs`-labeled PR, so your VERY FIRST action must be to fetch the comment and check whether it actually mentions the bot:

1. Call the `github` MCP `issue_comment_read` tool with `owner` = the org-part of `${{ github.repository }}`, `repo` = the repo-part, `comment_id` = `${{ github.event.comment.id }}`.
2. Read the returned `body` field.
3. **IF the body does NOT contain the literal substring `@krkn-docs-sync`, STOP IMMEDIATELY. Do nothing. Do not call any safe-output. Exit.**
4. Otherwise read the returned `user.login` field for the acknowledgement reply later.

## Rules — read first

- DO NOT modify files that were not already in PR #${{ github.event.issue.number }}'s diff. If the comment asks for a change in a different file, reply with a clarifying question instead of acting.
- DO NOT touch `CLAUDE.md`, `hugo.yaml`, `layouts/`, `assets/`, or any file under `static/`.
- DO NOT push if the comment is ambiguous, contradictory, or asks for something outside docs scope. Reply with a question instead.
- DO NOT touch the original PR's body or title — only the file contents on the branch.

## Steps

### 1. Read the comment

Fetch the triggering comment as described in "How to read the comment" above. Extract:
- The comment body (the actual instruction from the reviewer)
- The author login (for the acknowledgement reply)

### 2. Read the PR's current state

Use the `github` toolset to:
- Fetch PR #${{ github.event.issue.number }} with `pull_request_read` — note the branch name and head SHA.
- Fetch the PR diff with `list_files` — record exactly which files the PR currently touches.
- Read the PR body. Find the line beginning with `**Triggered by:**` — that's the upstream PR link. Keep it for context if needed.

### 3. Interpret the comment

Identify what the reviewer wants. Common shapes:

- "also add a CLI example" → add a code-fenced example below the parameter description.
- "fix the table — the default is X not Y" → correct the value in the existing table.
- "remove this whole section, we don't use it" → delete the named section.
- "reword to match the existing tone" → rewrite without changing meaning.

If the request is unclear: STOP. Use `add_comment` to ask a clarifying question. Do not push.

### 4. Apply the change

Use `edit` on files already in the PR's diff (Step 2). If the request explicitly asks to touch a NEW file inside `content/en/docs/krknctl/` AND that file already exists, that is allowed. Anywhere else, ask first.

### 5. Push the change

Call the `push_to_pull_request_branch` MCP tool from the safe-outputs server. Pushing will validate the PR's `title-prefix` (`[docs-sync] `) and `automated-docs` label before accepting the push.

### 6. Acknowledge

Call `add_comment` with a one-paragraph summary of what changed. Example:

> Applied your suggestion — added a `krknctl run --namespace my-app` example below the `--namespace` row in the parameter table. Let me know if you want it phrased differently.

## If you cannot act

If you reach this step without having pushed AND without having asked a clarifying question, something went wrong. Call `add_comment` once with a short explanation (e.g. "I couldn't determine which file you wanted to change — could you point me to a specific file or section?") and stop.
