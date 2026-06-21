---
name: pr-monitor
description: "Monitor pending feature PR submissions across repos. Use when: user wants to log PRs for a feature, check PR status, monitor CI failures, review comments, rebase needs, analyze failures, list pending features, remove merged features, checkout PR branches, rebase PRs, draft review request message, PR monitoring, feature submission tracking."
argument-hint: "Action (log/status/check/remove/list/checkout/rebase/review-msg) and optionally: feature name or PR URLs"
---

# PR Monitor

Track and monitor sets of GitHub PRs grouped by feature. Data persists in `/memories/pr-monitor-features.md` (user-scoped — shared across all workspaces and conversations).

## Memory File Format

The tracking file at `/memories/pr-monitor-features.md` uses this structure:

```markdown
# Pending Feature PRs

## <Feature Name>
- **Logged:** YYYY-MM-DD
- **Description:** <one-line summary>
- **PRs:**
  | Owner | Repo | PR# | Branch | Title | Status |
  |-------|------|-----|--------|-------|--------|
  | Elmnt-Internal | repo-name | 123 | branch-name | PR title | pending |
```

`Status` column is a local cache updated on each check: `pending`, `merged`, `ci-fail`, `changes-requested`, `approved`, `needs-rebase`, `closed`.

The `Branch` and `Title` columns are populated once at log time and reused by all other commands — no need to re-fetch them from GitHub on every invocation.

## Commands

### 1. Log a Feature (`log`)

User provides a feature name and one or more PR URLs or `owner/repo#number` references.

**Procedure:**
1. Parse each PR reference to extract owner, repo, PR number.
2. For each PR, use `github-pull-request_issue_fetch` to get title, head branch, and current state.
3. Read the memory file with the memory tool (`/memories/pr-monitor-features.md`). Create it if it doesn't exist.
4. Append a new feature section with today's date and all PRs, **including the branch name and title** in the table — these are cached for later use by `checkout`, `status`, and other commands.
5. Write back with the memory tool.
6. Confirm with a summary table.

### 2. Check All Features (`status` / `check`)

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. For each PR in every feature, gather status **in parallel where possible**:
   a. Use `github-pull-request_issue_fetch` to get current PR state (open/merged/closed), mergeable status, and whether it's behind base branch (needs rebase).
   b. Use `github-pull-request_pullRequestStatusChecks` to get CI status and review state (approvals, requested changes, unresolved comments).
3. Determine each PR's status:
   - **merged** — PR state is merged
   - **closed** — PR state is closed (not merged)
   - **ci-fail** — Any check run has failed
   - **changes-requested** — Reviewer requested changes
   - **needs-rebase** — PR is not mergeable or is behind base branch
   - **approved** — All required reviews approved, CI green
   - **pending** — Open, CI running or no reviews yet
4. Update the status column in the memory file.
5. Present a per-feature summary table:

   **Feature: <name>** (logged YYYY-MM-DD)
   | Repo | PR# | Title | CI | Reviews | Mergeable | Status |
   |------|-----|-------|----|---------|-----------|--------|

   Use the **Title** from the memory file cache; only fetch PR state, CI, and review data from GitHub.

   The **PR#** column must be rendered as a clickable markdown link to the PR on GitHub, using the format `[#<number>](https://github.com/<Owner>/<Repo>/pull/<number>)`.

6. Flag any items needing attention (CI failures, review comments, rebase needed).
7. If **all PRs** in a feature are `merged`, notify the user and offer to remove the entry.

### 3. Analyze a CI Failure (`analyze`)

When a PR has a CI failure:

**Procedure:**
1. Use `github-pull-request_pullRequestStatusChecks` to get the failed check details and failure logs.
2. Read the failure log content.
3. Categorize the failure:
   - **Build error** — compilation failure
   - **Test failure** — specific test(s) failed
   - **Lint/style** — formatting or lint violations
   - **Infrastructure** — flaky CI, timeout, runner issue
4. Present:
   - Failed check name and workflow
   - Relevant failure log excerpt
   - Root cause analysis
   - Suggested fix or next steps
5. If the failure appears to be infrastructure/flaky, suggest re-running CI.
6. **Re-triggering CI:** Individual failed workflows cannot be re-run in isolation — all workflows must be re-triggered fresh because they have inherited dependencies between them. Use `gh pr close <PR#> && gh pr reopen <PR#>` to restart the full CI pipeline. Never suggest re-running a single workflow.

### 4. List Features (`list`)

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. Present a summary of all tracked features with PR counts and high-level status.

### 5. Remove a Feature (`remove`)

**Procedure:**
1. Confirm the feature name with the user.
2. Read the memory file, remove the feature section.
3. Write back with the memory tool.
4. Confirm removal.

### 6. Checkout Feature Branches (`checkout`)

Checkout the local workspace repos to the PR branches for a tracked feature, pulling latest from GitHub.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If no feature name is given and multiple features exist, ask the user which feature to checkout.
3. Read the **Branch** column directly from the memory file — branch names are already cached from the `log` command. No GitHub fetch needed.
4. Find the workspace manifest XML (`.repo/manifests/` directory) to map repo names to local paths via `<project name="..." path="...">` entries.
5. For each repo **with a PR branch**, fetch, checkout, and pull to ensure it's synced to the latest state:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git fetch origin {BRANCH} && git checkout {BRANCH} && git pull origin {BRANCH}
   ```
6. For all other repos in the workspace manifest that **do not** have a PR branch in this feature, checkout `main` and pull:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git checkout main && git pull origin main
   ```
   This ensures the entire workspace is in a consistent state — feature branches where needed, clean `main` everywhere else.
7. Present a summary table:
   | Repo | Local Path | Branch | Status |
   |------|-----------|--------|--------|
   | repo-name | path | feature-branch | OK / Error |
   | other-repo | path | main | OK / Error |
8. Report any repos that failed to checkout (e.g., local uncommitted changes blocking the switch).

### 7. Rebase Feature Branches (`rebase`)

Rebase all PRs in a feature (or all features) that have `needs-rebase` status onto the latest `main`.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If a feature name is given, scope to that feature. Otherwise, operate across all features.
3. Collect all PRs with status `needs-rebase`.
4. If no PRs need rebasing, inform the user and stop.
5. Find the workspace manifest XML (`.repo/manifests/` directory) to map repo names to local paths via `<project name="..." path="...">` entries.
6. For each PR that needs rebasing, sequentially:
   a. Checkout the branch:
      ```bash
      cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git fetch origin {BRANCH} && git checkout {BRANCH} && git pull origin {BRANCH}
      ```
   b. Fetch latest main and rebase:
      ```bash
      git fetch origin main && git rebase origin/main
      ```
   c. If the rebase has **conflicts**, abort and report:
      ```bash
      git rebase --abort
      ```
      Record the repo and conflicting files, then move on to the next PR.
   d. If the rebase succeeds cleanly, force-push:
      ```bash
      git push --force-with-lease origin {BRANCH}
      ```
   e. Return the repo to `main`:
      ```bash
      git checkout main
      ```
7. Present a summary table:
   | Feature | Repo | PR# | Branch | Result |
   |---------|------|-----|--------|--------|
   | feature | repo | 123 | branch | Rebased & pushed / Conflicts (files) / Error |
8. For any PRs that were successfully rebased, update their status in the memory file to `pending` (CI will re-run).
9. For PRs with conflicts, keep status as `needs-rebase` and list the conflicting files so the user can resolve manually.

### 8. Draft Review Request Message (`review-msg`)

Draft a message asking someone to review the PRs for a feature.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If a feature name is given, scope to that feature. Otherwise, if multiple features exist, ask the user which feature.
3. If no reviewer name is provided, ask the user for the reviewer's name.
4. Collect the GitHub PR URLs for all non-merged PRs in the feature: `https://github.com/{Owner}/{Repo}/pull/{PR#}`.
5. Output the following message for the user to copy:

```
Hi <name>,
Can you please review these PRs?
<each PR URL on its own line>
Thanks
```

### 9. Auto-Cleanup

During any `status`/`check` operation:
- If all PRs in a feature are `merged`, ask the user: "All PRs for **<feature>** are merged. Remove from tracking?"
- On confirmation, remove the feature entry from memory.

## Parsing PR References

Accept these formats:
- Full URL: `https://github.com/Owner/Repo/pull/123`
- Short: `Owner/Repo#123`
- Repo-only (assumes `Elmnt-Internal`): `Repo#123`

Extract: owner (default `Elmnt-Internal`), repo name, PR number.

## Status Priority

When multiple conditions apply, report the highest-priority status:
1. `merged`
2. `closed`
3. `ci-fail`
4. `changes-requested`
5. `needs-rebase`
6. `approved`
7. `pending`

## Rules

- Always use the memory tool to read/write `/memories/pr-monitor-features.md`.
- Never delete a feature entry without user confirmation.
- When checking status, fetch all PRs even if some have cached status — always get fresh data.
- Use `gh` CLI as fallback if the GitHub tools don't return sufficient detail (e.g., for failure logs).
- Present results as markdown tables for readability.
- When a PR has unresolved review comments, mention the count and latest commenter.
