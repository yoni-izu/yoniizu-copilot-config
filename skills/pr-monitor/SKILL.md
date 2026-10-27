---
name: pr-monitor
description: "Monitor pending feature PR submissions across repos. Use when: user wants to log PRs for a feature, check PR status, monitor CI failures, review comments, rebase needs, analyze failures, list pending features, remove merged features, checkout PR branches, rebase PRs, draft review request message, test a feature on the PLDM job queue, track PLDM/Palladium test results per feature, PR monitoring, feature submission tracking."
argument-hint: "Action (log/status/check/remove/list/checkout/rebase/review-msg/pldm-test/pldm-status) and optionally: feature ID/name or PR URLs"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# PR Monitor

Track and monitor sets of GitHub PRs grouped by feature. Canonical state lives in
`~/tasks/prs.db`, owned exclusively by `pm.py` (shared across workspaces).
Use `pm.py` for every read/write; do not edit the database or legacy memory file.
The original memoir and cache are backed up and imported once. Markdown is an
explicit import/export format, not a second writable source.

## Markdown interchange format

Legacy imports and Markdown exports use this structure; do not use a memory
tool to persist feature edits after database cutover:

```markdown
# Pending Feature PRs

## [N] <Feature Name>
- **Logged:** YYYY-MM-DD
- **Priority:** critical | high | medium | low
- **Summary:** <one sentence: what the feature does>
- **Jira:** KEY-123, KEY-456
- **Description:** <technical detail>
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

**Priority** is optional and defaults to `medium` when absent (the portal marks it as a default).
**Summary** and **Jira** are optional. When absent, `pm.py` derives them: the summary is the
description's first sentence (leading `[KEY]` tags stripped), and the Jira keys are every
`ABC-123` in the name and description (`SW-0` placeholders ignored). Write them explicitly
when logging a feature so the portal shows a curated one-liner instead of a derived one.

**Activity notes** are dated bullets anywhere in the block — `- **YYYY-MM-DD (tag):** text`
— plus the opening `- **Notes:** YYYY-MM-DD — text` field. Every one is kept (newest first);
the tag in parentheses is shown as a label. Append with `pm.py note` rather than hand-editing.

The `Branch` and `Title` columns are populated once at log time and reused by all other commands — no need to re-fetch them from GitHub on every invocation.

## Commands

### 1. Log a Feature (`log`)

User provides a feature name and one or more PR URLs or `owner/repo#number` references.

**Procedure:**
1. Parse each PR reference to extract owner, repo, PR number.
2. For each PR, use `github-pull-request_issue_fetch` to get title, head branch, and current state.
3. Read tracked features with `pm.py list` or `pm.py payload`.
4. Use `pm.py add-feature NAME -p REF ...` to allocate the stable ID transactionally.
5. Include the summary, Jira keys, priority and description using supported flags.
   The owner records branches/titles and initializes PLDM state.
6. Verify the resulting feature using `pm.py payload`; never write a Markdown block directly.
7. Confirm with a summary table, showing the assigned ID.

### 2. Check All Features (`status` / `check`)

**Procedure:**
1. Read `pm.py payload`.
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
4. Persist status observations through `pm.py refresh`, never direct file/database edits.
5. **Refresh in-flight PLDM jobs.** For any feature whose `PLDM Test` is non-terminal (`queued` or `running`) and which has a `PLDM Details` line, re-check the job outcome using the `pldm-status` logic (step 10) and update `PLDM Test` + the `updated` date. Features that are `not-tested` or already terminal (`passed`/`failed`/`cancelled`) are left untouched.
6. Present a per-feature summary table:

   **Feature [N]: <name>** (logged YYYY-MM-DD)
   | Repo | PR# | Title | CI | Reviews | Mergeable | Status |
   |------|-----|-------|----|---------|-----------|--------|

   Use the **Title** from the owner's cached observations; fetch current state, CI and reviews from GitHub.

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
1. Read `pm.py payload`.
2. Present a summary of all tracked features with their **ID**, PR counts, high-level status, and **PLDM Test** flag, ordered by ID.

### 5. Remove a Feature (`remove`)

**Procedure:**
1. Resolve the feature by ID or name and confirm with the user.
2. Use `pm.py drop-feature ID` to retire the feature without deleting history.
3. Verify the active payload. Do **not** renumber the remaining features.
4. Confirm removal.

### 6. Checkout Feature Branches (`checkout`)

Checkout the local workspace repos to the PR branches for a tracked feature, pulling latest from GitHub.

**Procedure:**
1. Read `pm.py payload`.
2. If no feature ID/name is given and multiple features exist, ask the user which feature to checkout.
3. Read branches from `pm.py payload` — they were cached when the PRs were tracked.
4. Find the workspace manifest XML (`.repo/manifests/` directory) to map repo names to local paths via `<project name="..." path="...">` entries.
5. **Check workspace occupancy first.** The [workspace-manager](../workspace-manager/SKILL.md) store (`~/tasks/workspaces.db`) is the single source of truth for which workspace slots are in use. Determine the target workspace slot (from `{WORKSPACE_ROOT}`) and run `python3 ~/.copilot/skills/task-manager/tm.py workspaces` (or `python3 ~/.copilot/skills/workspace-manager/wm.py status <slot>`). If that slot is already held — by an active task (a review or another feature) **or** an ad-hoc hold — **warn the user before clobbering it** — checking out feature branches over a live review/dev workspace loses their context. Let the user confirm or pick a different workspace.
5b. **Fast path (token savings).** Whole-workspace checkouts go through the [wip-workspace](../wip-workspace/SKILL.md) CLI. If a `feat-<feature-id>` snapshot already exists from a prior `checkout`, restore the whole set in one cheap op — `cd {WORKSPACE_ROOT} && wip load feat-<feature-id> && wip all checkout` — then skip to the summary (step 8). Only fall through to the per-repo loop below the first time (no snapshot yet).
6. For each repo **with a PR branch**, fetch, checkout, and pull to ensure it's synced to the latest state:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git fetch origin {BRANCH} && git checkout {BRANCH} && git pull origin {BRANCH}
   ```
7. For all other repos in the workspace manifest that **do not** have a PR branch in this feature, checkout `main` and pull:
   ```bash
   cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git checkout main && git pull origin main
   ```
   This ensures the entire workspace is in a consistent state — feature branches where needed, clean `main` everywhere else.
7b. **Snapshot the aligned workspace (token savings).** Record and save the composite state so re-checking-out this feature later is a single cheap command instead of the per-repo loop above:
   ```bash
   cd {WORKSPACE_ROOT} && wip scan && wip save feat-<feature-id>
   ```
   Restore later with `wip load feat-<feature-id> && wip all checkout`; return the workspace to base with `wip all return`. When a feature is removed (`remove`), delete its snapshot: `rm -f ~/.wip/saved/feat-<feature-id>.wip`.
8. Present a summary table:
   | Repo | Local Path | Branch | Status |
   |------|-----------|--------|--------|
   | repo-name | path | feature-branch | OK / Error |
   | other-repo | path | main | OK / Error |
9. Report any repos that failed to checkout (e.g., local uncommitted changes blocking the switch).

### 7. Rebase Feature Branches (`rebase`)

Rebase all PRs in a feature (or all features) that have `needs-rebase` status onto the latest `main`.

**Procedure:**
1. Read `pm.py payload`.
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
8. For successfully rebased PRs, refresh owner observations with `pm.py refresh`.
   CI still requires an explicit `/ci` comment; a local status edit cannot trigger it.
9. For PRs with conflicts, keep status as `needs-rebase` and list the conflicting files so the user can resolve manually.

### 8. Draft Review Request Message (`review-msg`)

Draft a message asking someone to review the PRs for a feature.

**Procedure:**
1. Read `pm.py payload`.
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
6. Update the feature through `pm.set_pldm()`:
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
4. Update PLDM state/details through `pm.set_pldm()`, including the observation date.
5. Report the current status with the run URL. If `failed`, offer to open the run logs to help diagnose.

### 11. Auto-Cleanup

During any `status`/`check` operation:
- If all PRs in a feature are `merged`, the batch is done — retire it without asking.
- Retiring means **annotating the heading**, never deleting the entry:
  `## [11] Guard SV driver Kconfig behind EL_SV (IPINFRASW-2003) — ✅ COMPLETE 3/3 MERGED
  (2026-08-27, dropped from active tracking)`.
  The feature store is a record first, so the notes, description and PR table all stay. A heading
  matching `COMPLETE|DROPPED|ABANDONED` is what drops the feature out of active tracking, so
  un-retiring is just deleting that suffix.
- `pm.py refresh` does this automatically (see below), which is the normal path. Do it by hand
  when retiring without a scan.
- Notify the user: "All PRs for **<feature>** are merged — dropped from active tracking."

## `pm.py` — the token-free status refresher

`pm.py` (next to this file) does the mechanical half of `status`/`check` **without a model in
the loop**: it reads the owner database, probes tracked PRs through `gh` in parallel, and
writes the new value back into each row's `Status` cell. Everything else in the file — the
memoir bullets, descriptions, PLDM lines — is left byte-for-byte alone.

```bash
python3 ~/.copilot/skills/pr-monitor/pm.py list            # what's tracked, from the store
python3 ~/.copilot/skills/pr-monitor/pm.py refresh         # probe GitHub, update Status cells
python3 ~/.copilot/skills/pr-monitor/pm.py refresh -f 4 -f 7  # just features 4 and 7 (others keep cached probe detail)
python3 ~/.copilot/skills/pr-monitor/pm.py refresh --no-purge   # keep merged features active
python3 ~/.copilot/skills/pr-monitor/pm.py purge -n        # what would be retired
python3 ~/.copilot/skills/pr-monitor/pm.py payload         # JSON for the Team Portal

pm.py add-feature NAME [-P PRIO] [-s SUMMARY] [-j JIRA] [-d DESC] [-p REF ...]   # log a new feature
pm.py edit-feature 4 [--name N] [-P PRIO] [-s S] [-j J] [-d D]   # "" clears a field
pm.py note 4 "rebased clean, /ci posted" -l "rebase + /ci"   # append activity note
pm.py add-pr 4 <url|owner/repo#123>             # track one more PR on feature 4
pm.py rm-pr 4 286 [--repo ip.pcie.sw.fw.ep]     # stop tracking one PR
pm.py drop-feature 4                            # retire a feature from active tracking
```

Per PR it runs `gh pr view` (state, mergeable, mergeStateStatus, reviewDecision) plus
`gh pr checks` — **not** `statusCheckRollup`, because the rollup counts every superseded run
left behind by repeated `/ci` re-fires and reports phantom failures. Status is derived with the
same precedence as [Status Priority](#status-priority). Roughly 30 PRs settle in ~15s.

Merge-queue membership comes from a **single batched GraphQL query** (`queue_states`) covering
every tracked PR at once — ~2s for 27 PRs. `gh pr view --json` has no `isInMergeQueue` field, and
a call per PR would have tripled the scan. The same query returns `autoMergeRequest`, so a PR
armed with auto-merge shows `auto-merge` in the portal's Gate column. If the GraphQL call fails
the scan carries on without queue information rather than aborting.

The richer probe output (per-PR check counts, review decision, merge state, URL, and
`failed_runs` — up to four GitHub Actions run URLs of the failing checks, job suffix stripped) is cached at
the PR owner's `~/tasks/prs.db` for the portal. Owner mutations preserve applicable
probe details transactionally; the old JSON cache is only a migration input.

**Use `pm.py refresh` first** when the user asks for `status`/`check`. It is free and gives you
the current state of every PR; spend model calls only on the follow-up work — `analyze` on a
red PR, rebasing, or refreshing an in-flight PLDM job (which `pm.py` does not touch).

After writing the fresh statuses, `refresh` applies [Auto-Cleanup](#11-auto-cleanup) itself: any
feature whose PRs are now **all merged** gets its heading annotated `— ✅ COMPLETE N/N MERGED
(<date>, dropped from active tracking)` and drops out of the active payload. Nothing is deleted,
so the historical record survives; reactivation must use an explicit owner operation. The
retired features come back in `payload["purged"]` as `{id, name, total}` — report them to the
user. Pass `--no-purge` to skip it, or run `pm.py purge -n` to preview.

### In the Team Portal

The [`portal-maintainer`](../portal-maintainer/SKILL.md) portal has a **PRs** section rendered from
`pm.payload()`: a KPI band (blocked features, CI failing, needs rebase, changes requested,
approved, merge queue, merged) and one collapsible row per feature whose detail shows the summary, Jira links, PR table and
the full dated activity history, plus an editor for name/priority/summary/Jira/description and a note box
(`POST /api/feature-edit` → `edit_feature` / `add_note`). Its **↻ Scan PRs** button POSTs `/api/pr-scan`, which starts `pm.refresh()` on a background
thread server-side — so the user can re-check every PR from the browser at zero token cost. The scan
streams into the same progress banner the workspace alignment uses: it POSTs, gets a job id back, and
polls `/api/align/<job>` until done, showing `Scanning PRs (12/30) · Elmnt-Internal/ip.se.sw.fw #147`
plus one colour-coded row per PR as each probe lands. `pm.refresh()` takes an optional
`progress(done, total, feature, pr)` callback for exactly this; a second concurrent scan is refused
with HTTP 409. The same scan retires
fully-merged features, and the button reports it inline: `probed 30 PRs · retired 1: Guard SV
driver Kconfig behind EL_SV (IPINFRASW-2003) (3/3)`. A card that is all-merged but not yet
scanned is labelled *retires on the next scan*.

Tracking is editable from the browser too. The toolbar's **＋ Track feature** opens a dialog for a
name, an optional description and a list of PR refs (one per line, URL or `owner/repo#123`); each
card header carries **＋ PR** to track another PR on that feature and a trailing **✕** to drop the
whole feature; each PR row reveals a **✕** on hover to untrack that one PR. They POST
`/api/feature-track`, `/api/pr-track`, `/api/feature-untrack` and `/api/pr-untrack`, which call the
mutators below and return the refreshed payload. Nothing on GitHub is touched — only local tracking.
These endpoints take the portal scan lock and answer HTTP 409 (*a PR scan is running*)
when necessary; owner database transactions also protect independent CLI writers.

Each card also carries a **▶ PLDM test** button that submits the whole feature to the
[PLDM job queue](../pldm-job-queue/SKILL.md): it reserves a free workspace slot, checks it out
to the feature's PR branches, refuses if anything is dirty or unpushed, runs
`octl ws build --remote --target pldm`, and stamps the result back here via `pm.set_pldm()`.
See [Submitting a feature to PLDM](../../team-portal/references/portal-contracts.md#submitting-a-feature-to-pldm-from-the-portal).

### `set_pldm(feature_id, state, details)`

Transactionally updates a feature's PLDM state and details through the owner.
Passing empty details preserves existing details. `refresh()` updates GitHub
observations, not PLDM execution truth.

### Editing what's tracked

Six owner mutators edit tracked features without a model rewriting storage — exposed both as CLI
subcommands and as portal buttons:

| Function | Does | Does **not** |
|---|---|---|
| `add_feature(name, description, refs)` | Appends a `## [N]` block with `Logged`, optional `Description`, and an empty PR table; `N = max(id) + 1`. Then `add_pr`s each ref. Returns `(ok, msg, fid)`. | Touch existing blocks |
| `add_pr(feat_id, ref, …)` | Parses a PR URL or `owner/repo#123`, looks the PR up with `gh` to fill Branch/Title/Status, and inserts the row. Creates the `- **PRs:**` bullet and table header only if they are missing. | Add a duplicate `(owner, repo, number)`, or add a PR `gh` cannot see |
| `remove_pr(feat_id, number, repo="")` | Deletes that one table row. | Guess when `#N` appears twice — it asks for `--repo` |
| `edit_feature(feat_id, name, description, summary, jira, priority)` | Rewrites the heading and upserts/removes the `Priority`/`Summary`/`Jira`/`Description` fields (`None` = leave, `""` = remove). Jira input is normalised to upper-case keys; text with no key is refused. | Touch notes or the PR table |
| `add_note(feat_id, text, label="")` | Appends `- **<today> (label):** text` at the end of the block. | Edit or remove existing notes |
| `drop_feature(feat_id)` | Annotates the heading `— 🚫 DROPPED (<date>, dropped from active tracking)` so it falls out of the active payload. | Delete anything — remove the annotation to bring it back |

Each mutation preserves still-tracked PR probe details (check counts, review
decision, merge state and `last_scan`) through the owner. Markdown field/table
terminology below describes interchange formatting, not permission to edit the
legacy file or bypass database transactions.

PR titles are run through `_cell()`, which collapses whitespace and maps `|` to `/` — a literal
pipe would split the markdown row.

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
3. `merge-queue` — GitHub has taken the PR into the merge queue. It is landing on its own, so it outranks everything below: nothing here is actionable.
4. `ci-fail`
5. `changes-requested`
6. `needs-rebase`
7. `approved`
8. `pending`

## Rules

- Always use `pm.py` to read/write tracked features. Never edit the legacy memory
  file, generated exports or database directly.
- Allocate IDs through the owner transaction. Do not calculate and persist IDs
  through hand-written Markdown or SQL. Conflicting legacy identities block import
  and require an explicit repair; never guess which record to overwrite.
- **Run `pm.py refresh` before a `status`/`check` pass** — it costs nothing and returns the true state of every PR. Reserve model calls for the interpretation and the follow-up actions.
- Each feature has a stable running ID in its header (`## [N] <Feature Name>`); assign `max(existing)+1` on `log` and never renumber on `remove`.
- Never delete a feature entry. Retiring a fully-merged batch is an automatic heading annotation, not a deletion, and `remove` should do the same unless the user explicitly asks for the text to go.
- When checking status, fetch all PRs even if some have cached status — always get fresh data.
- Use `gh` CLI as fallback if the GitHub tools don't return sufficient detail (e.g., for failure logs).
- Present results as markdown tables for readability.
- When a PR has unresolved review comments, mention the count and latest commenter.
- For `pldm-test`, always delegate the actual job-queue submission to the [`pldm-job-queue`](../pldm-job-queue/SKILL.md) skill — do not re-implement the `octl` flow here. Track only the outcome (`PLDM Test` + `PLDM Details`) through the owner API.
- The **PLDM Test** field is feature-level. It is set by `pldm-test` (submit) and refreshed by `pldm-status`; `status`/`check` also auto-refreshes any feature still `queued`/`running` so in-flight jobs settle without a manual `pldm-status`. Terminal results (`passed`/`failed`/`cancelled`) and `not-tested` features are not re-queried. When a fully-merged feature is retired, it stops being re-queried along with everything else on the entry.
