# Snapshot Format

Weekly snapshots capture milestone state for trend analysis. Stored in `~/team-planner/snapshots/YYYY-MM-DD.json`.

## Schema

```json
{
  "date": "2026-06-04",
  "milestone": "iridium-system-sw-6.0",
  "milestone_short": "MS6",
  "sprint": "FW HSI MS6 Sprint 2",
  "team_size": 5,

  "tickets": {
    "total": 0,
    "by_status": {
      "done": 0,
      "in_review": 0,
      "in_progress": 0,
      "to_do": 0,
      "blocked": 0
    },
    "by_subsystem": {
      "pcie_ep": { "done": 0, "in_review": 0, "in_progress": 0, "to_do": 0, "blocked": 0 },
      "se": { "done": 0, "in_review": 0, "in_progress": 0, "to_do": 0, "blocked": 0 },
      "d2d": { "done": 0, "in_review": 0, "in_progress": 0, "to_do": 0, "blocked": 0 },
      "infra": { "done": 0, "in_review": 0, "in_progress": 0, "to_do": 0, "blocked": 0 },
      "other": { "done": 0, "in_review": 0, "in_progress": 0, "to_do": 0, "blocked": 0 }
    }
  },

  "effort": {
    "unit": "days",
    "source_field": "timeoriginalestimate",
    "total": 0,
    "done": 0,
    "done_wont_do": 0,
    "done_duplicate": 0,
    "done_other": 0,
    "in_review": 0,
    "in_progress": 0,
    "to_do": 0,
    "blocked": 0,
    "unestimated_tickets": 0,
    "completion_pct": 0.0,
    "code_complete_pct": 0.0
  },

  "capacity": {
    "nominal_days": 0,
    "focus_days_estimated": 0,
    "blocked_external_days": 0,
    "blocked_by_category": {
      "rtl": 0,
      "silicon": 0,
      "lab": 0,
      "board": 0,
      "host_driver": 0,
      "validation": 0,
      "release": 0,
      "other": 0
    }
  },

  "raid_summary": {
    "red": 0,
    "orange": 0,
    "yellow": 0,
    "green": 0,
    "total_open": 0
  },

  "assignee_load": [
    {
      "name": "",
      "in_progress_count": 0,
      "in_progress_days": 0,
      "in_review_count": 0,
      "blocked_count": 0,
      "to_do_count": 0,
      "done_count": 0
    }
  ],

  "confidence": {
    "overall": 0,
    "by_subsystem": {
      "pcie_ep": 0,
      "se": 0,
      "d2d": 0,
      "infra": 0
    },
    "justification": ""
  },

  "notes": ""
}
```

## Field Notes

- `effort` uses `timeoriginalestimate` from Jira (seconds ÷ 28800 = days). This team does not use story points (`customfield_10016`).
- `completion_pct` = `done (resolution=Done only) / total` by effort-days. Falls back to ticket count when estimates are missing.
- `code_complete_pct` = `(done + in_review) / total` by effort-days. `In Review` means the code is done (development complete, awaiting merge — see `jira-hsi-planning` → Jira Status Semantics), so this is the development-done view of progress. Keep it distinct from `completion_pct`.
- `in_review` (status + effort) tracks code-complete work that is not yet merged/closed. Never fold it into `done`.
- `done_wont_do` / `done_duplicate` / `done_other` track non-Done resolutions in effort-days for planning-churn visibility.
- `blocked_external_days` is cumulative for the snapshot period (typically since last snapshot).
- `confidence` is the lead's 1-10 assessment, with a one-line justification.
- `assignee_load` comes from Jira; `in_progress_days` is the sum of `timeoriginalestimate` for that person's in-progress tickets.

## Data Collection Note

The Atlassian MCP `searchJiraIssuesUsingJql` returns only: summary, status, issuetype, priority, created, description. It strips resolution, assignee, and estimate fields. A full snapshot requires individual `getJiraIssue` calls per ticket.
- `notes` captures anything notable: scope changes, dependency shifts, team changes.

## Trend Usage

When multiple snapshots exist, the skill can compare:
- Velocity: points completed per snapshot period
- Blocked days trend: is external blocking improving or worsening?
- Scope drift: total tickets/points growing or shrinking?
- RAID trend: are Red rows resolving or accumulating?
- Confidence trend: is the lead's assessment stable, improving, or degrading?
- Completion curve: are we on track for milestone end?
