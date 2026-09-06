---
name: checkout-pr-branches
description: "Checkout local repos to PR branches. Use when: user pastes GitHub PR links, switching branches for PRs, checking out PR branches, testing PRs locally, multi-repo PR checkout."
argument-hint: "Paste one or more GitHub PR URLs"
---

# Checkout PR Branches

## When to Use

When the user provides one or more GitHub pull request URLs and wants the corresponding local repos checked out to the PR branches.

## Procedure

### 1. Parse PR URLs

Extract from each URL:
- **Owner**: the GitHub org (e.g. `Elmnt-Internal`)
- **Repo name**: the repository name (e.g. `ip.d2d.sw.fw`)
- **PR number**: the pull request number

URL format: `https://github.com/{owner}/{repo}/pull/{number}`

### 2. Find the workspace manifest

Look for the repo manifest XML that maps repo names to local paths. Common locations:
- `.repo/manifests/` directory — search for XML files
- Look for `<project name="..." path="...">` entries

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

### 5. Checkout and pull each repo

For each repo, fetch, checkout, and pull the PR branch:

```bash
cd {WORKSPACE_ROOT}/{LOCAL_PATH} && git fetch origin {BRANCH} && git checkout {BRANCH} && git pull origin {BRANCH}
```

### 5b. Snapshot the aligned workspace (token savings)

Whole-workspace checkouts should go through the [wip-workspace](../wip-workspace/SKILL.md)
CLI so re-aligning the same PR set later costs **one** cheap command instead of
re-parsing the manifest and re-querying `gh`. Once the repos are on their PR
branches, record and snapshot the state:

```bash
cd {WORKSPACE_ROOT}
wip scan                    # record the current branch of every repo
wip save prset-<name>       # snapshot -> ~/.wip/saved/prset-<name>.wip
```

To restore this exact set later (no `gh`, no manifest parse, no per-repo loop):

```bash
wip load prset-<name> && wip all checkout
```

Return the whole workspace to its clean base branch with `wip all return`.

### 6. Report results

Present a summary table:

| Repo | Local Path | Branch | Status |
|------|-----------|--------|--------|
| repo-name | path | branch-name | OK / Error |
