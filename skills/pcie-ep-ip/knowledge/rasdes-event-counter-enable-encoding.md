# RAS DES EVENT_COUNTER_CONTROL_REG — enable/clear code encoding (and the codes FW gets wrong)

- **Recorded:** 2026-08-07
- **Status:** Verified from register reference (two independent register instances)
- **Tags:** ras des, event counter, event_counter_control_reg, event_counter_enable,
  event_counter_clear, event_counter_status, per event on, per event off, all on, all off,
  per clear, all clear, viewport, lane select, event select, ec_enable, ec_disable, ec_clear,
  ec_read, granularity

## Scope / Question
What are the legal values of `EVENT_COUNTER_ENABLE` and `EVENT_COUNTER_CLEAR`? Does enabling
the event-counter mechanism enable *all* counters, or can individual counters be
enabled/disabled/cleared? Can FW read back whether a given counter is enabled?

## Findings

### EVENT_COUNTER_ENABLE — bits 4:2, write-only (reads as 0)

| Code | Name | Meaning |
|---|---|---|
| `0x1` | `PER_EVENT_OFF` | disable the counter currently selected by `EVENT_SELECT` + `LANE_SELECT` |
| `0x3` | `PER_EVENT_ON` | enable the currently selected counter |
| `0x5` | `ALL_OFF` | disable every event counter |
| `0x7` | `ALL_ON` | enable every event counter |
| any other | — | **"For other values no change"** — writing `0x0`, `0x2`, `0x4`, `0x6` is a no-op |

Reset value `0x0`, i.e. **all event counters are disabled by default**. So a global enable is
required before any counter accumulates.

`0x7` ignores the viewport selection and turns on the whole block. `0x1` / `0x3` act only on the
counter addressed by the current `EVENT_SELECT` / `LANE_SELECT` — so per-event control is a
select-then-write sequence, not a separate register.

**There is no per-group enable code** in this controller version (v6.42a-lca01). Group-level
granularity does not exist.

### EVENT_COUNTER_CLEAR — bits 1:0, write-only (reads as 0)

| Code | Name | Meaning |
|---|---|---|
| `0x0` | `NO_CHANGE` | no action |
| `0x1` | `PER_CLEAR` | clear the currently selected counter |
| `0x2` | `NO_CHANGE_2` | no action |
| `0x3` | `ALL_CLEAR` | clear all counters |

Same viewport rule: `PER_CLEAR` applies to the selected `{group, event, lane}`.

### EVENT_COUNTER_STATUS — bit 7, read-only
Returns the **enable status** of the counter currently selected by `EVENT_SELECT` /
`LANE_SELECT` (`0x0` clear / `0x1` set). This is the only way to query whether a specific
counter is on.

### Sticky-ness of the selector
`EVENT_COUNTER_EVENT_SELECT`, `EVENT_COUNTER_LANE_SELECT` and `EVENT_COUNTER_STATUS` are marked
**sticky** register fields. The enable/clear fields are write-only strobes.

### FW mismatch found in `ip.pcie.sw.fw.ep` (2026-08-07, PR under review)
`include/dwc_ep_ras_des.h` defines:

```c
#define EC_ENABLE_DISABLED  0x0   /* WRONG - not a defined code -> no change */
#define EC_ENABLE_PER_EVENT 0x5   /* WRONG - 0x5 is ALL_OFF */
#define EC_ENABLE_PER_GROUP 0x6   /* WRONG - no per-group code; no change */
#define EC_ENABLE_GLOBAL    0x7   /* correct - ALL_ON */
```

Consequences:
- `dwc_ep_ctl_ec_disable()` writes `0x0` → **hardware ignores it, counters keep running**, and
  the function still returns 0. Reachable from the SV shell (`ec enable off`).
- `EC_ENABLE_PER_EVENT` is a trap: using it to "enable one event" performs ALL_OFF.
- `dwc_ep_ctl_ec_read()` ORs `ALL_ON` into the control write on *every* counter read, so any
  per-event disable is wiped by the next read of any counter.

The `EC_CLEAR_*` constants (`0x0` / `0x1` / `0x3`) are correct.

Correct naming:
```c
#define EC_ENABLE_PER_EVENT_OFF 0x1
#define EC_ENABLE_PER_EVENT_ON  0x3
#define EC_ENABLE_ALL_OFF       0x5
#define EC_ENABLE_ALL_ON        0x7
```

## Sources
- `~/docs/PCIe EP/controller/DWC_pcie_ctl_reference.pdf` §3.2.28.3 `EVENT_COUNTER_CONTROL_REG`,
  Table 3-219 (field descriptions for `EVENT_COUNTER_EVENT_SELECT`, `EVENT_COUNTER_LANE_SELECT`,
  `EVENT_COUNTER_STATUS`, `EVENT_COUNTER_ENABLE`, `EVENT_COUNTER_CLEAR`). Cross-checked against a
  second instance of the same register block in the same manual — identical encoding.
- `~/docs/PCIe EP/controller/DWC_pcie_ctl_ep_databook.pdf` §29.3.3.2 "Event Counters"
  (Figure 29-7 register access diagram, Table 29-20 VSEC registers).
- FW: `ip.pcie.sw.fw.ep` → `include/dwc_ep_ras_des.h`,
  `drivers/snps_ep_ctl/dwc_ep_ras_des.c`, `drivers/snps_ep_ctl/dwc_ep_ctl_shell_sv.c`.
- Related KB: `rasdes-counter-reset-persistence.md`, `rasdes-vs-telemetry-metrics.md`.
