---
name: review-mgr
description: "Manage the incoming code-review queue end-to-end: discover PRs that need your review, log them, align the workspace to a review, batch auto-draft preliminary reviews of new tasks, scan blocked reviews for author activity, and close out a review pass. Use when: scan for PRs that need my review, find new review requests, add PRs to review, list/track my review queue, start/stop a review checkout, pre-review / auto-draft / review new PRs, finish a review pass, check if a blocked review bounced back, review queue status. For the actual act of reviewing a PR (drafting findings, the memoir, submitting), use the pr-review skill. For tracking your OWN submitted PRs, use pr-monitor."
argument-hint: "Action (scan/add/list/start/pre-review/stop/done/status/check/blocked-scan/msg) and optional PR links or task id"
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

> **HSI team cross-review.** All HSI team members review each other's PRs, so any PR
> in your queue may already carry reviews from teammates. Whenever you **start** or
> **resume** a review (`start`, and the re-review path after `blocked-scan`), first
> pull the existing reviews and inline threads left by others and factor them into
> your draft — don't duplicate what a teammate already raised, and weigh their
> findings before adding your own.

## CLI (mechanics live in tm.py — always prefer these)

Run as: `python3 ~/.copilot/skills/task-manager/tm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `scan-reviews [--fw | --all] [--limit N] [--json]` | Discover open PRs that request your review | Three scope tiers: **hsi** (default) = D2D, PCIe (EP), SE; **`--fw`** = the broader FW team repo allowlist; **`--all`** = every repo GitHub flags. Allowlists in [references/hsi-repos.txt](./references/hsi-repos.txt) and [references/fw-repos.txt](./references/fw-repos.txt). Filters out self-authored PRs, `ip.infra.sw.manifests`, and `MODINFSDKSW-*` families. Annotates each candidate as `[new]` or `[tracked T-NNN]` and tags PRs sharing a Jira key as a group. |
| `add-review <url> [<url>...] [-t TITLE] [-d DESC] [-p PRI] [--complexity X] [--cost N] [--jira K]` | Create a review task from one or more PR URLs | Fetches title/author/branch/stats via `gh`. Auto-estimates complexity. Multiple URLs → one multi-PR task. |
| `list-reviews` | List active review tasks | Hides done/cancelled and all-merged tasks. |
| `review-update <id> <review_status> [--pr N]` | Manual review-status update | Applies to all PR entries unless `--pr`. Auto-derives task status; the task is only `done` once **every** PR is `merged` (no purge — merged tasks persist as a record). |
| `review-check` | Poll GitHub, mark merged/closed PRs, mark fully-merged tasks `done` | Skips done/cancelled. Re-derives status; a task becomes `done` only when every PR has merged. |
| `review-msg <id>` | Print a templated review-request message | |
| `blocked-scan [--update] [--json]` | Check every `blocked` review task for author activity | For each PR: counts commits since `submitted_at`, groups thread replies, flags pushback (`won't fix`, `by design`, `next step`…) and addressed markers (`done`, `fixed`…). Compact output (~2–3 KB). `--update` auto-flips PRs with new commits to `waiting-for-re-review` and moves the task back to `todo`. |

Review statuses: `pending`, `in-progress`, `commented`, `changes-requested`, `waiting-for-re-review`, `approved`, `merged`.

Review lifecycle: `todo` → `in-progress` → `blocked` (comments/changes-requested submitted) → `todo` (author updated) → … → `approved` (reviewer finished, task stays `in-progress`) → `done` (only when **all** PRs are **merged**). Approval is *not* completion — the task keeps tracking until every PR merges. Never mark a review task `done` after submitting non-approval feedback — `review-update` enforces this.

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
5. **Record workspace occupancy.** Detect the workspace you are aligning (the `~/projects/<project>/<ws>` slot the repos live in). Before occupying it, check it is not already held by a *different* holder — a task **or** an ad-hoc hold (`tm.py workspaces`, backed by the [workspace-manager](../workspace-manager/SKILL.md) store). If free, claim it with `tm.py assign <id> <ws>` — this records the slot in `wm.py` and promotes `todo`→`in-progress`. If it is already held, don't clobber it: report the conflict and let the user decide (`--force` displaces the holder).
6. Update the task: each selected PR entry `review_status: in-progress` if `pending`; append a note. Use `tm.py review-update <id> in-progress` or edit the store. (`assign` in step 5 already handled the task `status`.)
7. Report: which repos switched to review branches, which to main, which were skipped (and why), and which workspace slot is now occupied.
8. **Check for existing HSI teammate reviews.** The whole HSI team now cross-reviews each other's PRs, so before drafting your own pass, pull what others already said. For each PR entry, run `gh pr view <pr> --json reviews,reviewThreads` (or `gh api /repos/<owner>/<repo>/pulls/<pr>/reviews` and `.../comments`) to fetch prior reviews and inline threads from other reviewers. Summarize per PR: who reviewed, their verdict (approved / changes-requested / commented), and the gist of their inline comments and any unresolved threads. **Feed this into the `pr-review` handoff** so your draft (a) doesn't duplicate points teammates already raised, (b) can agree with or push back on their findings, and (c) accounts for concerns the author may already be addressing. Skip resolved/outdated threads. Do **not** post anything — this is read-only context gathering.
9. **Auto-invoke `pr-review local`** — starting a review always runs the review, never just aligns the workspace. Hand off to the **`pr-review`** skill in local workspace mode against the just-checked-out branches, passing along the teammate-review summary from step 8. Do **not** stop after the checkout; the checkout exists to be reviewed.
   - **Fresh vs. re-review is decided automatically** (never ask, never wait for a keyword). For each PR entry, look for a prior baseline in this order: (a) a `reviewed @ <repo>:<sha>` note in the task (recorded by `done`); (b) the head SHA in the most recent [drafts/](../pr-review/drafts/) file for that PR; (c) the `commit.oid` of your last submitted GitHub review. If **no** baseline exists → **fresh review** (full local diff against the merge-base). If a baseline exists and the current head differs → **re-review** (diff `baseline..new-head`, triage only what changed). If a baseline exists and the head is identical → tell the user there are no new changes since the last review and skip the heavy pass (offer a re-read only if asked).
   - Multi-PR tasks: make the fresh/re-review decision per PR entry independently.
   - This stays drafts-only — `pr-review local` never posts to GitHub without explicit approval (its Hard Rules still apply).

### `pre-review` — batch auto-draft every new review task ("review new PRs")

Do an unattended **preliminary** pass over every review task you've **never looked at**, leaving a local `pr-review` draft ready for each one. This front-loads the mechanical read so that when the user sits down to review a PR themselves, your draft is already waiting. It **never posts to GitHub** — drafts only.

Trigger phrases: "review new PRs", "pre-review", "auto-draft the new reviews".

1. **Select the new tasks.** Read the store; pick every active review task (status `todo`/`in-progress`, not done/cancelled) whose notes do **not** already contain the `initial auto review draft done` marker. These are the never-looked-at tasks. If none qualify, report "no new review tasks to pre-review" and stop. List the selected tasks (ids + PR counts) before starting.
2. **Process one task at a time**, in order. For each task:
   a. **Align the workspace** using the `start` mechanics: for each PR entry, `git fetch origin <branch>` then `git checkout <branch>` (fall back to `git fetch origin pull/<pr>/head:<branch>` on a missing ref); switch every other workspace repo to its default branch (`main`). Never create `pr-NNN` branches, never use destructive resets. If a repo is dirty / checkout is blocked, **skip that task**, report it, and move on — do not force.
   b. **Auto-draft** by running **`pr-review local`** against the checked-out branches. First pull any existing reviews and inline threads left by HSI teammates (`gh pr view <pr> --json reviews,reviewThreads`) and feed that summary into the draft so it doesn't duplicate what others already raised. This produces local draft findings only; **do not submit, comment, or approve** anything on GitHub.
   c. **Mark the task** by appending the note `initial auto review draft done` (with the date) via `tm.py`. Do **not** change `review_status` — this is a preliminary pass, not an actual review; leave the task's status untouched so the user's own review flow (`start` → `pr-review`) still runs normally.
   d. Move to the next task.
3. **Return to a clean state.** After the last task, run the `stop` mechanics: switch every workspace repo back to its default branch (skip and report dirty repos; never force).
4. **Report** a summary: which tasks were drafted, where each draft lives, which tasks/repos were skipped (and why), and confirm the workspace is back on default branches.

Guarantees: drafts only (no GitHub writes), no destructive git, and no review-status transitions — the user still reviews each PR themselves later, now with a head start.

### `stop`

Switch every workspace repo back to its default branch. Skip (don't force) dirty repos and report them. Don't change task status unless asked. **Release the workspace slot** you were occupying so the store reflects that you stopped working there: `tm.py release <id>` (frees the slot in `wm.py`, leaves `status` untouched).

### `done <task-id> [outcome]` — close out a review pass

Combined `stop` + status update.

1. Switch all workspace repos to default branch (skip dirty ones; never force).
2. Set `review_status` on the relevant PR entries to the outcome (`commented`, `changes-requested`, `approved`, …). If unspecified, keep current.
3. Derive task status:
   - **All** PR entries `merged` → task `done`, clear `workspace`, set `completed`.
   - All PR entries `approved`/`merged` but not all merged → task stays `in-progress` (review complete, awaiting merge); keep tracking until every PR lands. **Release the workspace** (`tm.py release <id>`) since you're no longer actively editing it, but leave the task `in-progress`.
   - Outcome `commented`/`changes-requested` → task `blocked`, clear `workspace`.
   - Outcome `in-progress` (just stopping) → keep `in-progress`, release `workspace` (`tm.py release <id>`).
4. Always: `updated = today`, append a note describing the outcome.
5. Easiest path: `tm.py review-update <id> <review_status>` (handles the status derivation and auto-clears `workspace`/`workspace_since` for the `done`/`blocked`/`todo` outcomes). For the "still in-progress, just stopping" case, follow with `tm.py release <id>` to free the slot.
6. **Memoir update** — append lessons to `pr-review`'s memoir at `~/.copilot/skills/pr-review/references/memoir.md`:
   - Read it first to avoid duplicates and respect existing section names.
   - Consolidate: fold each lesson under the right theme heading and merge duplicates — no per-review log section.
   - Capture findings (posted and dropped), new patterns/anti-patterns, drop reasoning, process insights.
   - **Strip specifics** — no PR numbers, commit hashes, branch names, line numbers, task ids, or author names.
   - If a new recurring "What I Always Check" item emerged, add it to that section too.

### `blocked-scan` — has the ball come back to me?

For `blocked` review tasks, check whether the author acted. **Delegate the mechanical polling to `tm.py blocked-scan`** — do not fan out `github-pull-request_issue_fetch` calls (that dumps ~10–20 KB of file diffs per PR into context for state you don't need).

1. Run `python3 ~/.copilot/skills/task-manager/tm.py blocked-scan --update` (or `--json` if you want to post-process). The CLI:
   - For each `blocked` code-review task, for each unresolved PR entry, calls `gh pr view` (state / commits / HEAD / mergeable) and `gh api /pulls/N/comments` (threads).
   - Counts commits since `submitted_at`, groups author replies to your inline threads, and flags each reply with a `pushback` heuristic (`won't fix`, `by design`, `next step`, `out of scope`, `intentional`, …) or an `addressed` heuristic (`done`, `fixed`, `addressed`, `applied`).
   - Classifies each PR: `needs_re_review` (new commits) / `replies_only` / `no_activity` / `merged` / `closed`.
   - With `--update`: transitions PRs classified `needs_re_review` to `review_status: waiting-for-re-review` per-PR (multi-PR tasks: quiet PRs stay put) and moves the task back to `status: todo`.
2. Read the summary. Your judgment is still required for:
   - **`replies_only`** PRs — decide whether the author's reply is convincing enough to close the thread, or whether you want a re-review.
   - **`needs_re_review`** PRs with `pushback` flags — verify the author isn't blowing off a real concern; the heuristic is deliberately loose.
   - **`no_activity`** PRs that have been blocked for many days — decide whether to nudge the author.
3. Report to the user per task:
   ```
   ## T-NNN: <title> (blocked Xd)
   ### <repo>#<pr> — ⚠️ Author updated (N new commits)
   - Your comment on `<file>`: <topic> → Author replied: "<gist>"
   → **Needs re-review** (X/Y threads addressed)
   ```
   Indicators: ⚠️ new commits, 💬 replies only, ✅ no changes, 🔀 merged, ❌ closed.

Only fall back to `github-pull-request_issue_fetch` when you need the actual file diff for one specific PR — never for the sweep.

## Rules

1. **`tm.py` is the only writer to the store.** Use it for every mechanical mutation. Fall back to direct JSON edits only for the judgment branches above (`start`/`stop`/`done` git steps, `blocked-scan`, memoir).
2. **Never post to GitHub without explicit user approval** — that gate belongs to `pr-review`; `review-mgr` only queues and tracks.
3. **Curate, don't auto-add.** `scan` always goes through the tick step.
4. **Never destructive git.** No resets; skip and report dirty repos.
5. **Done gating:** a review task is `done` only when every PR is **merged**. Approval alone keeps the task `in-progress` (awaiting merge). Submitting comments/changes-requested → `blocked`.
6. **No purge — track until merged:** the task-manager keeps a review task until every PR merges; fully-merged tasks persist as a `done`/`merged` record (surfaced in the dashboard code-review section) rather than being deleted. `review-update` / `review-check` re-derive status without purging.
7. **One PR per repo per task.** A multi-PR review task must never contain two PRs targeting the same repo. `review-start` can only check out one branch per repo — a second PR in the same repo is unreachable locally, causes confusion about which code is under review, and forces the reviewer to context-switch branches mid-review. When adding PRs (via `scan` or `add-review`), split any same-repo collisions into separate tasks. When inheriting a task that already violates this, split it before `start`.
8. **Repo allowlists** live in [references/hsi-repos.txt](./references/hsi-repos.txt) (HSI IPs: D2D/PCIe/SE) and [references/fw-repos.txt](./references/fw-repos.txt) (broader FW team); keep them current as repos are added/retired.
9. **`pre-review` is drafts-only.** The batch auto-draft pass produces local `pr-review` drafts and never writes to GitHub or transitions review status. It marks each processed task with the `initial auto review draft done` note so subsequent runs skip it; that note is the sole "already looked at" signal.
10. **Consider teammate reviews.** The HSI team cross-reviews, so before drafting on any `start`/resume/`pre-review` pass, read the existing reviews and inline threads other reviewers left on the PR and factor them in — don't duplicate raised points, weigh their findings, and note concerns the author may already be handling. This is read-only; never post in response to a teammate's review without explicit approval.
