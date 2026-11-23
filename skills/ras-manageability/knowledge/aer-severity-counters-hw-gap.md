# AER correctable / non-fatal / fatal counters — requirement vs A1 HW support (EP and SE)

- **Recorded:** 2026-11-22
- **Status:** Verified (spec in Git + A1 generated CSR headers + Neo, wrapper-level). The controller's internal behaviour is unverified.
- **Tags:** aer, error counters, pciecorrectableerrorcount, pcienonfatalerrorcount, pciefatalerrorcount, sucorrectableerrorcount, sunonfatalerrorcount, sufatalerrorcount, sei cause, ras des, cfg_send_cor_err, radm_fatal_err, cfg_aer_rc_err_int, sticky, host ownership, scale-up, pcie ep, a1, hw gap

## Scope / Question
Can FW implement the required per-link severity-classified AER counters (correctable /
non-fatal / fatal) on A1 PCIe EP and on SE (scale-up DM)? Specifically, given that the AER
status is sticky and that FW must not clear it on EP, and that host error messages can be
disabled.

## Findings

### Requirement exists (Chip Management Rev1.0.0)
- §16.10 / Table 16-10 defines per-link `PcieCorrectableErrorCountL<x>`,
  `PcieNonFatalErrorCountL<x>` and `PcieFatalErrorCountL<x>`, each "Numeric with event" and
  each with warning/critical thresholds. The fatal critical threshold sets Critical Chip Status.
- §16.11 defines the scale-up equivalents: `SuCorrectableErrorCountL<x>`,
  `SuNonFatalErrorCountL<x>`, `SuFatalErrorCountL<x>` and their thresholds.
- This is the general `module.arch.hwarch.specs` document. It has not been proven to be the A1
  customer contract; see the skill's document-gap note.

### PCIe EP (A1): the statement holds
- **No severity summary interrupt.** The severity-level SEI/SPI causes are only the three
  message-send ones: `PEP_CTRL_AUX` SEI `CFG_SEND_F_ERR` (bit 20), `CFG_SEND_NF_ERR` (bit 22),
  and SPI `CFG_SEND_COR_ERR` (bit 14). All three depend on the message-send path.
- **No `RADM_*_ERR` causes.** `PEP_CTRL_CORE` SEI holds only `RTLH_FC_OVERFLOW_ERR`, the
  `RPLH_FLIT_*` causes and `RADM_CPL_TIMEOUT`.
- **Per-type SEI covers 15 types only.** `PEP_CTRL_AUX` SEI bits 1–15 (`CFG_*_ERR_STS`).
  Poisoned TLP, completer abort, unexpected completion, unsupported request and ACS violation
  have no input. Completion timeout arrives separately, via `RADM_CPL_TIMEOUT`.
- **Pulse vs sticky is unknown.** It is unverified whether these inputs are per-event pulses or
  reflections of the sticky AER status. The A2 uarch note calls them sticky RW1C; that is not A1
  evidence. The cause register is level-latched W1C, so a sticky level re-asserts until the
  **host** clears AER.
- **RAS DES is present but not a severity count.** The register block (event counter, TBA,
  EINJ) is present. Its counters are per-type and unclassified by severity, and the enabled
  groups on A1 are unverified.
- **No wrapper AER counter.**
- **FW must not clear AER.** The host owns AER (RW1CS). Polling the sticky status can therefore
  detect at most "a bit went 0→1 since the host last cleared it", which is not a count.

### SE / scale-up DM (A1): the "can't clear" argument does not apply, but exact counting is NOT established
Verdict: the ownership/sticky part of the statement is wrong for SE. The "no HW mechanism to
count or report" part still holds for an exact counter; only best-effort counting is shown.
- **FW owns the AER registers.** No host OS enumerates the SE ports, and A1 SE FW already
  implements AER read, W1C-clear and reporting-enable (`ip/se/include/dwc_dm_ctl_sv.h`). A
  poll → count → W1C loop is legal there. It is lossy: repeats of the same type between two
  polls count as one, so it yields "polls with ≥1 error of this type", not an event count.
- **Severity indications exist for the DSP/RC role.** `DM_CTRL_CORE` SEI has `RADM_FATAL_ERR`
  (bit 4) and `RADM_NONFATAL_ERR` (bit 3); SPI has `RADM_CORRECTABLE_ERR` (bit 20). These are
  error messages received from the link partner. `DM_CTRL_AUX` SPI `CFG_AER_RC_ERR_INT` (bit 4)
  is the RC AER interrupt, which does not depend on the message-send path.
  - Whether the port's own detected errors reach `CFG_AER_RC_ERR_INT` depends on Root Error
    Command, Device Control and the masks, all FW-programmable here. That is unverified on DWC.
  - Even if they do, it is a single interrupt plus sticky Root Error Status (first/multiple
    flags), so several errors before FW services it collapse into one. Not a per-event counter.
  - `RADM_*_ERR` cover only errors reported by the link partner, not the port's own errors,
    and only in the DSP/RC role.
- **Same per-type gaps as EP.** `DM_CTRL_AUX` SEI bits 0–14 carry the same 15 per-type causes,
  with the same missing types.
- **RAS DES present.** Both the USP and DSP maps have it; the enabled groups are unverified.
- **No wrapper AER counter.**

### Correction to an older entry
`pcie-aer-fw-counters.md` said the EP `PEP_CTRL_CORE` SEI has `RADM_FATAL_ERR` /
`RADM_NONFATAL_ERR`, and that the SEI cause is edge-latched. For A1 EP both claims are wrong:
those fields exist only in SE `DM_CTRL_CORE` (as received-message indications), and the cause
latches are level-sensitive.

### How to settle the remaining unknown
Inject errors with RAS DES EINJ (for example bad TLP or LCRC) while AER status stays set, then
W1C the SEI cause and see whether it re-asserts.
- **Re-asserts:** the input is a sticky level, so EP per-type counting is impossible without host
  cooperation.
- **Stays clear and re-fires per injection:** the input is a pulse, so FW can count exactly for
  those 15 types.

## Sources
- Spec: `Elmnt-Internal/module.arch.hwarch.specs@b39870f2`
  - `specs/manageability/chip-management/chip-management.md` (Rev1.0.0), §16.10 Table 16-10 and §16.11.
  - `specs/pcie/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md`, §5.3 AER and §11.2 Event Counters.
- CSR (A1 generated):
  - `build_artifacts/include/ips/pcie/v1/3_0_11_3p0_1389/prototype/pep_ctrl_aux_regs.h` and `pep_ctrl_regs.h` (`PEP_CTRL_CORE` SEI/SPI); `dwc_pcie_usp_regs.h` (`PF0_RAS_DES_CAP` at 0x268).
  - `ips/se/v2/1_4_0_1p4_1047/prototype/dm_ctrl_aux_regs.h` and `dm_ctrl_regs.h`. SE CSR version pinning was not confirmed.
- RTL via Neo (paraphrased):
  - EP: `ip.pcie.rtl@3afb411` (iridium), `pep_macro/rtl/pcie_iip_device_wrap.v`; `ip.pcie.csr@10bb3e2`, `ral_block_PF0_RAS_DES_CAP.sv`.
  - SE: `ip.se.rtl@dc5986d6`, `dm_macro/rtl/dm_pcie_iip_device_wrap.v` lines 1416–1457 and 1739–1866; `ip.se.csr.iridium@26b653cf`.
- FW: `ip.se.sw.fw` `include/dwc_dm_ctl_sv.h` (AER read / clear / enable APIs).
- Related KB: `pcie-aer-fw-counters.md`, `pcie-ep-ip/knowledge/aer-host-ownership-no-fw-clear.md`.
