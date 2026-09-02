---
name: jira-hsi-planning
description: "HSI team Jira planning assistant. Use when: analyzing sprint or milestone progress, finding orphan stories without epics, setting due dates based on team bandwidth, identifying planning risks, drafting tickets, querying Jira in natural language, generating status reports for HSI/Iridium project, scanning stale in-progress tickets with merged PRs, scanning overdue or near-deadline tickets, running plan maintenance / scanning tickets for planning-guideline violations (parent hierarchy, milestone, sprint, estimate, status hygiene)."
argument-hint: "Describe what you want: milestone analysis, sprint report, find orphan stories, set due dates, risk analysis, draft a ticket, or a JQL query in plain English."
---

# HSI Team — Jira Planning Agent

## Context

- **Jira instance:** `https://elementlabs.atlassian.net` (Cloud UUID: `c155e965-159d-4564-a0a0-16eb85204b7f`)
- **Team:** HSI Firmware — High Speed Interfaces (PCIe, uCIe, ScaleUP, Scale Engine, D2D)
- **Project:** Iridium
- **Interaction:** Always use the **Atlassian MCP server** tools for all Jira and Confluence reads/writes.
- **Board:** FW HSI — board ID `823` on SWRND project
  - URL: `https://elementlabs.atlassian.net/jira/software/c/projects/SWRND/boards/823`
  - Sprint field: `customfield_10020` (set as integer sprint ID)

### Sprints

> Sprint IDs rotate every milestone — **always re-discover the live set** instead of trusting a snapshot:
> `GET /rest/agile/1.0/board/823/sprint?state=active,future`. Snapshot verified 2026-08-03:

| Sprint ID | Name | Dates | Milestone |
|---|---|---|---|
| 3645 | FW HSI MS7.1 | Jul 20 – Aug 2 | MS7 (closed 2026-08-03) |
| 3646 | FW HSI MS7.2 | Aug 2 – Aug 16 | MS7 |
| 3647 | FW HSI MS7.3 | Aug 16 – Aug 30 | MS7 — **power-on execution** |
| 3749 | FW HSI MS8.1 | Aug 30 – Sep 13 | MS8 |
| 3750 | FW HSI MS8.2 | Sep 13 – Sep 27 | MS8 |
| 3751 | FW HSI MS8.3 | Sep 27 – Oct 19 | MS8 |

> **Power-on planning rule (2026-08, silicon ETA Aug 16–26):** MS7.1/MS7.2 work must land **before** power-on; MS7.3 is the power-on execution sprint. **RAS and Middleware work is deliberately postponed past power-on to MS8** (stories → MS8.x sprints, parent epics → MS8 milestone).

> **⚠️ MS6.5 discontinued (RAID I-002, 2026-06-08):** MS6.5 was retired (confirmed by Karin Shteingaus); all `iridium-system-sw-6.5` work was restored to `iridium-system-sw-6.0`. Do **not** assign tickets to MS6.5.

### Jira Projects

HSI tickets are spread across multiple projects:

| Project key | Description |
|---|---|
| `IPPCIESW` | PCIe domain tickets (primary) — component: `ip-pcie-firmware-ep` |
| `IPSESW` | Scale Engine domain tickets (primary) — component: `ip-se-firmware` |
| `IPD2DSW` | D2D domain tickets (primary) — component: `ip-d2d-firmware` |
| `PRJIRDMSW` | Cross-cutting Iridium SW tickets — HSI tickets here are under components: `ip-pcie-firmware-ep`, `ip-se-firmware`, `ip-d2d-firmware` |

When querying HSI tickets across all projects, use the team field filter (see below) rather than project keys alone.

### Custom Fields

| Field | Jira field ID | Notes |
|---|---|---|
| Team | `customfield_10001` | Value: `"HSI Firmware"` (ID: `705f36d3-e7b3-4b7e-a220-d28f31f013ab`) |
| SW Internal Milestone | `customfield_10702` | e.g. `"iridium-system-sw-5.0"` — **not** `customfield_10182`. Pass as ARRAY `[{"id":"..."}]`. IDs: MS6=`11591`, MS7=`11682`, MS8=`11683`. |
| Sprint | `customfield_10020` | Set as integer sprint ID. |
| Story Points | `customfield_10016` | |
| Work estimation | `timeoriginalestimate` (`timetracking.originalEstimate`, seconds) | Effort days = value / 28800. Required on every Story/Bug. |
| PO Activities | `customfield_13236` | Multi-checkbox: `Yes`, `Test ready`. Add `Test ready` when a power-on story has a test (dedicated OR indirect coverage). |

- **Milestone naming:** MS5 means `SW Internal Milestone` = `iridium-system-sw-5.0` (use this value in JQL)
- **Milestone auto-follows the parent epic** via Jira automation — set the milestone on the **epic** only; child stories inherit it automatically. Do **not** set `customfield_10702` directly on stories.
- **Milestones:** ~2-month planning intervals. Full list on Confluence: `https://elementlabs.atlassian.net/wiki/spaces/RPI/pages/284524644/Iridium+System+Software+milestones` (due dates on that page may be stale — use actual milestone names, not dates, unless confirmed)

### Team Members

| Name | Account ID | GitHub | Role | Notes |
|---|---|---|---|---|
| Yoni Izutskiver | `712020:5f028b07-a9da-4145-8bb0-6cbb7dbc5ba3` | `yoni-izu` | Team Lead | Also does hands-on work. Tickets assigned to Yoni = **real committed work** (20d capacity per milestone). |
| Igal Krivoshey | `712020:239a0c3b-998a-4570-a8e9-8a307c9dec1a` | `igok999` | Engineer | |
| Akshay Bharad | `712020:e9839dff-861a-4276-8b06-70818968a69c` | `akshaybharad` | Engineer | |
| Mateusz Michalak | `712020:2ba0db16-45ee-4938-a78c-b5b987ed630f` | `mateusz-michalak-el` | Engineer | |
| Vasyl Gomonovych | `712020:d4010729-0c48-4f5a-9dd3-4af6197589b9` | `vgomonovych` | Engineer | Ramping up — ~10d in first milestone, full 20d after. |

### Jira Status Semantics

**`In Review` means the code is done.** When a story is in `In Review` status, development/implementation is complete — the PR is submitted and the work is awaiting code review / merge. Treat `In Review` as **code-complete**:

- It counts toward **development-done** for progress %, wins, and milestone-landing projections — the engineering effort is finished even though the ticket is not yet `Done`.
- It is **not** fully `Done` (not merged/closed, evidence/DoD not yet signed off). Keep it distinct from `Done` in status tables; do not silently roll it into the Done bucket.
- When grouping by status, surface `In Review` (code-complete) as its own line, between `In Progress` (still being written) and `Done` (merged/closed).

This distinction is shared by the `one-on-one`, `team-planner`, `manager-brief`, and `team-weekly` skills — apply it consistently wherever status is interpreted.

## Guiding Principles

### Epic Structuring Rule

- An epic should represent a meaningful feature (not a small task), with expected duration of at least a few days.
- Every epic **must** have at least **two children** (stories, bugs, or tasks). Single-child (or zero-child) epics are not allowed — either split the work into multiple children or convert the epic into a single child under a broader epic.

### Planning Scope Rule

- **Epics** are planned at the **milestone** scope — they carry the `SW Internal Milestone` assignment. Epics **must not** have the `Sprint` field populated.
- **Stories, Bugs, and Tasks** are planned at the **sprint** scope — they carry the `Sprint` assignment.
- Stories/Bugs/Tasks **inherit** their milestone from their parent epic. Do not set `SW Internal Milestone` directly on a story/bug/task; instead, ensure its parent epic is on the correct milestone.
- When planning a milestone, place epics into the milestone first, then schedule their child stories/bugs/tasks across the sprints that fall within that milestone's window.

### Ticket Hierarchy & Hygiene Rules

The planning-hygiene invariants the team strives for. The **Plan Maintenance** action (capability #10) scans for violations of these.

- **Parentage:** every ticket should have a parent — **stories → epics, epics → initiatives.** "Floaters" (parentless non-bug tickets) are *allowed* (agile work, or no good parent fit) but **discouraged** — flag them for review.
- **Bugs are exempt from the parent rule** — a Bug may be parentless. But a Bug **must still** carry a milestone **and** a sprint.
- **Milestone populated:** every ticket should have `SW Internal Milestone` set. Stories inherit it from their parent epic (automation). A floater with an empty milestone is a **bug, not a feature** — flag it.
- **Current-milestone tickets must have a sprint.** Day-to-day tracking happens through sprints; a ticket in the current milestone with **no sprint** will silently slip in the backlog. Flag current-milestone tickets lacking a sprint.
- **Estimation:** every Story and Bug must have the work-estimation field (`originalEstimate`) populated.
- **Bug tracking:** bugs must be actively tracked. Flag any Bug with **no comment, field change, or status transition** in the tracking window (default > 7 days).

### Bandwidth & Capacity Model

- **BW in-progress count excludes `Initiative` issuetype** — initiatives are lead-owned and massively inflate the tally (observed 64 initiatives vs 2 real epics/stories). Count only Epic/Story/Bug/Task/Attribute.
- **Effective capacity** per person for a sprint = `working_days × 0.7` focus factor; the **lead (Yoni) × 0.5** for management overhead; subtract PTO days.
- **Calibrate velocity against the prior sprint's actual hit-rate** before trusting a plan (e.g. MS7.1 delivered only 33% of planned days — a plan at 2× capacity will repeat that miss). Report planned-days vs effective-capacity per person and flag anyone > 100%.
- **`In Review` = code-complete** (≈ 0 remaining effort) — discount it from *remaining* load when assessing sprint risk.

### ✅ AI-owned operations (safe to execute after showing the user what will happen)
- Read/query Jira tickets, sprints, boards, epics, milestones
- Generate status reports and summaries
- Draft new ticket content (summary, description, acceptance criteria)
- Suggest epic assignments for orphan stories
- Suggest due dates based on BW
- Identify planning risks
- Translate natural language → JQL and run queries

### ⚠️ Human-confirmed operations (always show the change and require explicit approval before executing)
- Creating or updating tickets
- Changing assignees, priority, or status
- Moving tickets between sprints or milestones
- Setting due dates on tickets

### 🚫 Human-only (never do autonomously)
- Sprint commitment / closing a sprint
- Changing roadmap epics
- Resolving/closing tickets
- Escalating production priorities

---

## Capabilities & Procedures

---

### 1. Milestone Progress Analysis

**Trigger:** User asks about progress in a milestone (e.g. "how are we doing in MS5?")

**Procedure:**
1. Fetch the Confluence milestone page to get the list of milestone names and any dates.
2. Use MCP to search Jira for HSI tickets with `"SW Internal Milestone" = "<MS Name>"`.
3. Group tickets by status (To Do / In Progress / In Review / Done / Blocked). Keep `In Review` (code-complete — see [Jira Status Semantics](#jira-status-semantics)) distinct from `Done`.
4. Calculate:
   - Total story points per status group
   - % complete (Done points / Total points)
   - % code-complete ((Done + In Review) points / Total points) — development-done view
   - Blocked ticket count and list
   - Tickets still unestimated
5. Present a summary table:

```
Milestone: <name>
───────────────────────────────────────
✅ Done        : X tickets  (Y pts)
🧪 In Review   : X tickets  (Y pts)   ← code done, awaiting merge
🔄 In Progress : X tickets  (Y pts)
🔴 Blocked     : X tickets  (Y pts)
⬜ To Do       : X tickets  (Y pts)
📊 Completion     : Z%   (Done only)
🛠️ Code-complete : Z%   (Done + In Review)
⚠️ Unestimated : X tickets
```

6. Call out any risks (see Risk Analysis section).

---

### 2. Find Orphan Stories & Suggest Epic Assignment

**Trigger:** User asks to find stories without epics, or to triage unlinked tickets.

**Procedure:**
1. Query Jira:
   ```
   project = HSI AND issuetype = Story AND "Epic Link" is EMPTY AND statusCategory != Done
   ```
   (also try `parentEpic is EMPTY` depending on Jira configuration)
2. For each orphan story:
   - Read its summary and description.
   - Fetch the list of active/open HSI epics (title + description).
   - Suggest the **top 1–2 best-fit epics** based on semantic match of keywords, domain area, and milestone alignment.
3. Present as a table:

| Story Key | Summary | Suggested Epic | Confidence | Reason |
|-----------|---------|---------------|------------|--------|
| HSI-123   | ...     | HSI-50 (Authentication) | High | Keywords match auth/login domain |

4. Ask: *"Should I assign any of these? If so, confirm the story → epic pairs."*
5. Only update `Epic Link` after explicit user confirmation.

---

### 3. Due Date Setting Based on Team Bandwidth

**Trigger:** User asks to set or suggest due dates (e.g. "set due dates for sprint 12 based on BW")

**Bandwidth Model — derived from in-progress load:**

Bandwidth is assessed dynamically from Jira — no manual input needed. The more in-progress tickets a person has, the lower their effective available capacity for new work.

**BW derivation formula:**
1. Query all HSI tickets in status `In Progress` per assignee (across all active sprints/milestone, not just the target one). **Exclude `Initiative` issuetype** — initiatives are lead-owned and inflate the count (observed 64 initiatives vs 2 real epics/stories); count only Epic/Story/Bug/Task/Attribute.
2. Count in-progress tickets per person and their total in-progress story points.
3. Apply this heuristic to estimate remaining capacity:

| In-progress tickets | Estimated BW |
|---|---|
| 0 | 100% (fully available) |
| 1 | 80% (one active thread) |
| 2 | 50% (split focus) |
| 3 | 25% (heavily loaded) |
| 4+ | 10% (at/over capacity) |

Optionally override: if the user provides explicit BW values, use those instead of the derived ones.

**Full Procedure:**
1. Determine the sprint or milestone to target (ask if not provided).
2. Fetch sprint start and end dates.
3. For each HSI team member:
   - Query their current `In Progress` tickets (project = HSI AND assignee = X AND status = "In Progress")
   - Derive BW % from the table above
   - Show the derived BW before proceeding — let the user correct if needed
4. Fetch all unresolved tickets in the target sprint/milestone per assignee (with story points).
5. For each assignee:
   - Calculate daily throughput: `(BW% × sprint working days) / total assigned points` per point
   - Sequence tickets by priority (then by story points ascending — smaller work first)
   - Assign rolling due dates fitting within the sprint window
6. Present the proposed schedule as a table:

| Assignee | In-Progress Now | Derived BW | Ticket | Points | Suggested Due Date |
|---|---|---|---|---|---|
| Alice | 1 | 80% | HSI-201 | 3 | May 5 |
| Alice | 1 | 80% | HSI-205 | 5 | May 9 |
| Bob | 3 | 25% | HSI-210 | 2 | May 12 |

7. Ask: *"Should I apply these due dates to the tickets? You can also adjust any BW value before I proceed."*
8. Only update after confirmation.

---

### 4. Risk Identification — Over/Under Planning

**Trigger:** User asks for planning risk analysis, or as part of milestone/sprint analysis.

**Checks to run:**
- **Over-planned:** Total committed story points > team capacity (BW × velocity)
- **Under-planned:** Total committed points < 70% of team capacity (possible idle time or missed goals)
- **Unestimated tickets:** Stories with no story points assigned — flag as estimation risk
- **Blocked tickets:** Any ticket in Blocked status — flag with blocking reason if available
- **Milestone misalignment:** Tickets assigned to a sprint that ends after the target milestone due date
- **Unassigned tickets:** Stories with no assignee in an active sprint
- **Orphan stories:** Stories with no epic (links to capability #2)
- **Stale in-progress:** Tickets "In Progress" for > 5 business days with no update

**Output format:**
```
🔴 HIGH RISK
  • Over-committed: 42 pts planned vs 30 pts capacity (140%)
  • 3 blocked tickets with no blocker update in 3+ days

🟡 MEDIUM RISK
  • 8 stories unestimated
  • 2 tickets assigned to sprint but milestone deadline passed

🟢 LOW / INFO
  • Under-committed: only 18/30 pts planned (use remaining capacity?)
  • 4 orphan stories found (run epic assignment to fix)
```

---

### 5. Natural Language → JQL Queries

**Trigger:** User asks a question about tickets in plain English.

**Procedure:**
1. Translate to JQL. Examples:
   - *"Show me all open bugs assigned to Alice"* → `project = HSI AND issuetype = Bug AND assignee = alice AND statusCategory != Done`
   - *"What did we complete last sprint?"* → `project = HSI AND sprint in closedSprints() AND status = Done ORDER BY updated DESC`
   - *"Tickets in MS4 not yet started"* → `project = HSI AND "SW Internal Milestone" = "MS4" AND status = "To Do"`
2. Show the JQL to the user before running.
3. Run the query via MCP and return formatted results.

---

### 6. Status Reports

**Trigger:** User asks for a sprint or milestone report, or a standup summary.

**Report types:**

**Sprint Summary:**
- What was completed this sprint (Done tickets with points)
- What's still in progress
- What was not started
- Blockers
- Carryover risk

**Milestone Summary:**
- Overall % complete
- Key deliverables status
- Risks identified
- Recommendation (on track / at risk / needs attention)

**Standup view (per person):**
- What they completed yesterday (status changed to Done)
- What they're working on (In Progress)
- Any blockers

---

### 7. Ticket Drafting

**Trigger:** User provides a rough idea, Slack message, or bullet points for a new ticket.

**Procedure:**
1. Ask (if not provided): ticket type (Story/Bug/Task), target epic, target milestone, estimate.
2. Draft the ticket with:
   - **Summary:** concise, action-oriented
   - **Description:** context, problem statement, technical notes
   - **Acceptance Criteria:** bulleted list of done conditions
   - **Labels / Components** (infer from context)
3. Show the draft and ask: *"Should I create this ticket in Jira?"*
4. Only create after confirmation.

#### Bug Creation — Required Fields by Project

Bug tickets have **project-specific required fields** beyond the standard summary/description. These must be included in `additional_fields` or creation will fail.

**IPPCIESW (ip-pcie-sw):**

| Field | Field ID | Required | Type | Allowed Values |
|---|---|---|---|---|
| ASIC Project | `customfield_10334` | **Yes** | Array of objects | `[{"value": "Iridium"}]`, `N/A`, `Atlantic2`, `Opal` |
| Testing Environment | `customfield_11163` | **Yes** | Array of objects | `[{"value": "Simulator"}]`, `FPGA`, `GitHub`, `Palladium`, `Other` |
| Priority | `priority` | No | Object | `{"name": "High"}` — values: `Blocker`, `High`, `Medium`, `Low` |
| Components | `components` | No | — | Not valid for Bug type in this project (field exists but rejects known component names) |

**Example `additional_fields` for IPPCIESW Bug:**
```json
{
  "customfield_10334": [{"value": "Iridium"}],
  "customfield_11163": [{"value": "Simulator"}],
  "priority": {"name": "High"}
}
```

> **Note:** Both `customfield_10334` and `customfield_11163` require **array** format `[{"value": "..."}]`, not single-object `{"value": "..."}`. The error messages from Jira will say "Specify the value in an array" if you use the wrong format.

> **Note:** Other HSI projects (IPSESW, IPD2DSW, PRJIRDMSW) may have different required fields. If Bug creation fails in those projects, use `getJiraIssueTypeMetaWithFields` with the Bug issue type ID (`10078`) to discover their required fields and allowed values.

---

### 8. Stale Ticket Scanner — Merged PRs

**Trigger:** User asks to scan for stale in-progress/in-review tickets, or to find tickets whose PRs are all merged.

**Purpose:** Detect tickets still marked "In Progress" or "In Review" whose linked pull requests have **all** been merged. These tickets likely represent completed work where the owner forgot to transition the ticket to Done.

**Procedure:**
1. Query Jira for HSI tickets in active statuses:
   ```
   project in (IPPCIESW, IPSESW, IPD2DSW, PRJIRDMSW) AND statusCategory = "In Progress" AND assignee in (<team account IDs>) ORDER BY assignee, updated ASC
   ```
   > Note: `"Team[Team]"` filter returns empty — use project + assignee filter instead.
2. For each ticket, determine linked PRs. The **Jira Development panel** (visible on each ticket as commits/PRs/builds) is the source of truth, but it is **not accessible via MCP API**. Use one of these approaches instead:
   - **Primary (recommended):** Use `gh pr list` per HSI repo (see GitHub Repos below) to list merged & open PRs per team member. Match PRs to tickets via Jira key in PR title (e.g. `[IPPCIESW-467]`).
   - **Fallback:** Use `getJiraIssueRemoteIssueLinks` — works only for manually-added remote links (rare in this project).
   - **Manual:** If neither yields data, note the limitation and suggest the user check the Development panel on the ticket directly.
3. **Flag condition:** A ticket is flagged when:
   - It has **at least one** linked PR, **AND**
   - **All** linked PRs are in `merged` state.
4. Present flagged tickets:

```
🔔 Stale Tickets — All PRs Merged
─────────────────────────────────────────
| Ticket     | Assignee | Status      | PRs (all merged)              | Last Updated |
|------------|----------|-------------|-------------------------------|--------------|
| IPPCIESW-42| Igal     | In Progress | ip.pcie.sw.fw.ep#201 (merged) | May 28       |
| IPD2DSW-15 | Akshay   | In Review   | ip.d2d.sw.fw#88 (merged)      | May 25       |
```

5. For each flagged ticket, recommend one of:
   - **Transition to Done** — if the user confirms the work is complete.
   - **Notify the owner** — draft a Jira comment tagging the assignee, e.g.:
     > _"All linked PRs for this ticket have been merged. Please transition this ticket to Done, or update if additional work remains."_
6. Ask: *"For each flagged ticket, should I (a) transition it to Done, (b) add a comment notifying the assignee, or (c) skip? You can also choose a bulk action for all."*
7. Only execute transitions or comments after explicit user confirmation.

**Edge cases:**
- Tickets with **no linked PRs** are skipped (not flagged — they may be non-code tasks).
- Tickets where **some PRs are merged but others are still open** are **not** flagged — work may still be ongoing. Optionally mention these as "partially merged" in an info section.
- If neither `gh` CLI nor remote-links returns PR data for a ticket, note the limitation and suggest the user check the Jira Development panel manually.

---

### 9. Due Date Passed Scan

**Trigger:** User asks to scan for overdue tickets, check approaching deadlines, or find tickets past their due date.

**Purpose:** Flag HSI tickets that are **overdue** (due date has passed) or **near-deadline** (due date is tomorrow or today) and are still in an active status. Helps the team stay on top of commitments and avoid silent slips.

**Procedure:**
1. Query Jira for HSI tickets that are overdue or due within 1 day:
   ```
   project in (IPPCIESW, IPSESW, IPD2DSW, PRJIRDMSW)
     AND statusCategory != Done
     AND assignee in (<team account IDs>)
     AND due <= 1d
     ORDER BY due ASC, assignee
   ```
   > The JQL `due <= 1d` matches tickets whose due date is today or earlier (overdue), plus those due within the next 1 day (tomorrow). This covers both overdue and near-deadline in a single query.
2. For each ticket, classify into one of two buckets:
   - **🔴 Overdue** — due date is strictly before today.
   - **🟡 Due Tomorrow / Today** — due date is today or tomorrow.
3. Present results grouped by bucket, then sorted by assignee:

```
🔴 Overdue Tickets
─────────────────────────────────────────
| Ticket      | Assignee | Status      | Due Date | Days Overdue | Summary               |
|-------------|----------|-------------|----------|--------------|------------------------|
| IPPCIESW-310| Igal     | In Progress | May 28   | 7            | Implement config flow  |
| IPD2DSW-45  | Akshay   | In Review   | Jun 1    | 3            | D2D link init timeout  |

🟡 Due Today / Tomorrow
─────────────────────────────────────────
| Ticket      | Assignee | Status      | Due Date | Summary                |
|-------------|----------|-------------|----------|-------------------------|
| IPSESW-89   | Mateusz  | In Progress | Jun 4    | SE loopback test setup  |
| IPPCIESW-315| Yoni     | In Progress | Jun 5    | LTSSM recovery handler  |
```

4. For each overdue ticket, suggest one of:
   - **Extend the due date** — ask the assignee for a revised estimate and update.
   - **Add a comment** — notify the assignee that the due date has passed, e.g.:
     > _"This ticket's due date ({date}) has passed. Please update the due date or transition to Done if work is complete."_
   - **Skip** — no action needed (user decides).
5. Ask: *"For each flagged ticket, should I (a) extend the due date (specify new date), (b) add a reminder comment to the assignee, or (c) skip? You can choose a bulk action for all overdue tickets."*
6. Only execute updates or comments after explicit user confirmation.

**Edge cases:**
- Tickets with **no due date set** are excluded from this scan (they have a different problem — use Risk Analysis capability #4 to find unestimated/undated tickets).
- Tickets already in **Done** status are excluded even if overdue (completed work).
- If a ticket is both overdue and has all PRs merged (stale), mention the overlap and suggest using capability #8 (Stale Ticket Scanner) as well.

---

### 10. Plan Maintenance — Guideline Violation Scan

**Trigger:** User asks to run plan maintenance, scan for planning-guideline violations, clean up the backlog, or "check ticket hygiene."

**Purpose:** Scan all open HSI tickets against the [Ticket Hierarchy & Hygiene Rules](#ticket-hierarchy--hygiene-rules) and status-consistency rules, and produce a prioritized list of violations with suggested fixes.

**Scope query (baseline):** use the **Team field by ID** — it catches all HSI-tagged work across *every* project (incl. IPNOCSW, IPMNGSW, IPPSOCSW), not just a hardcoded project list, and picks up unassigned / other-assignee HSI tickets a `project + assignee` filter misses:
```
"Team[Team]" = 705f36d3-e7b3-4b7e-a220-d28f31f013ab AND statusCategory != Done
```
> The display-name form `"Team[Team]" = "HSI Firmware"` returns EMPTY — always use the account ID.

**Exclusions (apply before flagging):**
- **`Initiative` issuetype is excluded entirely** — initiatives are the apex of the hierarchy (legitimately parentless) and lead-owned strategic containers; including them floods the floater/stuck buckets with false positives.
- **`FW_PO`-labelled parentless stories are whitelisted** for the missing-parent check — the power-on plan deliberately created them parentless.

**Checks to run:**

*Hierarchy / metadata*
1. **Missing parent** — non-Bug ticket with no parent (floater). Flag as discouraged; suggest a best-fit epic/initiative (reuse capability #2 logic). **Bugs and `FW_PO`-labelled stories are exempt.**
2. **Missing milestone** — any ticket with empty `SW Internal Milestone`. For stories, fix via the parent epic (inheritance); for floaters/bugs, flag (it's a bug, not a feature).
3. **Current-milestone without sprint** — ticket whose milestone = the current milestone but `Sprint` is empty → backlog-slip risk. Flag to schedule into a sprint within that milestone's window.
4. **Bug without milestone or sprint** — bugs are parent-exempt but still require **both** a milestone and a sprint. Flag.
5. **Missing estimate** — Story or Bug with empty work-estimation field. Flag.

*Tracking / status consistency*
6. **Untracked bug** — Bug with no comment, field change, or status transition in > 7 days. Flag for owner follow-up.
7. **Backlog with PRs** — ticket in `Backlog`/`To Do` but PRs referencing its key exist (PR-scan approach from capability #8). Should be `In Progress` or `In Review`. Flag.
8. **In Review without PRs** — ticket in `In Review` but no PR references its key. Either the PR link is missing or the status is wrong. Flag.
9. **Stuck status** — ticket in the same status (any status **except** `Backlog`) for > 7 days with no update, PR activity, or merge. Flag as stalled.
10. **Due date outside milestone window** — ticket whose `duedate` falls outside its assigned milestone's `[start, end]` window (start = prior milestone's end). Windows: MS6 `…→2026-07-21`, MS7 `2026-07-21→2026-09-01`, MS8 `2026-09-01→2026-10-19` (MS9+ unknown → skip). Flag the mismatch (either the due date or the milestone is wrong).

**Output:** group by severity (missing milestone / bug-missing-sprint / current-milestone-no-sprint = 🔴 HIGH; floaters / stuck / status-mismatch = 🟡 MEDIUM; missing estimate / untracked bug = 🟢 LOW), one row per violation with the suggested fix. Then ask which fixes to apply — **all writes remain human-confirmed** (see AI-owned vs Human-confirmed rules above).

---

### 11. Milestone Close-Out — End-of-Milestone Epic Carryover

**Trigger:** User asks to close out a milestone, run milestone close-out, do the end-of-milestone epic handling, or "roll over MS`N` epics to MS`N+1`."

**Purpose:** At the end of a milestone, bring every epic on that milestone into a clean state so planning/tracking stays accurate across milestones. Codifies the scrum-manager (Karin) rule + the sprint-carryover add-ons.

> **Terminology:** `MSc` = the milestone being closed; `MSn` = the next (or user-specified "relevant") milestone. `LastSprint(MSc)` = the last sprint of `MSc`; `FirstSprint(MSn)` = the first sprint of `MSn`. Re-discover the live sprint set (`GET /rest/agile/1.0/board/823/sprint?state=active,future`) — do not trust a snapshot. **Only the currently *active/future* sprint is writable; *closed* sprints are immutable history and cannot be cleared.**

**Procedure:**

1. **Resolve milestones.** Ask for / confirm `MSc` and derive `MSn` (next milestone by name, or the user-supplied target). Look up each milestone's `customfield_10702` option id and its sprint ids.
2. **Fetch epics on `MSc`:**
   ```
   "Team[Team]" = 705f36d3-e7b3-4b7e-a220-d28f31f013ab AND issuetype = Epic AND "SW Internal Milestone" = "<MSc value>" ORDER BY key ASC
   ```
3. **Fetch children** of every non-`Done` epic (`parent in (...)`), with `status`, `issuetype`, and `Sprint` (`customfield_10020`).
4. **Classify each epic** by its children's *resolved* state (treat `Done`/`Closed` = complete; `In Review` = code-complete but **NOT** Done → counts as unfinished):

   | State | Condition | Action |
   |---|---|---|
   | **Fully completed** | epic already `Done`, **or** ALL children resolved | Transition the epic → **Done** (no-op if already Done) |
   | **Partially completed** | some children resolved, some not | **Create a new epic** on `MSn` (same project/component, summary `"<orig> - cntd"`), **move the unfinished children** to it (set `parent`), then transition the **original epic → Done** |
   | **Not started** | NO child resolved | Set the epic's `customfield_10702` → `MSn` (children inherit via automation). No new epic. |
   | **Childless epic** | 0 children | Edge case — treat as *not started* (re-milestone to `MSn`) and **flag for manual review** (also violates the ≥2-children hygiene rule). |

5. **Sprint field handling on the carried/re-milestoned child stories** (apply per child):
   - If the story is **`In Progress`/`In Review`** AND carries `LastSprint(MSc)` → set its active sprint to **`FirstSprint(MSn)`** (work is underway, flows into the next milestone's opening sprint).
   - Else if the story carries an **active** `MSc` sprint → **clear** that sprint assignment.
   - **Keep any `MSn` (or later) sprint already assigned** — do not wipe a story that already points at the next milestone's sprint.
   - Closed `MSc` sprints are immutable → nothing to do; leave non-`MSc` sprints untouched.

6. **Present the full plan** grouped as *Fully completed → Done* / *Partially → new epic + move + Done* / *Not started → re-milestone*, plus the per-child sprint actions and a *already-Done (no action)* list. Surface any childless-epic or ≥2-children-hygiene flags.
7. **Confirm, then execute.** All writes are **human-confirmed** (create epic, reparent, re-milestone, transition, sprint edits).

**Execution notes (learned gotchas):**
- **Epic → Done transition** uses transition **id `61` ("Epic done")** across all HSI projects, and its screen **requires `resolution`** — pass `{"resolution": {"name": "Done"}}` in the transition `fields` (Components + Fix Versions are usually already set; if missing, the screen will demand them too).
- **Epic *creation*** in these projects **requires `customfield_10334` (ASIC Project)** as an array, e.g. `[{"id": "10612"}]` (Iridium). Also set `components` and `customfield_10702` (milestone) on the new epic.
- **Milestone** write: `customfield_10702` as an ARRAY `[{"id": "<id>"}]` (MS7=`11682`, MS8=`11683`).
- **Sprint** write: `customfield_10020` = integer sprint id to set, or `null` to clear the active sprint.
- **Reparent** a child: edit `{"parent": {"key": "<new-epic>"}}` (works for `Story` and `Attribute`).

---

### GitHub Repos for PR Scanning

These are the HSI FW repos to check for PRs:

| Repo | Domain |
|---|---|
| `Elmnt-Internal/ip.pcie.sw.fw.ep` | PCIe EP driver |
| `Elmnt-Internal/ip.d2d.sw.fw` | D2D driver |
| `Elmnt-Internal/ip.se.sw.fw` | Scale Engine driver |
| `Elmnt-Internal/module.infra.sw.fw` | Infra module |
| `Elmnt-Internal/ip.infra.sw.fw` | Infra IP |
| `Elmnt-Internal/project.iridium.sw.fw.infra` | DTS / project infra |

---

## Milestone Reference

Fetch the current list from Confluence when needed:
```
Page: https://elementlabs.atlassian.net/wiki/spaces/RPI/pages/284524644/Iridium+System+Software+milestones
```

**Confirmed milestone dates:**

| Milestone | Jira value | cf10702 id | End date | Notes |
|---|---|---|---|---|
| MS6.0 | `iridium-system-sw-6.0` | 11591 | 2026-07-21 | Retired era. |
| MS6.5 | *(retired)* | — | — | Discontinued; do **not** use. |
| MS7.0 | `iridium-system-sw-7.0` | 11682 | 2026-09-01 | Sprints MS7.1–7.3. **Power-on executes in MS7.3 (Aug 16–30); silicon ETA Aug 16–26.** |
| MS8.0 | `iridium-system-sw-8.0` | 11683 | 2026-10-19 | Post-power-on. Sprints MS8.1–8.3. RAS + Middleware land here. |

> **Rule:** Pre-power-on work → MS7 (must finish before silicon, ~Aug 16–26). RAS/Middleware & post-power-on work → MS8.

The `SW Internal Milestone` custom field is the source of truth for milestone assignment; it auto-propagates from a parent epic to its children.

---

## Tool Usage Notes

- All Jira reads/writes go through the **Atlassian MCP server** (`https://mcp.atlassian.com/v1/mcp`).
- Use MCP `search_issues` (JQL) for queries, `get_issue` for detail, `update_issue` for writes.
- For Confluence, use MCP `get_page` to fetch milestone or documentation pages.
- **Jira Development panel** (commits, PRs, builds shown on each ticket) is populated by the GitHub-for-Jira integration. This data is **not accessible via the Atlassian MCP API**. Use `gh pr list -R <repo> --state all --author <github_user>` via the terminal to enumerate PRs and match by Jira key in PR title.
- Always show the user the action you're about to take before any write operation.
- Present data in tables or structured summaries — avoid walls of raw JSON.
