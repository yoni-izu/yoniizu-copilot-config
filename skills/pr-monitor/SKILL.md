---
name: pr-monitor
description: "Monitor pending feature PR submissions across repos. Use when: user wants to log PRs for a feature, check PR status, monitor CI failures, review comments, rebase needs, analyze failures, list pending features, remove merged features, checkout PR branches, rebase PRs, draft review request message, test a feature on the PLDM job queue, track PLDM/Palladium test results per feature, PR monitoring, feature submission tracking."
argument-hint: "Action (log/status/check/remove/list/checkout/rebase/review-msg/pldm-test/pldm-status) and optionally: feature ID/name or PR URLs"
---

# PR Monitor

Track and monitor sets of GitHub PRs grouped by feature. Data persists in `/memories/pr-monitor-features.md` (user-scoped — shared across all workspaces and conversations).

## Memory File Format

The tracking file at `/memories/pr-monitor-features.md` uses this structure:

```markdown
# Pending Feature PRs

## [N] <Feature Name>
- **Logged:** YYYY-MM-DD
- **Description:** <one-line summary>
- **PLDM Test:** not-tested
- **PLDM Details:** <display-name> · <run-url> · updated YYYY-MM-DD
- **PRs:**
  | Owner | Repo | PR# | Branch | Title | Status |
  |-------|------|-----|--------|-------|--------|
  | Elmnt-Internal | repo-name | 123 | branch-name | PR title | pending |
```

Each feature header carries a **running ID** in the form `## [N] <Feature Name>`. The ID is a stable, monotonically increasing integer assigned at log time — it lets the user reference a feature by ID (e.g. "status of #3", "checkout 5") instead of typing the full name. IDs are never renumbered when features are removed; the next `log` always uses `max(existing IDs) + 1`.

**Resolving a feature reference:** when the user names a feature, accept either its numeric ID (`3`, `#3`) or its name (case-insensitive substring match on the header). Always map the reference to a single feature before acting; if it's ambiguous, ask which one.

`Status` column is a local cache updated on each check: `pending`, `merged`, `ci-fail`, `changes-requested`, `approved`, `needs-rebase`, `closed`.

The **PLDM Test** field is a **feature-level** flag recording whether the feature's PR set was validated on the Palladium (PLDM) Job Queue. Values: `not-tested` (default), `queued`, `running`, `passed`, `failed`, `cancelled`. The **PLDM Details** line caches the submission's display name, the GitHub Actions run URL, and the date of the last status refresh; it is absent until a job is first submitted. See the [`pldm-job-queue`](../pldm-job-queue/SKILL.md) skill for the submission mechanics.

The `Branch` and `Title` columns are populated once at log time and reused by all other commands — no need to re-fetch them from GitHub on every invocation.

## Commands

### 1. Log a Feature (`log`)

User provides a feature name and one or more PR URLs or `owner/repo#number` references.

**Procedure:**
1. Parse each PR reference to extract owner, repo, PR number.
2. For each PR, use `github-pull-request_issue_fetch` to get title, head branch, and current state.
3. Read the memory file with the memory tool (`/memories/pr-monitor-features.md`). Create it if it doesn't exist.
4. Determine the next running ID: `max(existing feature IDs) + 1` (start at `1` if there are none).
5. Append a new feature section headed `## [N] <Feature Name>` with today's date and all PRs, **including the branch name and title** in the table — these are cached for later use by `checkout`, `status`, and other commands. Initialise the feature's **PLDM Test** field to `not-tested` (omit the `PLDM Details` line until a job is submitted).
6. Write back with the memory tool.
7. Confirm with a summary table, showing the assigned ID.

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
   > **Note:** CI does not run automatically — it starts only when someone comments `/ci` on the PR. A PR with **no check runs at all** has simply not had CI triggered yet; treat this as `pending` (not `ci-fail`), and note it may need a `/ci` comment. Do not use close/reopen to start CI.
4. Update the status column in the memory file.
5. **Refresh in-flight PLDM jobs.** For any feature whose `PLDM Test` is non-terminal (`queued` or `running`) and which has a `PLDM Details` line, re-check the job outcome using the `pldm-status` logic (step 10) and update `PLDM Test` + the `updated` date. Features that are `not-tested` or already terminal (`passed`/`failed`/`cancelled`) are left untouched.
6. Present a per-feature summary table:

   **Feature [N]: <name>** (logged YYYY-MM-DD)
   | Repo | PR# | Title | CI | Reviews | Mergeable | Status |
   |------|-----|-------|----|---------|-----------|--------|

   Use the **Title** from the memory file cache; only fetch PR state, CI, and review data from GitHub.

   Below the table, show the feature-level **PLDM Test** status (freshly refreshed above) — e.g. `PLDM: passed (pr-monitor-f3-…, updated 2026-08-05)` or `PLDM: not-tested`.

   The **PR#** column must be rendered as a clickable markdown link to the PR on GitHub, using the format `[#<number>](https://github.com/<Owner>/<Repo>/pull/<number>)`.

7. Flag any items needing attention (CI failures, review comments, rebase needed).
8. If **all PRs** in a feature are `merged`, the feature is complete — automatically remove it from tracking (no confirmation needed) and notify the user it was dropped.

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
6. **Triggering / re-triggering CI:** CI does **not** run automatically on PRs. It runs **only** when someone posts a `/ci` comment on the PR (e.g. `gh pr comment <PR#> --body '/ci'`). Closing and reopening the PR does **not** re-trigger CI. To restart the full pipeline (e.g. after a flaky failure or a new push), comment `/ci` again. Never suggest close/reopen for CI, and never suggest re-running a single workflow in isolation.

### 4. List Features (`list`)

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. Present a summary of all tracked features with their **ID**, PR counts, high-level status, and **PLDM Test** flag, ordered by ID.

### 5. Remove a Feature (`remove`)

**Procedure:**
1. Resolve the feature by ID or name and confirm with the user.
2. Read the memory file, remove the feature section. Do **not** renumber the remaining features — IDs stay stable.
3. Write back with the memory tool.
4. Confirm removal.

### 6. Checkout Feature Branches (`checkout`)

Checkout the local workspace repos to the PR branches for a tracked feature, pulling latest from GitHub.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If no feature ID/name is given and multiple features exist, ask the user which feature to checkout.
3. Read the **Branch** column directly from the memory file — branch names are already cached from the `log` command. No GitHub fetch needed.
4. Find the workspace manifest XML (`.repo/manifests/` directory) to map repo names to local paths via `<project name="..." path="...">` entries.
5. **Check workspace occupancy first.** The [workspace-manager](../workspace-manager/SKILL.md) store (`~/tasks/workspaces.json`) is the single source of truth for which workspace slots are in use. Determine the target workspace slot (from `{WORKSPACE_ROOT}`) and run `python3 ~/.copilot/skills/task-manager/tm.py workspaces` (or `python3 ~/.copilot/skills/workspace-manager/wm.py status <slot>`). If that slot is already held — by an active task (a review or another feature) **or** an ad-hoc hold — **warn the user before clobbering it** — checking out feature branches over a live review/dev workspace loses their context. Let the user confirm or pick a different workspace.
6. For each repo **with a PR branch**, fetch, checkout, and pull to ensure it's synced to the latest state:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git fetch origin {BRANCH} && git checkout {BRANCH} && git pull origin {BRANCH}
   ```
7. For all other repos in the workspace manifest that **do not** have a PR branch in this feature, checkout `main` and pull:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git checkout main && git pull origin main
   ```
   This ensures the entire workspace is in a consistent state — feature branches where needed, clean `main` everywhere else.
8. Present a summary table:
   | Repo | Local Path | Branch | Status |
   |------|-----------|--------|--------|
   | repo-name | path | feature-branch | OK / Error |
   | other-repo | path | main | OK / Error |
9. Report any repos that failed to checkout (e.g., local uncommitted changes blocking the switch).

### 7. Rebase Feature Branches (`rebase`)

Rebase all PRs in a feature (or all features) that have `needs-rebase` status onto the latest `main`.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If a feature ID/name is given, scope to that feature. Otherwise, operate across all features.
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
   | [N] feature | repo | 123 | branch | Rebased & pushed / Conflicts (files) / Error |
8. For any PRs that were successfully rebased, update their status in the memory file to `pending` (CI will re-run).
9. For PRs with conflicts, keep status as `needs-rebase` and list the conflicting files so the user can resolve manually.

### 8. Draft Review Request Message (`review-msg`)

Draft a message asking someone to review the PRs for a feature.

**Procedure:**
1. Read `/memories/pr-monitor-features.md`.
2. If a feature ID/name is given, scope to that feature. Otherwise, if multiple features exist, ask the user which feature.
3. If no reviewer name is provided, ask the user for the reviewer's name.
4. Collect the GitHub PR URLs for all non-merged PRs in the feature: `https://github.com/{Owner}/{Repo}/pull/{PR#}`.
5. Output the following message for the user to copy:

```
Hi <name>,
Can you please review these PRs?
<each PR URL on its own line>
Thanks
```

### 9. Test a Feature on the PLDM Job Queue (`pldm-test`)

Submit the feature's PR set to the Palladium (PLDM) Job Queue to validate it on emulation hardware. Because a feature is a **set of PRs across repos** and remote job-queue mode builds the **entire workspace manifest**, a single submission tests the whole feature set together (one branch per repo).

Delegates the submission mechanics to the **[`pldm-job-queue`](../pldm-job-queue/SKILL.md)** skill — read that skill and follow it for the actual `octl` invocation, flags, and image-type handling.

**Procedure:**
1. Resolve the feature by ID or name.
2. Collect the feature's **non-merged / non-closed** PRs — this is the set under test. If every PR is already merged, tell the user there's nothing to test and stop.
3. Ensure the workspace is on the feature branches and clean:
   - Run the `checkout` command (command 6) for this feature first, or confirm the workspace is already on these branches.
   - Remote job-queue mode **requires a clean workspace with every commit pushed**. Run `git status` across the involved repos; if there is uncommitted or unpushed work, stop and tell the user to push first.
4. Read the `pldm-job-queue` skill and submit a **remote** job from the workspace root:
   ```bash
   octl ws build --remote --target pldm
   ```
   - Use a **display name** derived from the feature so it's traceable in the Portal: `pr-monitor-f<N>-<slug>` (e.g. `pr-monitor-f3-pcie-inactive-ppw`).
   - Choose an image type annotated `(currently available for pldm job queue)`. Ask the user for image type / group / timeout if unclear (defaults: group `default`, timeout `20`).
   - To reuse a saved config instead of the TUI: `octl ws build --remote --target pldm --from-file <yaml>`.
5. Capture from octl's output the **GitHub Actions run URL** and the **display name**.
6. Update the feature in the memory file:
   - `PLDM Test: queued`
   - `PLDM Details: <display-name> · <run-url> · updated YYYY-MM-DD`
7. Report the submission and remind the user they'll receive an **email** with the workflow link and logs when the job runs, and can track it in the [Portal](https://retool.elmnt.local/app/portal/PLDM_job_q). Refresh the result later with `pldm-status`.

### 10. Refresh PLDM Test Status (`pldm-status`)

Update the cached PLDM job-queue result for a feature.

**Procedure:**
1. Resolve the feature by ID or name. If it has no `PLDM Details` line, tell the user it was never submitted to the job queue and stop.
2. From the stored run URL, check the GitHub Actions run outcome:
   ```bash
   gh run view <run-id> --repo Elmnt-Internal/ip.infra.devops.workflow-entry
   ```
   or look up the job on the Portal PLDM Job Queue page by **display name**.
3. Map the outcome to a **PLDM Test** value:
   - **queued** — job accepted, waiting in the queue
   - **running** — assigned to an EDK and executing
   - **passed** — job completed successfully
   - **failed** — build failed or the test reported failure
   - **cancelled** — job cancelled / EDK released
4. Update `PLDM Test` and the `updated` date in `PLDM Details` in the memory file.
5. Report the current status with the run URL. If `failed`, offer to open the run logs to help diagnose.

### 11. Auto-Cleanup

During any `status`/`check` operation:
- If all PRs in a feature are `merged`, the batch is done — automatically remove the feature entry from memory without asking.
- Notify the user: "All PRs for **<feature>** are merged — removed from tracking."

## Parsing PR References

Accept these formats:
- Full URL: `https://github.com/Owner/Repo/pull/123`
- Short: `Owner/Repo#123`
- Repo-only (assumes `Elmnt-Internal`): `Repo#123`

Extract: owner (default `Elmnt-Internal`), repo name, PR number.

## Referencing Features

A feature can be referenced by its **running ID** (`3`, `#3`) or by **name** (case-insensitive substring match on the header). Always resolve the reference to exactly one feature before acting; if ambiguous, ask the user to disambiguate.

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
- Each feature has a stable running ID in its header (`## [N] <Feature Name>`); assign `max(existing)+1` on `log` and never renumber on `remove`.
- Never delete a feature entry without user confirmation, **except** fully-merged batches, which are auto-removed during `status`/`check`.
- When checking status, fetch all PRs even if some have cached status — always get fresh data.
- Use `gh` CLI as fallback if the GitHub tools don't return sufficient detail (e.g., for failure logs).
- Present results as markdown tables for readability.
- When a PR has unresolved review comments, mention the count and latest commenter.
- For `pldm-test`, always delegate the actual job-queue submission to the [`pldm-job-queue`](../pldm-job-queue/SKILL.md) skill — do not re-implement the `octl` flow here. Track only the outcome (`PLDM Test` + `PLDM Details`) in the memory file.
- The **PLDM Test** field is feature-level. It is set by `pldm-test` (submit) and refreshed by `pldm-status`; `status`/`check` also auto-refreshes any feature still `queued`/`running` so in-flight jobs settle without a manual `pldm-status`. Terminal results (`passed`/`failed`/`cancelled`) and `not-tested` features are not re-queried. When a fully-merged feature is auto-removed, its PLDM tracking is dropped with it.
