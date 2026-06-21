---
name: review-mgr
description: "Manage the incoming code-review queue end-to-end: discover PRs that need your review, log them, align the workspace to a review, scan blocked reviews for author activity, and close out a review pass. Use when: scan for PRs that need my review, find new review requests, add PRs to review, list/track my review queue, start/stop a review checkout, finish a review pass, check if a blocked review bounced back, review queue status. For the actual act of reviewing a PR (drafting findings, the memoir, submitting), use the pr-review skill. For tracking your OWN submitted PRs, use pr-monitor."
argument-hint: "Action (scan/add/list/start/stop/done/status/check/blocked-scan/msg) and optional PR links or task id"
---

# Review Manager

Own the **incoming PR-review queue** — the PRs *other people* ask you to review — from
discovery to close-out. This skill is the lifecycle layer; it does not replace:

- **`pr-review`** — the act of reviewing one PR (read state, draft findings, the review memoir, submit). `review-mgr` hands off to it.
- **`task-manager`** — the underlying task store and the *only* writer to `~/tasks/tasks.json`. `review-mgr` calls `tm.py`; it never edits the store directly except for the judgment branches called out below.
- **`pr-monitor`** — your *outgoing* submitted PRs. Different direction entirely.

Review tasks live in the same store as everything else, as `category: "code-review"`
tasks carrying a `reviews[]` array (one entry per PR). The schema and all mechanical
mutations are defined by `task-manager` / `tm.py`.

## CLI (mechanics live in tm.py — always prefer these)

Run as: `python3 ~/.copilot/skills/task-manager/tm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `scan-reviews [--fw | --all] [--limit N] [--json]` | Discover open PRs that request your review | Three scope tiers: **hsi** (default) = D2D, PCIe (EP), SE; **`--fw`** = the broader FW team repo allowlist; **`--all`** = every repo GitHub flags. Allowlists in [references/hsi-repos.txt](./references/hsi-repos.txt) and [references/fw-repos.txt](./references/fw-repos.txt). Filters out self-authored PRs, `ip.infra.sw.manifests`, and `MODINFSDKSW-*` families. Annotates each candidate as `[new]` or `[tracked T-NNN]` and tags PRs sharing a Jira key as a group. |
| `add-review <url> [<url>...] [-t TITLE] [-d DESC] [-p PRI] [--complexity X] [--cost N] [--jira K]` | Create a review task from one or more PR URLs | Fetches title/author/branch/stats via `gh`. Auto-estimates complexity. Multiple URLs → one multi-PR task. |
| `list-reviews` | List active review tasks | Hides done/cancelled and all-merged tasks. |
| `review-update <id> <review_status> [--pr N]` | Manual review-status update | Applies to all PR entries unless `--pr`. Auto-derives task status and auto-purges when every PR is resolved. |
| `review-check` | Poll GitHub, mark merged/closed PRs, purge fully-resolved review tasks | Skips done/cancelled. |
| `review-msg <id>` | Print a templated review-request message | |

Review statuses: `pending`, `in-progress`, `commented`, `changes-requested`, `waiting-for-re-review`, `approved`, `merged`.

Review lifecycle: `todo` → `in-progress` → `blocked` (comments/changes-requested submitted) → `todo` (author updated) → … → `done` (only when **all** PRs approved/merged). Never mark a review task `done` after submitting non-approval feedback — `review-update` enforces this.

## Workflows

### `scan` — discover PRs that need my review

Find new review requests and let the user tick which to add.

1. Run `tm.py scan-reviews --json` (HSI IPs: D2D/PCIe/SE) by default. Because you sit in a wide reviewers group, GitHub flags many PRs that are noise to you — so widen deliberately: `--fw` for the broader FW team allowlist, or `--all` for every repo GitHub flags. The CLI already excludes self-authored PRs, `ip.infra.sw.manifests`, and `MODINFSDKSW-*`, and annotates each candidate as new/tracked plus a Jira `group`.
2. Present the candidates as a **multi-select checkbox list** (use the ask-questions tool, `multiSelect: true`). For each candidate show: `repo#num — title (author)`. Already-tracked PRs (`tracked: T-NNN`) must be shown but clearly annotated and **left unticked by default**; new ones are the focus.
3. **Surface Jira groups as a suggestion, not a mandate.** When several ticked PRs share a `group` (Jira key), point it out and ask, per group, whether to add them as **one multi-PR review task** or as **separate tasks**. Standalone PRs (no group) become their own task.
4. **Enforce one PR per repo per task.** Before creating any multi-PR task, verify that no two PRs in the group target the same repo. If they do, **split them into separate tasks** — one task per conflicting repo. Reason: `review-start` checks out one branch per repo; two PRs in the same repo means one can never be checked out, making local review impossible and causing confusion about which code is live.
5. For each chosen group/PR, call `tm.py add-review <url> [<url>...]` — pass all URLs of a group in one call to create a single multi-PR task; pass single URLs for standalone tasks. Let `add-review` fetch details and estimate complexity. Skip anything already tracked.
6. Report what was added (task ids + PR counts) and what was skipped (already tracked).

Do **not** auto-add without the tick step — discovery is assistive, the user curates.

### `start <task-id-or-filter>` — align the workspace to a review

Check the workspace's repos out to a review task's branches so you can review locally.

1. Read the store; pick target review tasks (matching task id, author, repo, or PR — or default: all non-merged with status `todo`/`in-progress`).
2. For each PR entry, find the target repo path inside the workspace.
3. **Branch checkout (safe):**
   - `git fetch origin <branch>` then `git checkout <branch>`.
   - If the remote ref is missing (renamed/force-pushed), fall back to `git fetch origin pull/<pr>/head:<branch>` then `git checkout <branch>`.
   - **Never** create local `pr-NNN` branches — always use the real `reviews[].branch` name.
   - **Never** use destructive resets. If a repo has local changes and checkout is blocked, leave it and report it.
4. Switch all other workspace repos to their default branch (`main`) so the workspace reflects only the review context.
5. Update the task: `status: in-progress` if it was `todo`; each selected PR entry `review_status: in-progress` if `pending`; append a note. Use `tm.py review-update <id> in-progress` or edit the store.
6. Report: which repos switched to review branches, which to main, which were skipped (and why).
7. Hand off the actual review to **`pr-review`** (local workspace mode).

### `stop`

Switch every workspace repo back to its default branch. Skip (don't force) dirty repos and report them. Don't change task status unless asked.

### `done <task-id> [outcome]` — close out a review pass

Combined `stop` + status update.

1. Switch all workspace repos to default branch (skip dirty ones; never force).
2. Set `review_status` on the relevant PR entries to the outcome (`commented`, `changes-requested`, `approved`, …). If unspecified, keep current.
3. Derive task status:
   - All PR entries `approved`/`merged` → task `done`, clear `workspace`, set `completed`.
   - Outcome `commented`/`changes-requested` → task `blocked`, clear `workspace`.
   - Outcome `in-progress` (just stopping) → keep `in-progress`, clear `workspace`.
4. Always: `updated = today`, append a note describing the outcome.
5. Easiest path: `tm.py review-update <id> <review_status>` (handles the derivation), then adjust notes / `workspace` if needed.
6. **Memoir update** — append lessons to `pr-review`'s memoir at `~/.copilot/skills/pr-review/references/memoir.md`:
   - Read it first to avoid duplicates and respect existing section names.
   - Consolidate: fold each lesson under the right theme heading and merge duplicates — no per-review log section.
   - Capture findings (posted and dropped), new patterns/anti-patterns, drop reasoning, process insights.
   - **Strip specifics** — no PR numbers, commit hashes, branch names, line numbers, task ids, or author names.
   - If a new recurring "What I Always Check" item emerged, add it to that section too.

### `blocked-scan` — has the ball come back to me?

For `blocked` review tasks, check whether the author acted (CLI does **not** do this).

- For each `blocked` review task, for each PR entry: use `github-pull-request_issue_fetch` to compare `headRefOid` / latest commit date against the entry's `submitted_at`, and read author replies on review threads.
- Classify each thread: **addressed** (fix pushed or "done"/"fixed" reply), **pushed back** ("won't fix"/"by design"), or **no response**.
- If new commits since `submitted_at` → `tm.py review-update <id> waiting-for-re-review`. If only replies, do **not** auto-transition — report and ask.
- Report per task:
  ```
  ## T-NNN: <title> (blocked Xd)
  ### <repo>#<pr> — ⚠️ Author updated (N new commits)
  - Your comment on `<file>`: <topic> → Author replied: "<gist>"
  → **Needs re-review** (X/Y threads addressed)
  ```
  Indicators: ⚠️ new commits, 💬 replies only, ✅ no changes, 🔀 merged, ❌ closed.

## Rules

1. **`tm.py` is the only writer to the store.** Use it for every mechanical mutation. Fall back to direct JSON edits only for the judgment branches above (`start`/`stop`/`done` git steps, `blocked-scan`, memoir).
2. **Never post to GitHub without explicit user approval** — that gate belongs to `pr-review`; `review-mgr` only queues and tracks.
3. **Curate, don't auto-add.** `scan` always goes through the tick step.
4. **Never destructive git.** No resets; skip and report dirty repos.
5. **Done gating:** a review task is `done` only when every PR is approved/merged. Submitting comments/changes-requested → `blocked`.
6. **Auto-purge:** when every PR in a task is resolved (merged or closed-without-merge) the task is deleted — `review-update` / `review-check` handle this.
7. **One PR per repo per task.** A multi-PR review task must never contain two PRs targeting the same repo. `review-start` can only check out one branch per repo — a second PR in the same repo is unreachable locally, causes confusion about which code is under review, and forces the reviewer to context-switch branches mid-review. When adding PRs (via `scan` or `add-review`), split any same-repo collisions into separate tasks. When inheriting a task that already violates this, split it before `start`.
8. **Repo allowlists** live in [references/hsi-repos.txt](./references/hsi-repos.txt) (HSI IPs: D2D/PCIe/SE) and [references/fw-repos.txt](./references/fw-repos.txt) (broader FW team); keep them current as repos are added/retired.
