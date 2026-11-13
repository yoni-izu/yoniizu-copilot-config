---
name: checkout-pr-branches
description: "Checkout local repos to PR branches. Use when: user pastes GitHub PR links, switching branches for PRs, checking out PR branches, testing PRs locally, multi-repo PR checkout."
argument-hint: "Paste one or more GitHub PR URLs"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Checkout PR Branches

## When to Use

When the user provides one or more GitHub pull request URLs and wants the corresponding local repos checked out to the PR branches.

## Procedure

**RTL gate:** Before checkout or a `wip` scan, classify every PR and the
workspace from repository names and manifest/file metadata. Do not use a local
RTL repository, a mixed workspace containing one, or a PR that changes HDL.
Follow [rtl-vm-policy](../rtl-vm-policy/SKILL.md): RTL review is done by asking
Neo (`~/bin/neo-ask "Review <repo>#<pr> (branch <head>) …"`), which returns
paraphrased findings only. If Neo is unavailable, report the prerequisite. FW and CSR software
header checkouts retain this workflow.

For a software PR, use the guarded `fw-workspace-git/scripts/pr_set.py`
checkout where available: it classifies complete changed-file metadata and
fetches the verified head SHA, rather than a floating branch. An unknown
classification or unavailable guard stops checkout. Its filename heuristic
is not proof that unmarked files or review text contain no HDL.
For a rename/copy with an unavailable old path, `--neo-assisted` may resolve
the classification only if Neo verifies all affected paths and the exact head
and base; an inconclusive answer still stops the whole set.

### 1. Parse PR URLs

Extract from each URL:
- **Owner**: the GitHub org (e.g. `Elmnt-Internal`)
- **Repo name**: the repository name (e.g. `ip.d2d.sw.fw`)
- **PR number**: the pull request number

URL format: `https://github.com/{owner}/{repo}/pull/{number}`

### 2. Find the workspace manifest

Resolve the active manifest from `.repo/metadata.json`; do not select an
arbitrary XML file from another revision or project. Read its
`<project name="..." path="...">` entries.

In the manifest, each `<project>` element has:
- `name`: the repo name (matches the GitHub repo name)
- `path`: the local directory path relative to the workspace root

Also note the `revision` attribute from the manifest — this is the **base fb branch** (e.g. `fb-iridium-2_9_1_1p0_983`). When returning repos to a clean state, checkout this fb branch, **not** `main`.

### 3. Match repos to local paths

For each PR repo name, find the matching `<project name="REPO_NAME" path="LOCAL_PATH">` in the manifest. The `path` attribute is the local directory where that repo is cloned, relative to the firmware/workspace root.

### 4. Get PR branch names

For each PR, use `gh` CLI to fetch the head branch name:

```bash
gh pr view {PR_NUMBER} --repo {OWNER}/{REPO} --json headRefName -q '.headRefName'
```

Run these in parallel or chained with `&&` and `echo "---"` separators for efficiency.

### 5. Check out the classified software PR set

Use the guarded helper; it fetches the verified PR-head SHA, not a branch that
could change after classification. If the helper or its guard is unavailable,
stop rather than falling back to an unguarded local fetch. Existing branches
and dirty work are preserved. The resulting branch tracks the verified PR head
remote, changing the target of future plain pull/push commands.

```bash
python3 ~/.copilot/installed-plugins/ai-toolkit/fw-soc/general/skills/fw-workspace-git/scripts/pr_set.py checkout {PR_URLS} --ws {WORKSPACE_ROOT}
```

### 5b. Snapshot the aligned workspace (token savings)

Whole-workspace checkouts should go through the [wip-workspace](../wip-workspace/SKILL.md)
CLI so re-aligning the same PR set later costs **one** cheap command. Add
`--save prset-<name>` to the guarded checkout; it preflights the whole WIP scan
scope before invoking `wip`.

Before restoring, independently verify that the saved snapshot and workspace
still exclude RTL and mixed HDL. Then:

```bash
wip load prset-<name> && wip all checkout
```

Return the whole workspace to its clean base branch with `wip all return`.

### 6. Report results

Present a summary table:

| Repo | Local Path | Branch | Status |
|------|-----------|--------|--------|
| repo-name | path | branch-name | OK / Error |
