# PIW AXI error-response manipulation (masking toward host), A1 and A2

- **Recorded:** 2026-11-22
- **Status:** Verified from RTL (via Neo, paraphrased), SW CSR headers and FW source. Open items listed below.
- **Tags:** piw, resp_mnplt, response manipulation, error masking, slverr, decerr, completer abort, aer, sei, a1, a2, ippciesw-586

## Scope / Question
How does the PIW "response manipulation" feature work, what are its A2 registers, where does its
interrupt go, and how should it be validated? (Jira IPPCIESW-586, "Error Response Masking Towards Host - PLDM".)

## Findings
- **Handler:** `resp_manipulate_handler` sits on the NoC→controller AXI response path in each of
  `piw_hbw` (MEM), `piw_lbw_mng` (MNG) and `piw_lbw_ctrl` (CTRL). Its logic is identical between A1 and A2.
- **ERR_MASK_EN = 0:** pure pass-through. It produces no capture, no status and no interrupt. Off at reset.
- **ERR_MASK_EN = 1:** **every** B/R response code is overwritten with `BRESP`/`RRESP`, including
  OKAY responses. Data, ID and LAST pass through unchanged, so the read data on an error is not meaningful.
  Keep RRESP/BRESP = 0, because a non-zero value corrupts all responses.
- **Capture:**
  - Triggered by the first non-zero original code (EXOKAY counts too) while `ERR_OCCURED = 0`.
  - Records the original ID (`*_ERR_ID_INFO`) and code (`*_ERR_RSP_INFO`).
  - Sticky: further errors are not recorded until SW writes `ERR_OCCURED = 0` (plain RW).
    If HW sets the bit in the same cycle as the clear, the HW set wins.
- **Interrupt:**
  - MNG/CTRL: a one-cycle pulse into the SEI cause (W1C) when the capture happens.
  - MAIN MEM: the cause follows the `ERR_OCCURED` level, so clear `ERR_OCCURED` first, then the cause.
- **Register map (A2 generated headers are authoritative; the yml handling_flow text has stale offsets):**
  - MEM:
    - `PIW_MAIN_CFG_MEM_RESP_MNPLT` at 0xAE4 on A2 (0xBA4 on A1); status/capture at 0xAE8–0xAFC.
    - Cause `PIW_SEI_CAUSE` bit 19 `MEM_RRESP_STATUS_ERR_OCCURED`.
  - MNG:
    - `PIW_MNG_CFG_RESP_MNPLT` (0x234 in the A1 header; reported unchanged on A2).
    - Cause `PIW_MNG_SEI_CAUSE` bit 18 `RESP_MANIP_ERR_RD`, aggregated via `PIW_SEI_CAUSE.MNG_SEI_AGG` bit 9.
  - CTRL:
    - `PIW_CTRL_CFG_RESP_MNPLT` at 0x4.
    - Cause `PIW_CTRL_SEI_CAUSE` bit 15, aggregated via `CTRL_SEI_AGG` bit 10.
  - `RESP_MNPLT` fields: `ERR_MASK_EN` b0, `RRESP` [5:4], `BRESP` [9:8].
- **A2 interrupt routing:** async SEI → `pcie_ep_cls` → chip `fc_interrupt` vector bit 240 (SPI 272) →
  `ip.psoc.rtl` `puc_intr_aggr` → PSOC CPU.
  - **PSOC only.** There is no PCIe-uC destination, because the uC is not integrated in the A2 POR.
  - The PLIC hop is not verified.
- **SEI masks** reset to masked.
  - FW (`el_piw.c` ~322-420) unmasks the drain bits, the FIU `ERR_RSP` leaves and the MNG/CTRL aggregates.
  - No explicit unmask of the `RESP_MANIP_ERR_*` / MEM RRESP/BRESP leaves was found. This is open.
- **FW:**
  - `el_piw_pre_link_cfg_impl` → `el_piw_manipulate_response_impl` sets ERR_MASK_EN = 1 and RRESP = BRESP = 0 on all ports at boot.
  - `el_piw_irq_host_notify_resp_err` (`el_piw_irq_workers.c`) reads the ID/RSP capture, clears `ERR_OCCURED`
    and sends `EL_PCIE_SEI_RESP_ERR` to the host.
  - Cause bindings are in `el_piw_sei_tree.c`.
- **Not this feature:** PIW interceptors that act *before* the NoC terminate the access themselves:
  - range-register miss (SEC_TERM_CFG_RR);
  - BMU no-hit (SEI bit 25) or multi-hit (bit 26);
  - narrow burst, empty WSTRB, TEE and BMU_TERM, which default to DECERR;
  - the DRAIN terminators, which default to SLVERR.

  These belong to the downstream-termination flow (IPPCIESW-598). A test address must reach the NoC.
- **Without masking**, the controller returns an error completion to the host (expected to be Completer Abort).
  The host typically reads all-ones and AER logs an error, which may escalate to a hang.
  - `AMBA_ERROR_RESPONSE_DEFAULT_OFF` (DBI 0x8d0) only controls the **outbound** mapping.
  - The exact inbound SLVERR/DECERR → CA/UR mapping is **unverified**; the vendor manual is unavailable.

## Validation recipe (as defined for IPPCIESW-586)
- **Test A, disabled:** the host must see the error (all-ones and/or AER CA or UR; a hang counts), and the
  mechanism must stay silent: no status, no capture, no interrupt.
- **Test B, enabled:**
  - the host is unaffected and the device stays alive;
  - `ERR_OCCURED` is set, `RSP_INFO` is 2 or 3, and the ID is captured;
  - the PSOC interrupt is handled (UART `resp-manip-err-rd` / `MEM_RRESP_STATUS_ERR_OCCURED`).
- **Re-arm:** there is no new interrupt until `ERR_OCCURED` is cleared.
- **Reads only:** writes are posted, so the host cannot observe them.

## Open items
- Does the A2 FW unmask the RESP_MANIP leaf SEI bits?
- NoC-hole behaviour per block: SLVERR/DECERR versus read-as-zero.
- DWC inbound error → CA/UR mapping.

## Sources
- RTL (Neo): `ip.pcie.rtl`
  - A1 freeze d5be1ef and A2 main 98f0e9f, `resp_manipulate_handler.v`;
  - instantiations in `piw_hbw`, `piw_lbw_mng`, `piw_lbw_ctrl`;
  - `piw_main.v` (~2155), which shows the uC is not integrated.
- SW CSR: `fw/build_artifacts/include/prototype/piw_{main,mng,ctrl}_cfg_regs.h`
- FW: `ip.pcie.sw.fw.ep` `drivers/el_piw/el_piw.c` (`el_piw_manipulate_response_impl`, SEI unmask),
  `el_piw_irq_workers.c` (`el_piw_irq_host_notify_resp_err`), `el_piw_sei_tree.c`.
- Jira: IPPCIESW-586 (the feature, A2), IPPCIESW-598 (downstream access termination SW trigger).
