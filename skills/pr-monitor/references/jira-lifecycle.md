# Confirmed feature-to-Jira lifecycle

## Scope and authorization

A Jira reference is a link, not proof that a feature implements the entire ticket.
When logging a feature with Jira references, ask the user separately for each ticket:
does this exact PR set cover the whole ticket, including all required repositories?
Ask once per ticket and PR set. Do not auto-enable existing tracked features or
infer coverage from PR titles, descriptions, labels, or an implementation assessment.
Partial or uncertain coverage stays unconfirmed.
Consult existing `jira-coverage` notes before asking. An explicit user "no" is
recorded using `--revoke` for the current PR set, so future invocations do not
repeat the question unless the user changes that decision. A note with
`reason: pr-set-changed` is an automatic invalidation, not a new user answer;
ask again for the changed set.

After explicit approval, read the owner revision and record the decision:

```bash
rtk proxy python3 ~/.copilot/skills/pr-monitor/pm.py show 4
rtk proxy python3 ~/.copilot/skills/pr-monitor/pm.py confirm-jira 4 TEST-123 --expected-revision REV
```

Replace `REV` with the returned revision. A stale revision is rejected; reload and
show the user the changed PR set before confirming again. The owner appends a
`jira-coverage` JSON activity note containing the ticket, exact PR references and
confirmation flag. These notes live only in the private owner database. Do not
hand-author approval notes or edit the store. Portal confirmation uses the same API.
Adding or removing a PR revokes existing approvals, even if the old PR set is
later restored. Each ticket must be explicitly confirmed again.

To revoke automatic updates:

```bash
rtk proxy python3 ~/.copilot/skills/pr-monitor/pm.py confirm-jira 4 TEST-123 --expected-revision REV --revoke
```

## Automatic transitions

`pm.py refresh` and the Portal's Scan PRs action share the owner implementation.
They run without an AI model and require no interactive MCP session. They use
the configured private Jira site and existing credential resolution: environment
`ATLASSIAN_EMAIL`/`ATLASSIAN_TOKEN`, the site's netrc entry, then the existing
local Atlassian token/email files. Never copy credentials into tracked files,
activity notes, logs or browser payloads.

For each confirmed ticket, fetch the authenticated Jira identity and current
issue assignee/status. Only the authenticated user's assigned tickets qualify;
unassigned or reassigned tickets are left unchanged.

| Fresh feature state | Jira action |
|---|---|
| At least one non-draft PR still open, others open or merged | In Review |
| CI pending/failing, changes requested, needs rebase, approved or merge-queued | Keep In Review; do not bounce back to In Progress |
| Every PR merged | Done |
| Draft, closed-unmerged, unknown state, failed probe or no PRs | Block; do not transition |
| Same ticket referenced by another active feature | Block ambiguous completion scope |
| Jira already at target | Verify and report unchanged; no duplicate transition |
| Jira in a different terminal status | Block; never reopen automatically |

Discover live Jira transitions by **destination status**, not transition name or
hard-coded IDs. `In-Review`, `In Review`, and equivalent separator/case variants
are accepted. Exactly one available transition must target the requested status;
otherwise report blocked rather than guessing intermediate workflow hops.
No comments, assignments, sprint changes, resolution overrides or unrelated
field changes are performed. Required workflow fields must be handled explicitly
by the user; a rejected transition is an error, not success.

The final local-revision check, Jira ownership/status recheck, transition and
read-back are guarded against concurrent local edits. External Jira races cannot
be made transactional with the local store. An interrupted or uncertain write
is retried by reading actual Jira state first, never blindly replaying a POST.

## Reporting, retirement and cadence

The owner appends `jira-sync` result notes and exposes per-feature `jira_coverage`
and `jira_sync` fields. Refresh returns a top-level `jira_sync` list, including
retired-feature retries. Each result includes `feature_id`, `key`, `state`,
`message`, exact PR references and, when known, `target`.

Report updated, unchanged, blocked and error outcomes, with the reason. CLI refresh
returns nonzero for blocked/error Jira results or failed GitHub probes. Portal
scan results also surface Jira failures. Do not issue duplicate transitions via
Atlassian MCP after the owner has run.

All-merged features still retire normally. If their confirmed Done update was
blocked or failed, subsequent refreshes re-probe their PRs and retry Jira.
After verified Done, they stop being queried. Explicitly dropped features do not
retry. History remains accessible via `pm.py show ID`.

There is **no new polling daemon or schedule**: synchronization occurs whenever
the existing CLI/Portal refresh runs. Logging, confirmation, revocation,
read-only list/show/payload and manual removal do not transition Jira themselves.
Tests use isolated databases, fake Jira clients and mocked HTTP; do not validate
by moving a real ticket without separate authorization.
