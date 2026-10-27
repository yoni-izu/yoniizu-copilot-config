# Live Mode — Input Grammar

Used by capability 2 (Live) of the `one-on-one` skill.

## Tagged inputs

Preferred format. Each line begins with a tag, followed by `:` and the content.

| Tag | Destination in `_next.md` live section | Reaches Confluence? | Example |
|---|---|---|---|
| `note: <text>` | Notes | Yes (sanitized) | `note: discussed scope trim for SE Intr cluster` |
| `said: <text>` | Their response | Yes (paraphrased) | `said: blocked on <team-member>'s PHY work` |
| `me said: <text>` | <team-lead>'s response | Yes (paraphrased) | `me said: agreed to drop IPSESW-122` |
| `action: <text> [owner: X] [due: Y]` | Action items table | Yes | `action: file RAID row for D2D dep, owner: me, due: Jun 9` |
| `decision: <text>` | Decisions | Yes | `decision: defer PLDM 8-chip to MS6.5` |
| `risk: <text>` | Risks (staged for RAID) | Mention only | `risk: SNPS PHY extraction timeline at risk` |
| `feedback: <text>` | Feedback log | **No** (private) | `feedback: praised PCIe segregation` |
| `topic: <text>` | Topics covered | Yes | `topic: career growth — interested in D2D` |
| `followup: <text>` | <team-lead> follow-ups | Yes (as <team-lead> action) | `followup: introduce them to D2D spec` |
| `private: <text>` | Private notes | **No** (visible only to <team-lead>, never to Confluence) | `private: <private note about this team-member>` |
| `push to page: <text>` | (immediate) | Yes — appended to Confluence page after confirm | `push to page: Decision: defer PLDM 8-chip` |

## Action item fields

- `owner:` — `me` (<team-lead>), `them` (<team-member>), or `<team-member>`. Required.
- `due:` — date (any reasonable parse). Required for actions tagged as commitments.
- If either is missing, the assistant prompts once for it.

## Untagged input

Free text is accepted. The assistant best-guesses a tag and asks for confirmation:

> *"I read this as **action item** ('file RAID row for D2D dep'). Confirm? [y / re-tag / skip]"*

Responses:
- `y` → store under the guessed tag.
- `re-tag <tag>` → re-classify (e.g., `re-tag note`).
- `skip` → drop.

## Visibility rules (recap)

| Goes to Confluence published page | Goes to local archive only |
|---|---|
| `note`, `said`, `me said`, `action`, `decision`, `topic`, `followup`, `push to page` | `feedback`, `private`, win rationale, "Risks / things to flag" from prep |
| `risk` mentioned in summary, with RAID link if promoted | `risk` full text + RAID draft |

## Periodic assistant behavior

- Every ~3 entries: remind <team-lead> of uncovered prep topics.
- Missing `owner` / `due` on action → prompt once.
- `risk:` → stage RAID row draft. Do **not** commit until `end 1:1`.
- Never auto-edit the Confluence page. Page edits only on explicit `push to page:` request.

## Exit

Any of:
- `end 1:1 with <team-member>`
- `finalize 1:1 <team-member>`
- `close 1:1`

Triggers capability 3 (Post).
