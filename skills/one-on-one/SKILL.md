---
name: one-on-one
description: "Legacy compatibility wrapper. Route all 1:1 prep/live/post/history and member-record operations to team-manager."
argument-hint: "Use team-manager for all new work. This wrapper only preserves backward invocation compatibility."
---

# one-on-one (legacy wrapper)

This skill is now **legacy**. The source-of-truth model moved from local Markdown files to the Team domain database owned by [`team-manager`](../team-manager/SKILL.md).

## Routing

For any request previously handled here, invoke [`team-manager`](../team-manager/SKILL.md) and execute there:

- 1:1 prep / live / end flow
- 1:1 history queries
- running notes and action tracking
- member profile reads/updates
- Confluence summary export (on demand)

## Legacy artifacts

The old Markdown files under `~/team-planner/members/` are historical inputs for migration and audit only; they are no longer authoritative records.
