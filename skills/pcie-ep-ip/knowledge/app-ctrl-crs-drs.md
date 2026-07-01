# APP_CTRL — CRS hold-off and DRS notification semantics

- **Recorded:** 2026-06-30
- **Status:** Verified from FW driver + DWC databook (`ip.pcie.sw.fw.ep`#249, head `8143b2e`)
- **Tags:** app_ctrl, app_req_retry_en, app_drs_ready, crs, configuration retry status, drs, device readiness status, hot reset, dl_down, dl_up, perst, bring-up, set_config_ready

## Scope / Question
How does `APP_CTRL` gate the host CRS retry behaviour and DRS notification on the DWC EP
controller, where must FW assert/clear those bits in the bring-up flow, and is `APP_DRS_READY`
a level or an edge?

## Findings

### Register bits (APP_CTRL)
| Bit | Name | Role | Notes |
|-----|------|------|-------|
| `0x4` | `APP_REQ_RETRY_EN` | **Global** master CRS retry — when set, the controller answers all host config requests with CRS until cleared. | This is the bit FW toggles to hold the host off across init / hot reset. RMW preserves the per-function bits. |
| `0x8` | `APP_PF_REQ_RETRY_EN` | **Per-PF** selective CRS retry. | Intentionally untouched by the global CRS hold-off. |
| `0x10` | `APP_VF_REQ_RETRY_EN` | **Per-VF** selective CRS retry. | Same. |
| (other) | `APP_DRS_READY` | DRS-notification **permit** (level-gated), see below. | |

### `APP_DRS_READY` is a level permit, not an edge
- Setting `APP_DRS_READY=1` does **not** itself transmit DRS. It only **permits** the
  controller to issue DRS when a qualifying HW event happens. Qualifying events are
  controller-internal: **hot-reset exit** and **`DL_Down → DL_Up`**.
- No SW re-arm pulse is needed after each DRS — the bit stays asserted as a level and the
  controller will re-issue DRS on the next qualifying HW edge.
- Common misreading: "a rising edge of `APP_DRS_READY` triggers DRS, so we should clear it
  after setting it." That's wrong — clearing it just suppresses future DRS.

### Where to assert/clear in the bring-up flow
- The CRS hold-off (`APP_REQ_RETRY_EN=1`) and DRS-permit-clear (`APP_DRS_READY=0`) belong in
  the **shared pre-linkup path** — `el_pcie_ep_bring_up()`, immediately before
  `el_pcie_ep_do_linkup()`. That site naturally covers **both** the cold-boot path and the
  deferred-`PERSTn` (PERSTn ISR) path; placing it in the reset handler covers only the latter
  and forces an `#if`/`#else` split.
- Clear (`APP_REQ_RETRY_EN=0`, `APP_DRS_READY=1`) after init completes — boot path sets ready
  after init; deferred-`PERSTn` path sets it in the PERSTn ISR. Verified: no double-signal,
  no missed-signal.
- The helper `dwc_ep_ctl_set_config_ready(dev, ready)` is the API: `false` = hold off (CRS on,
  DRS-permit off), `true` = release. It is an RMW on APP_CTRL, touches only those two bits,
  no shadow needed. Idempotent — safe to call on a reset path even though a parallel init
  path may touch the register (the two are temporally disjoint).

### Hot-reset handler symmetry
- `handle_early_hot_reset_warning` early-returns when `delay_link_dn == 0`, so
  `set_config_ready(false)` is skipped in that branch.
- `handle_post_hot_reset` calls `set_config_ready(true)` unconditionally (outside the
  `delay_link_training` block).
- Result: when `delay_link_dn == 0`, the host is never held off on the warning side, yet
  readiness is re-asserted on the post side. Benign in practice (link is down during hot
  reset; host config can't reach the EP), but if symmetric bracketing is desired, hoist
  `set_config_ready(false)` above the `delay_link_dn` guard.

## Sources
- FW: `Elmnt-Internal/ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl.c`,
  `include/dwc_ep_ctl.h`, `drivers/el_pcie_ep/el_pcie_ep.c` (`el_pcie_ep_bring_up`),
  `drivers/el_pcie_ep/el_pcie_ep_reset.c` (PR #249 / `IPPCIESW-398`)
- Doc: Synopsys DWC PCIe EP databook — APP_CTRL field map; DRS rules (hot-reset exit,
  `DL_Down→DL_Up`)
