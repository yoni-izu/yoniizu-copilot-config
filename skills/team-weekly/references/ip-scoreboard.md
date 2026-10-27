# Per-IP Scoreboard Rules

Used in prep (capability 1) and archive (capability 3) of the `team-weekly` skill to produce a one-line win + one-line gap per active IP.

## Active IPs and owner resolution

Resolve current owners from `team-planner` ownership data and the active member records at runtime. Do not store a named roster in this skill.

| IP | Owner hook | Components hint |
|---|---|---|
| PCIe EP | `team-members[]` assigned to the IP | components contain "pcie" |
| SE | `team-members[]` assigned to the IP | components contain "ip-se-" |
| D2D | `team-members[]` assigned to the IP | components contain "d2d" |
| Infra/Mng | <team-lead> (cross-cutting) | components contain "infra", "mng", "noc" |

If a new IP becomes active (e.g., SU), add a row here and to the templates.

## Win selection (one per IP)

Prefer the strongest signal available. Pick **one** per IP, in this priority:

1. **Merged feature PR** (via `pr-monitor`, last 7 days). Title contains a real change (not just rebases).
2. **Ticket closed with `resolution=Done` and effort ≥ 2d.** Pull via `jira-hsi-planning`.
3. **Bring-up milestone hit** (lab run pass, first-light, smoke test passing).
4. **Cross-IP collaboration delivery** (e.g., D2D + PCIe joint bring-up step).
5. **Process win** (CI green-streak, test backlog cleared) — only if no technical win exists.

Format: `<team-member>: <one-line action>` — cite source.

## Gap selection (one per IP)

Surface the most actionable gap. Pick **one** per IP, in this priority:

1. **Blocked ticket ≥ 5 days** for that IP (via `jira-hsi-planning`).
2. **Open RAID row** (Red or Orange) where the FW Owner sits in that IP.
3. **Stuck PR**: feature PR open > 3 days without review activity (via `pr-monitor`).
4. **No progress logged** on a ticket in `In progress` for ≥ 5 days (no `timespent` deltas).
5. **Evidence gap**: a story in the active sprint with no DoD tier or no test artifact identified.

Format: `<one-line gap>` — cite source (KEY / R-ID / PR URL).

## Status (RAG) per IP

Roll-up based on:
- **Green**: no blocked tickets > 3 days, no open Red RAID, sprint progress on track for IP scope.
- **Yellow**: one gap of the kind above, but mitigation is clear.
- **Orange**: ≥ 2 gaps, or one blocked ticket > 7 days, or an open Orange RAID row.
- **Red**: open Red RAID row, OR sprint-critical scope for the IP is blocked, OR the IP's owner is out and no coverage is in place.

Tie-break with the most recent snapshot's per-domain RAG when ambiguous.

## "No win" / "No gap" handling

- If there's no win for an IP this week, write `—` and add a `gap:` candidate to follow up on. Silence about an IP is itself data.
- If there's no actionable gap, write `—` and call it out positively in the summary line.
- Never invent a win or a gap to fill the cell.

## Cross-cutting items

If an item touches multiple IPs (e.g., IRQ tree framework merged across all repos):
- Surface it once in the IP that owns the change (Infra/Mng if cross-cutting infrastructure).
- Add a one-line acknowledgement in each affected IP's row noting the dependency lift.
