# Live Grammar

Reference for capability 2 (Live) of the `team-weekly` skill. Tags below are the preferred input grammar. Free text is accepted with a confirmation step (see SKILL.md).

## Tag table

| Tag | Destination in `_next.md` (Live notes) | Reaches Confluence? |
|---|---|---|
| `note: <text>` | `## Live — notes` | yes (rolled into "Notes / decisions / actions") |
| `said: <team-member>: <text>` | `## Live — discussion` (attributed) | yes (paraphrased, attributed) |
| `me said: <text>` | `## Live — discussion` (attributed to <team-lead>) | yes (paraphrased, attributed) |
| `decision: <text>` | `## Live — decisions` | yes |
| `action: <text> [owner: X] [due: Y]` | `## Live — actions` | yes |
| `risk: <text>` | `## Live — risks` (staged for RAID, never auto-promoted) | yes (one-line summary only) |
| `win: <text>` | `## Live — wins added` | yes |
| `gap: <text>` | `## Live — gaps added` | yes (in next-week framing) |
| `topic: <text>` | `## Live — topics raised` | yes |
| `parking: <text>` | `## Live — parking lot` (carried to next week) | yes |
| `discussion: <text>` | `## Live — open discussion notes` | **no** (internal only — stays in local archive) |
| `staff: <text> [link: URL]` | `## Live — staff updates relayed` (and marked relayed in `staff-updates.md`) | yes |
| `private: <text>` | `## Live — private (<team-lead> only)` | **no** |

## Action item normalization

- Missing `owner:` → prompt once: *"Who owns this?"*. Default to `team` if <team-lead> declines to assign.
- Missing `due:` → prompt once. Default to next weekly date if <team-lead> declines.
- `owner: me` is normalized to `<team-lead>`.
- Owners matching the resolved team-member records are accepted; others trigger a one-time confirm.

## Risk staging

- `risk:` items accumulate in a draft RAID-row block at the bottom of `_next.md` during live mode.
- On `end team weekly`, present each as a candidate RAID row for `team-planner` to write. <team-lead> confirms per item before any RAID write.
- Never auto-promote a risk to RAID during live mode.

## Staff-update relay

- `staff:` items must include a `link:` (URL). If missing, prompt once.
- On commit, mark the matching entry in `staff-updates.md` with `Relayed: <today>` so it isn't re-surfaced next week.

## Push-to-page protocol

- The assistant never auto-edits the Confluence page in live mode.
- <team-lead> explicitly requests with `push to page: <text>` (or `push to page section <name>: <text>`).
- The assistant shows the exact diff/insertion and asks for confirm before calling `updateConfluencePage`.
- If `team-weekly` is in live mode, the page body must always be fetched fresh before each edit (per the `confluence-weekly-status.md` repo memory pattern — never type body inline).

## Periodic prompts

- Every ~5 entries: *"Uncovered prep agenda items: `<list>`. Continue or skip?"*
- If 10+ minutes of `note:` only with no `decision:` / `action:` / `discussion:` → prompt: *"No decisions or actions captured in the last block. Anything to record before moving on?"*
- If the same tag is used 3+ times in a row with no other tags → prompt: *"You've been adding only `<tag>` items — is the meeting still on the same topic?"*

## Exit grammar

Any of these closes live mode and triggers capability 3 (Post):
- `end team weekly`
- `finalize team weekly`
- `close team weekly`
