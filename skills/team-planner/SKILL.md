---
name: team-planner
description: "Legacy compatibility wrapper. Route planning, member records, 1:1 lifecycle, and team-level forecasting to team-manager."
argument-hint: "Use team-manager for all new planning and member-record workflows."
---

# team-planner (legacy wrapper)

This skill is now **legacy**. Planning and member-record ownership moved to [`team-manager`](../team-manager/SKILL.md), which uses the Team database as source of truth.

## Routing

Delegate these requests to [`team-manager`](../team-manager/SKILL.md):

- milestone/sprint planning and forecasting
- RAID-aware status and briefing drafts
- member profile, goals, ownership, feedback, and assignment tracking
- 1:1 lifecycle and history
- metrics rollups (features, bugs, attributes, stories)
- optional Confluence summary exports

## Scope retained here

Only compatibility for existing invocation names. New functionality must be implemented in `team-manager`.
