# DM controller — link speed/width change (bandwidth-change) "interrupt"

**Question:** Is there an interrupt that signals a link speed change / degradation on the SU (DM) port?

**Answer:** Only partially. The PCIe-standard mechanism (LBMS/LABS in Link Status, gated by
LBMIE/LABIE in Link Control) exists in the DM controller and its outputs reach an SE wrapper
register, but on both A1 (Iridium) and Atlantic they are **not wired into any SPI/SEI cause
tree** → no CPU interrupt; FW must poll. And the source signals exist only for a **downstream
port (RC/RP)**; they are Reserved for an upstream port / EP.

## Facts
- **Spec mechanism (LINK_CONTROL_LINK_STATUS_REG, reference p7812–7813):**
  - **LBMS** (`PCIE_CAP_LINK_BW_MAN_STATUS`, bit 30) = **the degradation bit.** Set (without
    DL_Down) when (a) HW changed speed/width *to correct unreliable link operation* (LTSSM timeout
    or higher-level process), or the downstream component initiated a change *not* flagged
    autonomous; **or** (b) a retrain completed after SW wrote Retrain Link=1 — set on **every**
    Retrain Link write, even if the link was already retraining. ⇒ FW-initiated retrains/speed
    changes also set LBMS; clear it before, and re-check speed/width after, any SW retrain.
  - **LABS** (`PCIE_CAP_LINK_AUTO_BW_STATUS`, bit 31) = autonomous change **for reasons other than
    correcting unreliable operation** (e.g. power/bandwidth mgmt) ⇒ *not* a degradation signal.
  - Enables: Link Control LBMIE (`PCIE_CAP_LINK_BW_MAN_INT_EN`) / LABIE (`PCIE_CAP_LINK_AUTO_BW_INT_EN`).
    Both status bits RW1C, Reserved for EP / upstream port, and hardwired 0 unless Link Capabilities
    `PCIE_CAP_LINK_BW_NOT_CAP` (Link Bandwidth Notification Capable) = 1.
- **Controller outputs:** `cfg_bw_mgt_int` / `cfg_link_auto_bw_int` (level, aux_clk_g) assert when
  Command.INTx_Disable=0 AND the Link Control IE=1 AND the Link Status bit=1. Pulse variants
  `cfg_bw_mgt_msi` / `cfg_link_auto_bw_msi`. "[DM,SW]: For upstream port: Reserved. [EP]: Reserved."
  Reference `DWC_pcie_ctl_reference.pdf` Table 2-137 p1725–1726; user guide p301 (Level).
  Databook p477–478 Table 6-29: SII2MSI can turn them into local MSIs (RP only).
- **SoC wiring (A1 `ip.se.rtl` branch `iridium` and `main`):** `dm_macro/rtl/dm_wrap.v` leaves the
  `_msi` pulses unconnected; `dm_pcie_iip_device_wrap.v` writes the `_int` levels into the
  DM_CTRL_AUX CSR via `.int_sig_cfg_link_auto_bw_int_wr` / `.int_sig_cfg_bw_mgt_int_wr` (data 1'b1).
- **CSR:** `DM_CTRL_AUX_INT_SIG` — A1 offset **0x7C**, Atlantic offset **0x80**. Register sw_access RW,
  fields HW-set (hw_access WO), so it is a sticky latch that SW clears by writing 0.
  `CFG_LINK_AUTO_BW_INT` bit 16 (0x10000), `CFG_BW_MGT_INT` bit 17 (0x20000). Sources:
  `ip.se.csr.iridium` `yml/dm_macro/DM_CTRL_AUX.yml`, `scripts/headers/dm_ctrl_aux_regs.h`;
  `ip.se.csr` (main) same header.
- **Not an interrupt:** `INTERRUPT_INTR_SPI_CAUSE` (A1: PM_L1_ENTRY_STARTED, PM_ASPM_L1_ENTER_READY,
  CFG_SEND_COR_ERR, CFG_FLIT_LATENCY_MSI, CFG_AER_RC_ERR_INT; Atlantic adds EFEC_INTR) and
  `INTR_SEI_CAUSE` (AER-type errors) do **not** include any INT_SIG bit. The other INT_SIG bits
  (LTSSM latency/trigger-event, LINK_EQ_REQ, PME, HP) are also polled-only.
- **FW (A1 `ip/se`):** no reference to LBMS/LABS/INT_SIG bandwidth bits.
- **Indirect interrupts available:** a degrade normally goes through Recovery → may show as
  correctable errors (CFG_SEND_COR_ERR SPI, receiver errors in SEI) — not a direct speed-change signal.
  RAS DES Group 5 ev 0x00 "L0 to Recovery Entry" counts recoveries (counter, no IRQ).

## Practical options
1. RP side, degradation = **LBMS**: ensure `PCIE_CAP_LINK_BW_NOT_CAP`=1, enable LBMIE
   (+ INTx_Disable=0), poll `DM_CTRL_AUX_INT_SIG` bit 17 `CFG_BW_MGT_INT` (or Link Status LBMS
   directly); ignore LBMS right after a FW-issued Retrain Link; confirm via `PCIE_CAP_LINK_SPEED` /
   `PCIE_CAP_NEGO_LINK_WIDTH` vs target; RW1C LBMS, write 0 to the INT_SIG latch. LABS / bit 16 only
   reports non-reliability autonomous changes.
2. Request RTL to add INT_SIG bits 16/17 to the SPI cause tree (A2 change).

## Citations
- Historical secondary vendor source (unavailable in verified Git catalog): `DWC_pcie_ctl_reference.pdf` p1725–1726 (signals), p7812–7813 Table 3-2433
  (LBMS/LABS field semantics); `DWC_pcie_ctl_user.pdf` p301;
  `DWC_pcie_ctl_dm_databook.pdf` p477–478.
- `Elmnt-Internal/ip.se.rtl` @iridium & @main: `dm_macro/rtl/dm_wrap.v`, `dm_macro/rtl/dm_pcie_iip_device_wrap.v`.
- `Elmnt-Internal/ip.se.csr.iridium` + `ip.se.csr` `scripts/headers/dm_ctrl_aux_regs.h`, `yml/dm_macro/DM_CTRL_AUX.yml`.

Status: Verified (docs + RTL + CSR, A1 and Atlantic main). Recorded 2026-11-08.
