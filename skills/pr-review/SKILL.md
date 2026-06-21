---
name: pr-review
description: "Review a GitHub pull request end-to-end with the reviewer's own style and memoir. Use when: reviewing a PR, drafting review comments, doing a code review, reviewing PRs checked out locally in the workspace (e.g. after review-mgr start), re-reviewing after a force-push/rebase, checking a PR before approving, requesting changes, auditing a code-move/refactor PR, reviewing HW/register/driver code, deciding an approve/comment/request-changes verdict."
argument-hint: "PR URL or owner/repo#number, OR 'local' to review checked-out workspace branches; optionally: re-review, draft-only, submit"
---

# PR Review

Conduct a high-signal code review of a GitHub PR grounded in three things, in priority order:

1. **The review memoir** — [references/memoir.md](./references/memoir.md): the reviewer's accumulated philosophy, always-checks, recurring patterns, approved designs, anti-patterns, and theme-grouped lessons. **Read it before every review.**
2. **The reviewer's style** — correctness first, actionable comments, pick battles (3 blockers > 15 nits), respect the author's design, verify every claim against the actual code path.
3. **Your own objective reasoning** — independent technical judgment. The memoir guides; it doesn't replace fresh analysis of *this* PR.

## Review Modes

- **Remote mode** (default) — a PR URL or `owner/repo#number` is given. Fetch the diff from GitHub.
- **Local workspace mode** — the workspace already has repos checked out to author branches (typically via `review-mgr start`). Compute the diff locally from the working tree and map each repo back to its PR. Use this when the user says "review local", "review the workspace", "review the checked-out branches", or when an in-progress `code-review` task with a `workspace` exists. Local mode gives richer context (full files, build, grep, tests) than `gh pr diff`. **All Hard Rules, drafting, approval, and submission steps are identical** — only diff acquisition (Step 1) differs.

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

**Always inventory every pre-existing comment before forming new findings — never review a PR as if it were untouched.** Existing threads are first-class review inputs: each one is either resolved, still-open, or stale, and that disposition shapes the verdict.

1. **Author's own pending drafts:** fetch via GraphQL `pullRequest.reviews(states: PENDING)` (or `gh api graphql`). Many findings may already be drafted in the author's words — focus on the gaps.
2. **All prior review comments and threads** (yours, other reviewers', and bots like Copilot/CI): pull the full set with `gh pr view <N> --json reviews,comments` and the review-thread resolution state via GraphQL (`pullRequest.reviewThreads` → `isResolved`, `comments`). Read every thread so you don't duplicate signal and so you can build on or defer to it.
3. **For each existing comment/thread, determine whether the author attended to it.** This is mandatory, not optional:
   - **Map the thread to its code anchor** and check the *current* diff snapshot (from Step 1) for a change that addresses it. A resolved/outdated thread whose target lines changed is likely handled; an unresolved thread on still-unchanged code is likely open.
   - **Cross-check the commit history** (`gh pr view <N> --json commits`, `git log`) for a fix commit referencing the comment, and read the author's reply on the thread.
   - Classify each prior thread as: **Addressed** (code changed or convincingly answered), **Partially addressed** (some but not all of the ask), **Unaddressed** (no change, no reply, still valid), **Acknowledged-won't-fix** (author declined with rationale), or **Stale/moot** (target code removed).
   - For **Unaddressed** or **Partially addressed** threads that you still agree with, carry them forward as findings (re-anchored to current lines) so they aren't silently lost. For ones you now disagree with, note them in the Dropped/considered section with the reason.
   - **Decide if anything should be added** to an existing thread: a follow-up nudge, a clarification, or a note that a fix is incomplete. Prefer replying on the existing thread over opening a duplicate.
4. **Existing pending review by the user:** inspect it; never clobber it.

### Step 3 — Analyze and form findings
Apply the memoir's checklists plus independent reasoning. For each candidate finding:
- Verify the claim against the actual code path — don't flag on suspicion.
- Pull the vendor manual for unfamiliar HW/register code: invoke the relevant IP skill (`pcie-ep-ip`, `su-ip`, `d2d-ip`, `ras-manageability`, etc.). Reading the register-field semantics first can flip the conclusion.
- Classify each finding: **blocker** / **suggestion** / **nit** / **question** / **praise**.
- Pick battles: drop nits that don't hurt readability or maintainability. Distinguish PR-introduced from pre-existing issues; defer the latter to their own PR.

### Step 4 — Draft locally
Write a draft to [drafts/](./drafts/) using the template below. Include: per-finding file/line anchor, classification, the comment body, and the *why*. Add a **Dropped/considered** section recording findings you investigated and discarded (with reason) — this preserves reasoning and feeds the memoir later.

### Step 5 — Present for approval
Lead with an **Executive summary of the changes** (see below), then show the user the full draft: a **Prior comments** disposition table (each existing thread marked Addressed / Partially / Unaddressed / Won't-fix / Stale, with what the author did and any follow-up to add), findings grouped by severity, a **recommended verdict** (approve / comment / request-changes) with one-line justification, and the dropped list. Call out any unaddressed prior comments explicitly — they often gate the verdict. Ask which comments to post and whether to submit the verdict. Do nothing on GitHub yet.

**Executive summary (always include):** a tight, high-level briefing of *what the PR does* — written for a reader who hasn't seen the diff. Keep it to a few sentences plus a short bullet list. Cover:
- **What & why:** the PR's purpose and the problem it solves, in plain language.
- **Scope:** the main areas touched (files/modules/repos), and for multi-repo sets, the per-repo gist.
- **Notable changes:** the few changes that matter most (new behavior, interface/ABI changes, risk areas), not an exhaustive file list.
- **Verdict at a glance:** the recommended verdict and the single biggest reason for it.
This is a summary of the *changes*, separate from and preceding the findings — it orients the reader before the detailed review.

### Step 6 — Submit only what's approved
On explicit approval, post the approved comments and (if instructed) the verdict via `gh`/MCP. Back up the submitted set into [drafts/](./drafts/) (e.g. `pr-<N>-submitted-<timestamp>.md`).

### Step 7 — Update the memoir
After the review, fold genuinely new, reusable lessons into [references/memoir.md](./references/memoir.md). **Consolidate intelligently:** group each lesson under the right existing theme, merge true duplicates, and keep entries tight and principle-first (drop review-specific provenance). Do not append a per-review log section.

## Local Workspace Mode

Review PRs from repos already checked out to author branches in the local workspace (typically left by `review-mgr start`). This replaces only the diff-acquisition part of Step 1; everything else (read existing review state, analyze, draft, present, submit, memoir) is unchanged.

### Resolve which repos/branches/PRs to review
1. **Prefer the review-mgr task store** as the source of truth. Read it with `python3 ~/.copilot/skills/task-manager/tm.py list --category code-review` (or read the store JSON). Pick the in-progress `code-review` task (the one matching the current `workspace`, or ask if several). Each `reviews[]` entry gives `repo`, `branch`, `owner`, `pr_number`, `pr_url`, `author` — this is the local-path-to-PR mapping needed for posting later.
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
- Draft to [drafts/](./drafts/) as usual; record the local head SHA reviewed (one per repo in a multi-repo set).
- When done, prefer `review-mgr done <id> <outcome>` so the review task status and the memoir update flow through the review-queue lifecycle. **Record the reviewed head SHA(s) in the task notes** (e.g. `reviewed @ <repo>:<sha>`), so the next re-review can diff `that-sha .. new-head` without re-deriving the baseline. This is the authoritative re-review baseline when no prior draft exists.

## Re-Review (force-push / rebase)
When invoked with `re-review` or when the PR changed since the last pass:
1. **Find the last actually-reviewed SHA** — the head from the *previous review pass*, not the head you just recomputed this session. Resolve it in priority order: (a) the `Head SHA reviewed` line in the most recent prior draft in [drafts/](./drafts/); (b) the `reviewed @ <sha>` recorded in the task-manager task notes (see `review-done`); (c) the `commit.oid` GitHub attaches to your last submitted review (`gh pr view <N> --json reviews` / GraphQL `reviews.nodes.commit.oid`). **Never diff a SHA against itself** — if old and new heads match, retrieve the older one from (a)/(b)/(c) before concluding "no new code".
2. **Fetch the old SHA even if it was force-pushed away** (`git fetch origin <old-sha>`; rebases orphan it but it stays fetchable by hash). Then `git diff <old-sha>..<new-sha>` (or `gh pr diff`) and triage per file: scrutinize comment-addressing fixes; glance-and-pass unrelated rebase artifacts (path repaths, CI config, base advances). Don't re-review unchanged files.
3. **Confirm "no new code" by diff, never by head-equality alone.** A rebase changes the head SHA even when your session's recomputed head already matches the PR head; the real question is `old-reviewed-SHA .. current-head`. New files (e.g. a driver split) hide here — list `git diff --name-status <old>..<new>` before claiming nothing changed.
4. Check for silent changes and JIRA ticket renumbering in commit messages (can signal scope change).
5. Re-audit each parked finding against the fresh diff snapshot — drop any whose target code no longer exists.

## Draft Template

```markdown
# PR #<N> — <title>
- Repo: <owner/repo>
- PR: <url>
- Head SHA reviewed: <sha>
- Drafted: <timestamp>

## Recommended verdict: <approve | comment | request-changes>
<one-line justification>

## Executive summary
<2-4 sentences: what the PR does and why, in plain language for a reader who hasn't seen the diff>
- **Scope:** <main areas / modules / repos touched; per-repo gist for multi-repo sets>
- **Notable changes:** <the few changes that matter most — new behavior, interface/ABI changes, risk areas>
- **Verdict at a glance:** <recommended verdict + the single biggest reason>

## Prior comments (existing threads)
- [<Addressed | Partially | Unaddressed | Won't-fix | Stale>] <file>:<line> — <commenter>: <one-line gist> — <how the author attended to it / what's still missing / follow-up to add>

## Findings
### [BLOCKER] <file>:<line>
<comment body — what to change and why>

### [SUGGESTION] <file>:<line>
...

### [NIT] / [QUESTION] / [PRAISE] ...

## Dropped / considered
- <finding> — <why dropped (e.g. observability exists; BUILD_ASSERT already present; author tested it)>
```
