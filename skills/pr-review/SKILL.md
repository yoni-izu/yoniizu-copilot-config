---
name: pr-review
description: "Review a GitHub pull request end-to-end with the reviewer's own style and memoir. Use when: reviewing a PR, drafting review comments, doing a code review, reviewing PRs checked out locally in the workspace (e.g. after task-manager review-start), re-reviewing after a force-push/rebase, checking a PR before approving, requesting changes, auditing a code-move/refactor PR, reviewing HW/register/driver code, deciding an approve/comment/request-changes verdict."
argument-hint: "PR URL or owner/repo#number, OR 'local' to review checked-out workspace branches; optionally: re-review, draft-only, submit"
---

# PR Review

Conduct a high-signal code review of a GitHub PR grounded in three things, in priority order:

1. **The review memoir** — [references/memoir.md](./references/memoir.md): the reviewer's accumulated philosophy, always-checks, recurring patterns, approved designs, anti-patterns, and theme-grouped lessons. **Read it before every review.**
2. **The reviewer's style** — correctness first, actionable comments, pick battles (3 blockers > 15 nits), respect the author's design, verify every claim against the actual code path.
3. **Your own objective reasoning** — independent technical judgment. The memoir guides; it doesn't replace fresh analysis of *this* PR.

## Review Modes

- **Remote mode** (default) — a PR URL or `owner/repo#number` is given. Fetch the diff from GitHub.
- **Local workspace mode** — the workspace already has repos checked out to author branches (typically via `task-manager review-start`). Compute the diff locally from the working tree and map each repo back to its PR. Use this when the user says "review local", "review the workspace", "review the checked-out branches", or when an in-progress `code-review` task with a `workspace` exists. Local mode gives richer context (full files, build, grep, tests) than `gh pr diff`. **All Hard Rules, drafting, approval, and submission steps are identical** — only diff acquisition (Step 1) differs.

## Hard Rules (never violate)

- **NEVER post reviews, comments, replies, or a verdict to GitHub without explicit user approval.** Draft locally, present for approval, submit only when told.
- **NEVER delete or overwrite a pending review** — it may hold the user's own draft comments. Always inspect existing pending reviews first.
- **NEVER auto-submit a verdict.** Recommend approve / comment / request-changes; the user decides and triggers submission.
- Draft files live under [drafts/](./drafts/) (skill-local). Name them `pr-<N>-<repo>-<YYYYMMDD-HHMM>.md`.

## Tooling

Prefer the `gh` CLI in a terminal (`gh pr view`, `gh pr diff`, `gh pr view --json commits,files,reviews`, `gh api graphql` for pending drafts). Fall back to `github-pull-request_*` MCP tools when `gh` is unavailable or a structured fetch is easier. For status checks and review state, either `gh pr checks` / `gh pr view --json reviews` or `github-pull-request_pullRequestStatusChecks`.

## Workflow

### Step 0 — Consult the memoir
Read [references/memoir.md](./references/memoir.md) in full. Note which "Always Check" items, anti-patterns, and lessons are likely to apply to this PR's domain.

### Step 1 — Fetch and understand the PR

**Remote mode:**
1. Resolve the PR (URL or `owner/repo#number`).
2. Pull metadata: title, description, author, base branch, state, mergeable, CI status.
3. **Get the canonical commit list** with `gh pr view <N> --json commits` — do NOT rely on `git log origin/main..HEAD` (may include sibling PRs' commits).
4. **Snapshot the final diff** with `gh pr diff <N>` and treat that snapshot as the source of truth for all findings. Re-pull right before posting (squash/force-push/revert during the review window erases code findings target).
5. Read the changed files for context — not just the hunks. For pure code-move/refactor PRs, hash-compare blobs instead of reading (see memoir "Code-Move & Multi-Repo Refactors").

**Local workspace mode:** see [Local Workspace Mode](#local-workspace-mode) below for diff acquisition, then continue with Step 2 unchanged.

### Step 2 — Read existing review state first
1. **Author's own pending drafts:** fetch via GraphQL `pullRequest.reviews(states: PENDING)` (or `gh api graphql`). Many findings may already be drafted in the author's words — focus on the gaps.
2. **Other reviewers' posted comments and threads:** read them so you don't duplicate signal; build on or defer to existing threads.
3. **Existing pending review by the user:** inspect it; never clobber it.

### Step 3 — Analyze and form findings
Apply the memoir's checklists plus independent reasoning. For each candidate finding:
- Verify the claim against the actual code path — don't flag on suspicion.
- Pull the vendor manual for unfamiliar HW/register code: invoke the relevant IP skill (`pcie-ep-ip`, `su-ip`, `d2d-ip`, `ras-manageability`, etc.). Reading the register-field semantics first can flip the conclusion.
- Classify each finding: **blocker** / **suggestion** / **nit** / **question** / **praise**.
- Pick battles: drop nits that don't hurt readability or maintainability. Distinguish PR-introduced from pre-existing issues; defer the latter to their own PR.

### Step 4 — Draft locally
Write a draft to [drafts/](./drafts/) using the template below. Include: per-finding file/line anchor, classification, the comment body, and the *why*. Add a **Dropped/considered** section recording findings you investigated and discarded (with reason) — this preserves reasoning and feeds the memoir later.

### Step 5 — Present for approval
Show the user the full draft: findings grouped by severity, a **recommended verdict** (approve / comment / request-changes) with one-line justification, and the dropped list. Ask which comments to post and whether to submit the verdict. Do nothing on GitHub yet.

### Step 6 — Submit only what's approved
On explicit approval, post the approved comments and (if instructed) the verdict via `gh`/MCP. Back up the submitted set into [drafts/](./drafts/) (e.g. `pr-<N>-submitted-<timestamp>.md`).

### Step 7 — Update the memoir
After the review, fold genuinely new, reusable lessons into [references/memoir.md](./references/memoir.md). **Consolidate intelligently:** group each lesson under the right existing theme, merge true duplicates, and keep entries tight and principle-first (drop review-specific provenance). Do not append a per-review log section.

## Local Workspace Mode

Review PRs from repos already checked out to author branches in the local workspace (typically left by `task-manager review-start`). This replaces only the diff-acquisition part of Step 1; everything else (read existing review state, analyze, draft, present, submit, memoir) is unchanged.

### Resolve which repos/branches/PRs to review
1. **Prefer the task-manager review store** as the source of truth. Read it with `python3 ~/.copilot/skills/task-manager/tm.py list --category code-review` (or read the store JSON). Pick the in-progress `code-review` task (the one matching the current `workspace`, or ask if several). Each `reviews[]` entry gives `repo`, `branch`, `owner`, `pr_number`, `pr_url`, `author` — this is the local-path-to-PR mapping needed for posting later.
2. **Workspace root:** the task's `workspace` field, else the current `~/projects/<project>/<ws>` root, else cwd. Map repo names to local paths via the workspace manifest (`.repo/manifests/` → `<project name="..." path="...">`). Note the manifest `revision` attribute — that fb/base branch is the diff base, **not** `main`.
3. **Fallback when no store entry exists:** for each repo on a non-base branch, resolve the PR with `gh pr list --head <branch> --repo <owner>/<repo> --json number,url,headRefName`.

### Acquire the diff locally
For each review repo:
1. **Confirm the checkout matches expectation:** `git -C <path> rev-parse --abbrev-ref HEAD` must equal the expected `branch`. If it doesn't (dirty, detached, or wrong branch), report and skip — never force-checkout or reset.
2. **Compute the true diff against the merge-base**, so unrelated base advances don't pollute the diff:
   ```bash
   git -C <path> fetch origin <base>            # base = manifest revision / PR base branch
   base_sha=$(git -C <path> merge-base origin/<base> HEAD)
   git -C <path> diff $base_sha..HEAD           # diff snapshot (source of truth)
   git -C <path> log --oneline $base_sha..HEAD  # commit list
   ```
   Cross-check the local head against the PR head SHA (`gh pr view <N> --json headRefOid`) so you're reviewing what's actually on the PR. If they differ, the local branch is stale — `git pull` or warn before proceeding.
3. **Exploit local context:** read full files, `grep`/build/run tests as needed. This is the main advantage over remote mode — use it for second-consumer greps, `BUILD_ASSERT` checks, and verifying claims against the real code path.

### Continue with the standard flow
- Step 2 (existing review state) and Step 6 (submission) still hit **GitHub** — the author's pending drafts, other reviewers' threads, and posted comments live there. Use the `owner`/`repo`/`pr_number` from the store/`gh` to target the right PR.
- Draft to [drafts/](./drafts/) as usual; record the local head SHA reviewed.
- When done, prefer `task-manager review-done <id> <outcome>` so the review task status and the memoir update flow through the task-manager lifecycle.

## Re-Review (force-push / rebase)
When invoked with `re-review` or when the PR changed since the last pass:
1. Find the previously reviewed SHA (from the prior draft in [drafts/](./drafts/)).
2. `git diff <old-sha>..<new-sha>` (or `gh pr diff`) and triage per file: scrutinize comment-addressing fixes; glance-and-pass unrelated rebase artifacts (path repaths, CI config). Don't re-review unchanged files.
3. Check for silent changes and JIRA ticket renumbering in commit messages (can signal scope change).
4. Re-audit each parked finding against the fresh diff snapshot — drop any whose target code no longer exists.

## Draft Template

```markdown
# PR #<N> — <title>
- Repo: <owner/repo>
- PR: <url>
- Head SHA reviewed: <sha>
- Drafted: <timestamp>

## Recommended verdict: <approve | comment | request-changes>
<one-line justification>

## Findings
### [BLOCKER] <file>:<line>
<comment body — what to change and why>

### [SUGGESTION] <file>:<line>
...

### [NIT] / [QUESTION] / [PRAISE] ...

## Dropped / considered
- <finding> — <why dropped (e.g. observability exists; BUILD_ASSERT already present; author tested it)>
```
