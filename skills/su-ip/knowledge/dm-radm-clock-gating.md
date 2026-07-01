# DM Controller — RADM dynamic clock gating must be un-gated for async CSR↔MEM I/F

- **Recorded:** 2026-06-30
- **Status:** Verified from RTL register header + FW driver landing (`ip.se.sw.fw`#100, head `da866bc`)
- **Tags:** dm controller, dwc, clock_gating_ctrl_off, radm_clk_gating_en, radm_clk, async csr2mem, mem2csr, ecc, error reporting, usp, dsp, role-symmetry, init sequence

## Scope / Question
Why does the DWC PCIe Dual-Mode (DM) controller need `RADM_CLK_GATING_EN` cleared at init,
and does the DSP path need a different register/bit than the USP path?

## Findings

### Register
- `CLOCK_GATING_CTRL_OFF` lives at **DBI offset `0x88c`** in the DM controller. Reset state has
  multiple gating enables set; observed RMW transition `0x1D → 0x1C` (clear bit 0 only).
- **Bit 0 — `RADM_CLK_GATING_EN`** (mask `0x1`): when set, gates `radm_clk` dynamically.
  The async CSR↔MEM handshake that the controller uses to configure ECC and read RADM internal
  memory error-report state runs on `radm_clk`; with the gate active that handshake can hang
  any time `radm_clk` is dropped.
- **Other bits in the same register** (CDM/NFM/FM gating, bits 2–4) are intentionally preserved
  — only bit 0 must be cleared. Use RMW, never blanket write.

### Role symmetry — USP/DSP share the register
- The `CLOCK_GATING_CTRL_OFF` offset (`0x88c`) **and field layout/mask (`0x1`) are identical**
  in the USP and DSP role contexts of the DM controller. The same `DM_USP_*` register symbols
  can be used from both EP/USP and RP/DSP code paths against the appropriate `dbi_base`
  (`cfg->dsp_dbi_base` for RP). This is the established codebase convention (mirrors
  `dwc_dm_set_mps` and other DM register helpers).

### When to apply
- Run early in the DM init sequence, **after device-type/role configuration and before MPS**.
  In the SE driver this is `STEP_RP_EP` (covers both EP/USP and RP/DSP). One init-step wrapper
  is enough — no separate USP vs DSP write.

### Why
- Without this clear, after init the controller appears alive (DBI reachable through its own
  clock), but any CSR access that needs to cross into the RADM clock domain — ECC config,
  RADM correctable/uncorrectable error reporting, RAS DES counters that touch RADM SRAMs —
  will silently stall or return stale data. Symptom: ECC programming "writes" that never take
  effect; error-report registers stuck at reset.

## Sources
- FW: `Elmnt-Internal/ip.se.sw.fw` → `drivers/snps_dwc_pcie_dm/dwc_dm_ctl.c`,
  `include/dwc_dm_ctl.h`, `drivers/el_se/el_se.c` (PR #100 / `IPSESW-130`)
- Register: DWC DM register header (`CLOCK_GATING_CTRL_OFF @ 0x88c`, `RADM_CLK_GATING_EN` bit 0)
- Pattern reference: `dwc_dm_set_mps` (same RMW + USP-symbol-for-DSP-base convention)
