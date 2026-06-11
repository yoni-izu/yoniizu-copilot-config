---
name: pr-review
description: "Review a GitHub pull request end-to-end with the reviewer's own style and memoir. Use when: reviewing a PR, drafting review comments, doing a code review, re-reviewing after a force-push/rebase, checking a PR before approving, requesting changes, auditing a code-move/refactor PR, reviewing HW/register/driver code, deciding an approve/comment/request-changes verdict."
argument-hint: "PR URL or owner/repo#number, optionally: re-review, draft-only, or submit"
---

# PR Review

Conduct a high-signal code review of a GitHub PR grounded in three things, in priority order:

1. **The review memoir** — [references/memoir.md](./references/memoir.md): the reviewer's accumulated philosophy, always-checks, recurring patterns, approved designs, anti-patterns, and theme-grouped lessons. **Read it before every review.**
2. **The reviewer's style** — correctness first, actionable comments, pick battles (3 blockers > 15 nits), respect the author's design, verify every claim against the actual code path.
3. **Your own objective reasoning** — independent technical judgment. The memoir guides; it doesn't replace fresh analysis of *this* PR.

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
1. Resolve the PR (URL or `owner/repo#number`).
2. Pull metadata: title, description, author, base branch, state, mergeable, CI status.
3. **Get the canonical commit list** with `gh pr view <N> --json commits` — do NOT rely on `git log origin/main..HEAD` (may include sibling PRs' commits).
4. **Snapshot the final diff** with `gh pr diff <N>` and treat that snapshot as the source of truth for all findings. Re-pull right before posting (squash/force-push/revert during the review window erases code findings target).
5. Read the changed files for context — not just the hunks. For pure code-move/refactor PRs, hash-compare blobs instead of reading (see memoir "Code-Move & Multi-Repo Refactors").

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
