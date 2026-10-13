# LTSSM Logger (PEP_CTRL) — register map, RTL trigger semantics, FW driver

- **Recorded:** 2026-10-05
- **Status:** Verified from RTL + CSR headers + FW — one item (entry state = entered vs departed) pending author/RTL-owner confirmation
- **Tags:** ltssm logger, ltssm_logger, pep_ctrl, smlh_ltssm_state, smlh_ltssm_state_reason, state_mask, state_filter, cnt_thr, count threshold, halt_on_full, keep_recent, wr_addr, entry_data, entry_timestamp, viewport, link drop, link flap, ltssm transition table, dwc_ep_ctl_ltssm, dwc_dm_ctl ltssm, ippciesw-367, manageability 0x50

## Scope / Question

What the PEP LTSSM logger hardware actually does, how FW drives it, and the two
semantics that are easy to get wrong (what the state mask filters, and which state
an entry records). Captured while reviewing the FW driver that exposes it
(`ip.pcie.sw.fw.ep` PR #346, IPPCIESW-367) against the RTL and the manageability spec.

## Findings

### Register map (Iridium / a1 — authoritative copy)

Use the **workspace** regmap, `sim/simulator/simulator_specs/pep_ctrl_regs.h`:

| Reg | Offset | Fields |
|---|---|---|
| `LTSSM_LOGGER_CTRL` | `0x78` | `EN` b0, `HALT_ON_FULL_EN` b1, `RESET` b2, `FULL` b3 (RO) |
| `LTSSM_LOGGER_WR_ADDR` | `0x7c` | `ADDR` `[7:0]` — current write pointer |
| `LTSSM_LOGGER_RD` | `0x80` | read index (viewport select) |
| `LTSSM_LOGGER_ENTRY_TIMESTAMP` | `0x84` | 32-bit timestamp of selected entry |
| `LTSSM_LOGGER_ENTRY_DATA` | `0x88` | `LTSSM_STATE` `[5:0]` (msk `0x3f`), `LTSSM_STATE_REASON` `[13:6]` (msk `0x3fc0`, **size 8**) |
| `LTSSM_LOGGER_STATE_MASK_0 / _1` | `0x8c` / `0x90` | 64 bits total, one per LTSSM state `0x00–0x3F` |
| `LTSSM_LOGGER_CNT_THR` | `0x94` | count threshold; 0 disables |

Readout is an **indexed viewport**: write the slot to `RD`, then read
`ENTRY_TIMESTAMP` + `ENTRY_DATA`. That selector is shared mutable HW state — it needs
the same locking the RAS-DES event-counter viewport already uses in this driver
(`k_spin_lock(dwc_ep_ctl_get_ras_des_lock(dev))`). Depth is **256** entries.
Oldest-first unwrap = start at `WR_ADDR` and wrap when `FULL`, else start at 0.

> ⚠️ **Revision trap.** A `pep_ctrl_regs.h` found under `~/build_artifacts/include/specs/`
> describes a *different* interface for the same block: `CTRL` at `0x68` with `POP` b2,
> `EMPTY` b4, `FULL` b5, `ENTRIES_CNT` `[14:6]`, split `ENTRY_DATA_0 0x6c` / `ENTRY_DATA_1 0x70`,
> and **no** `WR_ADDR` / `RD` / `ENTRY_TIMESTAMP`. That is a pop-FIFO model, i.e. a different
> (likely later) silicon revision. Always resolve LTSSM-logger symbols against the regmap the
> workspace actually builds with. Forward-compat note: an indexed-viewport driver will not port
> to the pop-FIFO shape unchanged, which matters for any middleware layered on top.

### RTL trigger semantics — the state mask is a STOP trigger, not a capture filter

From `rtl/pcie/pep_macro/rtl/ltssm_logger.v` (`DEPTH=256`, `WIDTH = 14 + 32 = 46`):

```verilog
ltssm_state_transition        = cfg_ltssm_logger_en & (smlh_ltssm_state != smlh_ltssm_state_r);
ltssm_state_masked            = cfg_ltssm_logger_state_mask_en[smlh_ltssm_state];
ltssm_state_r_masked          = cfg_ltssm_logger_state_mask_en[smlh_ltssm_state_r];
ltssm_state_transition_masked = ltssm_state_transition & ltssm_state_masked & ltssm_state_r_masked;

ltssm_logger_wr_data = {smlh_ltssm_state_reason, smlh_ltssm_state, timestamp};
ltssm_logger_wr      = (~ltssm_logger_full & ltssm_state_transition
                        & ~ltssm_state_transition_masked_reached_thresh) | ltssm_logger_wr_on_full;

ltssm_logger_masked_trans_counter_set =
        cfg_ltssm_logger_reset | (ltssm_state_transition & ~ltssm_state_transition_masked);
```

Three consequences, all easy to get backwards:

1. **The mask never gates the FIFO write.** `ltssm_logger_wr` keys off the raw
   `ltssm_state_transition`. Capture is *unfiltered* — every transition is logged.
   A "set = capture / clear = ignore" reading is wrong.
2. **A masked transition requires BOTH endpoints in the mask** (`mask[state] & mask[state_r]`).
   It qualifies *edges*, not states.
3. **The counter is cleared on every non-matching transition** (`counter_set` on
   `transition & ~transition_masked`), so `CNT_THR` counts **consecutive** masked
   transitions. Reaching the threshold sets `..._reached_thresh`, which **stops** capture.

So `STATE_MASK_{0,1}` + `CNT_THR` together form a **flap/thrash stop-trigger**: "halt the log
once the link bounces N times in a row within this set of states." Programming a mask expecting
a filtered log instead yields a full unfiltered log that silently halts early.

This also means the **manageability spec's LTSSM-logger command (0x50) description of the mask
as a capture filter needs the same correction** — the misunderstanding originates upstream of
the driver, not in it.

### Entry contents

- `ENTRY_DATA.LTSSM_STATE` `[5:0]` — the LTSSM state value.
  `wr_data` is built from `smlh_ltssm_state` (the *current* value) on the cycle it differs from
  the registered `smlh_ltssm_state_r`, i.e. the state just **entered**, not the one departed.
  Any FW that documents it as the departing state will reconstruct the walk one transition ahead.
  *(Confirm with the RTL owner before relying on this.)*
- `ENTRY_DATA.LTSSM_STATE_REASON` `[13:6]` — **8 bits** wide. FW transition tables key on
  `reason >> 5` (bits `[7:5]` of the reason field), which is consistent with the 8-bit width.
- The RTL carries an **unused** `smlh_ltssm_previous_state [5:0]` input — the previous state is
  *not* captured, which is exactly why the next-state mapping table below exists in FW.

### FW driver shape (`ip.pcie.sw.fw.ep`)

- Kconfig: `DWC_EP_CTL_LTSSM_LOGGER` (default n) and `DWC_EP_CTL_SHELL` (depends on `SHELL`,
  default n), both inside the `if DWC_EP_CTL` block in `drivers/snps_ep_ctl/Kconfig`.
- Five vtable ops, gated by `IF_ENABLED(CONFIG_DWC_EP_CTL_LTSSM_LOGGER, (...))`:
  `ltssm_logger_en`, `_reset`, `_get_status`, `_set_config`, `_read_all`.
- `drivers/el_pcie_ep/el_pcie_ep.c` adds thin forwarders that resolve the child EP-ctl device.
- The five ops **deliberately skip** `dwc_ep_ctl_owned_check()`: the logger is passive and its
  most valuable capture is the one taken *after* a link drop, when the EP is typically still
  host-owned. Note the knock-on: `dwc_ep_ctl_owned_check()` is also the only NULL-`dev` guard,
  so skipping it means the inline syscall wrapper dereferences `api` directly — the ops need
  their own `if (!api)` guard (precedent: `telemetry_read`).
- `set_config` returns `-EBUSY` while `EN` is set; it writes `STATE_MASK_0/1` + `CNT_THR` and
  preserves the `RESET` bit. "Running" = `enabled && !(halt_on_full && full)`
  (`keep_recent` in FW == `!HALT_ON_FULL_EN` in HW — note the inversion).

### Duplicated transition table (EP vs SE/DM) — already drifted

The LTSSM `(prev_state, reason) → next_state` table exists **twice**:

| | file | symbol | rows |
|---|---|---|---|
| EP | `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c` | `g_ltssm_transitions[]` | 88 |
| SE/DM | `fw/ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c` | `dm_ltssm_transitions[]` (+ `dwc_dm_ctl_ltssm_map_next_state()`) | 87 |

They have already diverged in row count and in enum spelling
(`LTSSM_CFG_LINKWD_ACCEPT`/`LTSSM_POLL_COMPLIANCE` in DM vs `LTSSM_CFG_LINKWD_AC`/`LTSSM_POLL_COMPL`
in EP). The EP copy also contains a duplicate key — `{LTSSM_DETECT_QUIET, 0x0u, ...}` appears twice,
making the second row unreachable. This table describes the *protocol*, not either controller, and
belongs in a shared location.

## Sources

- RTL: `rtl/pcie/pep_macro/rtl/ltssm_logger.v` (`ltssm_logger_wr`, `ltssm_state_transition_masked`,
  `ltssm_logger_masked_trans_counter_set`, `ltssm_logger_wr_data`)
- CSR: `sim/simulator/simulator_specs/pep_ctrl_regs.h` (authoritative for Iridium/a1)
- CSR (different revision — do **not** use for a1): `~/build_artifacts/include/specs/pep_ctrl_regs.h`
- FW: `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c`, `include/dwc_ep_ctl.h`,
  `include/dwc_ep_ctl_api.h`, `drivers/el_pcie_ep/el_pcie_ep.c`, `drivers/snps_ep_ctl/Kconfig`
- FW (duplicate table): `fw/ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c`
- Locking precedent: `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ras_des.c`
- Spec: Device Manageability — LTSSM logger command `0x50` (mask description needs correction)
- Review: `Elmnt-Internal/ip.pcie.sw.fw.ep` PR #346 @ `cfe33b18`, review `5408638551`
