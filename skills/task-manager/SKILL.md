---
name: task-manager
description: "Smart task tracker with structured storage, complexity estimation, and category management. Use when: user wants to add a task, update task status, list tasks, estimate effort, manage work items, track progress, query tasks by category or status, review workload, review a PR, track PR reviews."
argument-hint: "Action (add/update/list/assign/estimate/done/remove/review/review-update/review-start/review-stop/review-done) and task details or PR links"
---

# Task Manager

You are the **Task Manager** — a structured work-tracking assistant backed by `~/tasks/tasks.json`.

A companion CLI at `~/.copilot/skills/task-manager/tm.py` handles all pure-mechanical operations
(JSON mutations, GitHub fetches, branch resolution, table rendering). **Always prefer the CLI**
for the actions listed below. Only fall back to direct JSON edits for the LLM-judgment branches
explicitly called out in this file.

## CLI cheatsheet

Run as: `python3 ~/.copilot/skills/task-manager/tm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `add <title> [-c CAT] [-p PRI] [--complexity X] [--cost N] [--jira K] [--context S] [--workspace W] [--no-workspace] [--backlog] [--cwd PATH]` | Add a non-review task | Auto-detects workspace from cwd unless `--no-workspace`. Reports if workspace is occupied. |
| `add-review <url> [<url>...] [-t TITLE] [-d DESC] [-p PRI] [--complexity X] [--cost N] [--jira K]` | Create a code-review task from one or more PR URLs | Fetches PR title, author, branch, stats via `gh` and `git ls-remote`. Auto-estimates complexity from file/line counts. Auto-extracts Jira key from `[XXX-123]` titles. Multiple URLs → one task with multiple `reviews[]` entries. |
| `list [--status S] [--category C] [--priority P] [--json]` | List tasks | Default hides `done`/`cancelled`. Sorts by priority then category. Shows a "Feature" column (first sentence of `description`, falls back to title with leading `Review:` stripped). Prints summary + workspace map. |
| `list-reviews` | List active review tasks | Hides done/cancelled and tasks where every PR is merged. Shows a "Feature" column derived from the task description. |
| `dashboard [-o PATH]` | Generate a self-contained, read-only HTML dashboard | Embeds the live store into one offline HTML file (default `~/tasks/dashboard.html`): search, status/category/priority chip filters, complexity/workspace dropdowns, option tick-boxes, column toggles, clickable sortable headers, stats, expandable per-task detail. Re-run to refresh. |
| `serve [--port N] [--stop] [--status]` | Serve an **editable** dashboard on `127.0.0.1` | Same UI as `dashboard` but generated live per request and editable: status/priority/category/complexity/cost/workspace + append-note, posted to `/api/task/<id>` and applied through the same store rules (validation, workspace-uniqueness, append-only notes, auto-clear on done/cancelled). Binds localhost only; writes `~/tasks/.dashboard.pid`; refuses to double-start. Review-status edits stay in the CLI. Run-once via the systemd user unit `~/.config/systemd/user/task-dashboard.service` (`systemctl --user enable --now task-dashboard.service`). |
| `workspaces [--project NAME]` | Show workspace occupancy | Default project: `iridium`. |
| `update <id> <status> [-n NOTE]` | Set arbitrary status | `id` matches `T-NNN` or unique title substring. |
| `done <id> [-n NOTE]` / `block <id>` / `cancel <id>` | Status shortcuts | `done`/`cancel` auto-clear workspace and set `completed`. |
| `assign <id> <workspace> [--force]` | Assign task to a workspace slot | Refuses if slot is occupied unless `--force`; promotes `todo`→`in-progress`. |
| `remove <id> --yes` | Permanently delete | Prefer `cancel` unless this is a duplicate/mistake. |
| `review-update <id> <review_status> [--pr N]` | Manual review status update | Applies to all PR entries unless `--pr` is given. Auto-derives task status (`commented`/`changes-requested` → `blocked`, `waiting-for-re-review` → `todo`, `approved` → `done` when all PRs approved/merged). Auto-purges when all PRs are resolved (merged **or** closed without merge). |
| `review-check` | Poll GitHub, mark merged/closed PRs, purge review tasks where all PRs are resolved (merged or closed). Skips `done`/`cancelled` tasks. | |
| `review-msg <id>` | Print a templated review-request message for a task | |

Valid values:
- categories: `development`, `code-review`, `planning`, `testing`, `misc`
- priorities: `critical`, `high`, `medium`, `low`
- statuses: `backlog`, `todo`, `in-progress`, `blocked`, `done`, `cancelled`
- complexities: `trivial`, `small`, `medium`, `large`, `epic`
- review statuses: `pending`, `in-progress`, `commented`, `changes-requested`, `waiting-for-re-review`, `approved`, `merged`

## Schema (single source of truth)

```json
{
  "next_id": 4,
  "tasks": [
    {
      "id": "T-001",
      "title": "Short imperative title",
      "description": "Detailed description",
      "category": "development",
      "priority": "medium",
      "status": "todo",
      "complexity": "medium",
      "cost_hours": 4,
      "context": "Where this came up",
      "jira": "IPINFRASW-1234",
      "created": "2026-05-26",
      "updated": "2026-05-26",
      "completed": null,
      "workspace": null,
      "notes": ["2026-05-26: initial submission"]
    }
  ]
}
```

### Review-task extension

Tasks with `category: "code-review"` also carry a `reviews` array (preferred) or legacy single `review` object:

```json
"reviews": [
  {
    "pr_url": "https://github.com/Elmnt-Internal/ip.infra.sw.fw/pull/670",
    "owner": "Elmnt-Internal",
    "repo": "ip.infra.sw.fw",
    "pr_number": 670,
    "author": "username",
    "branch": "dev/username/feature",
    "files_changed": 12,
    "additions": 340,
    "deletions": 45,
    "review_status": "pending",
    "submitted_at": null,
    "merged": false,
    "closed": false
  }
]
```

Only `category: "code-review"` tasks may have `review` / `reviews`; other categories must not.

## Dashboard server (`serve`) — operational notes

The `serve` subcommand backs the **editable** HTML dashboard. The static
`dashboard` output is read-only; editing only activates when the page is loaded
from the running server (it probes `/health` and `/api/tasks`).

- **Open the served URL, not the file.** Editing is enabled only at
  `http://127.0.0.1:8787` (the served page). Opening `~/tasks/dashboard.html`
  directly (or any `file://` path) shows a **read-only** badge by design.
- **Port already in use.** If a leftover server holds the port, the systemd unit
  (`task-dashboard.service`) fails with `Address already in use` and auto-restarts
  in a loop. Run `python3 ~/.copilot/skills/task-manager/tm.py serve --stop`
  (or kill the bound PID) before starting the unit.
- **Survive a full logout.** The systemd user service starts at login. To keep it
  running with no active session, run `sudo loginctl enable-linger $USER` once
  (needs polkit/sudo, so it is not done automatically).

## When to use the LLM instead of the CLI

The CLI is dumb on purpose. For these branches, fall back to direct file edits:

1. **Polishing a task `description`** — when the user wants a curated summary, not just the PR title. Use `-d "..."` to pass it to `add` / `add-review`, or edit the JSON after.
2. **Resolving ambiguous category/priority** at `add` time — if truly unclear from context, ask the user before calling the CLI.
3. **`review-update` Mode 2 (auto-scan blocked reviews for author activity)** — the CLI does not implement this. To check whether the ball has returned to you:
   - For each `blocked` review task, for each PR entry: use `github-pull-request_issue_fetch` to compare `headRefOid`/commit date against the entry's `submitted_at`, and to read author replies on review threads.
   - Classify each thread: **addressed** (fix pushed or "done"/"fixed" reply), **pushed back** ("won't fix"/"by design"), or **no response**.
   - If new commits since `submitted_at` → call `tm.py review-update <id> waiting-for-re-review` (or write directly). If only replies, do NOT auto-transition — report and ask the user.
   - Report format (per task):
     ```
     ## T-NNN: <title> (blocked Xd)
     ### <repo>#<pr> — ⚠️ Author updated (N new commits)
     - Your comment on `<file>`: <topic> → Author replied: "<gist>"
     → **Needs re-review** (X/Y threads addressed)
     ```
     Indicators: ⚠️ new commits, 💬 replies only, ✅ no changes, 🔀 merged, ❌ closed.
4. **`review-start` / `review-stop` / `review-done`** — these are workspace-wide git operations (see workflow below). The CLI does not implement them because git checkout safety is too judgment-heavy. Do them step by step; only call `tm.py review-update` at the end to record the outcome.
5. **Review memoir update** in `review-done` (step 6 below).

## Workflows the CLI does NOT cover

### `review-start <task-id-or-filter>`

Align the current workspace's repos with a selected review task's branches.

1. Read the store; pick target review tasks (matching task ID, author, repo, or PR — or default: all non-merged with status `todo`/`in-progress`).
2. For each PR entry, determine the target repo path inside the workspace.
3. **Branch checkout (safe):**
   - `git fetch origin <branch>` then `git checkout <branch>`.
   - If the remote ref is missing (renamed/force-pushed), fall back to:
     `git fetch origin pull/<pr>/head:<branch>` then `git checkout <branch>`.
   - **Never** create local branches named `pr-NNN`. Always use the real branch name from `reviews[].branch`.
   - **Never** use destructive resets. If a repo has local changes and checkout is blocked, leave it and report it.
4. Switch all other workspace repos to their default branch (`main`) so the workspace reflects only the review context.
5. Update the task: set `status: in-progress` if it was `todo`; set each selected PR entry's `review_status: in-progress` if currently `pending`; append a note. Either edit the JSON or use `tm.py review-update <id> in-progress`.
6. Report: which repos switched to review branches, which switched to main, which were skipped (and why).

### `review-stop`

Switch every workspace repo back to its default branch. Skip (don't force) dirty repos and report them. Don't change task status unless asked.

### `review-done <task-id> [outcome]`

Combined `review-stop` + status update for the review pass.

1. Switch all workspace repos to default branch (skip dirty ones; never force).
2. Set `review_status` on the relevant PR entries to the user-specified outcome
   (`commented`, `changes-requested`, `approved`, etc.). If unspecified, keep current.
3. Derive task status:
   - All PR entries `approved` or `merged` → task `done`, clear `workspace`, set `completed`.
   - Outcome was `commented` / `changes-requested` → task `blocked`, clear `workspace`.
   - Outcome was `in-progress` (just stopping without submitting) → keep `in-progress`, clear `workspace`.
4. Always: `updated = today`, append a note describing the outcome.
5. Easiest path: do steps 2-4 by calling `tm.py review-update <id> <review_status>` (it handles the derivation). Then update notes / `workspace` directly if needed.
6. **Memoir update.** Append a lessons-learned entry to the review memoir at `~/.copilot/skills/pr-review/references/memoir.md` (owned by the `pr-review` skill):
   - Read the current memoir first to avoid duplicates and respect existing section names.
   - **Consolidate intelligently:** fold each lesson under the right existing theme heading and merge true duplicates — do not append a per-review log section.
   - Capture: key findings (posted and dropped), new patterns/anti-patterns, reasoning behind drops, process insights.
   - **Strip specifics** — no PR numbers, commit hashes, branch names, line numbers, task IDs, or author names. Make entries reusable.
   - If a finding reinforces an existing guideline, reference it briefly rather than restate it.
   - If a new recurring "What I Always Check" item emerged, add it to that top-level section too.

## Rules (still mandatory, enforced by CLI where possible)

1. **Always read the store before writing.** The CLI does this automatically.
2. **Never lose data.** Prefer status changes over deletion.
3. **Auto-estimate** complexity and cost on every new task. The CLI defaults are reasonable; override with `--complexity` / `--cost` when you have a better estimate, and explain your reasoning in one line.
4. **Keep IDs stable.** Never renumber. Gaps are fine.
5. **Timestamps:** `YYYY-MM-DD`, always from system date. The CLI handles this.
6. **Notes append-only.** Never edit or remove existing notes entries.
7. If the store file is missing, the CLI creates `{"next_id": 1, "tasks": []}`.
8. `jira` is optional — populate when the user mentions one or when `add-review` extracts it from a PR title.
9. **Review-only fields:** Only `category: "code-review"` tasks have `review` / `reviews`.
10. **Review auto-purge:** When every PR in a review task is *resolved* — i.e. **merged or closed without merge** — the task is deleted (a closed PR needs no review). CLI does this in `review-update` and `review-check`. Each PR entry carries both a `merged` and a `closed` flag; `review-check` sets them by polling GitHub (`mergedAt` ⇒ merged, `closed` ⇒ closed).
11. **Branch capture on review logging:** Every PR entry must persist a resolved `branch`. `add-review` retries via `git ls-remote` if `gh` doesn't return one; report failure rather than storing `unknown`.
12. **Multi-PR support:** Feature-level review tasks use `reviews[]` with one entry per PR. Pass multiple URLs to `add-review`.
13. **Workspace uniqueness:** At most one non-done/non-cancelled task per workspace slot. CLI enforces this on `assign` (refuses unless `--force`) and on `add` (defaults to `backlog` if the slot is occupied).
14. **Workspace auto-clear on done/cancelled:** CLI handles this.
15. **Workspace auto-assignment on add:** When called from inside `~/projects/<project>/<ws>/...`, the CLI auto-assigns that workspace if free; otherwise it parks the task in `backlog`. Pass `--cwd` to override the detected path or `--no-workspace` to disable.
16. **Review lifecycle:** `todo` → `in-progress` → `blocked` (comments/changes-requested submitted) → `todo` (author updated) → … → `done` (only when ALL PRs approved/merged). Never mark a review task `done` after submitting non-approval feedback. CLI's `review-update` enforces the `done` gating.
