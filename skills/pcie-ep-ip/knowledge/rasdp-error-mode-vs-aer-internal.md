# RAS DP Error Mode vs AER Internal Errors — same event, and who must clear what

- **Recorded:** 2026-07-27
- **Status:** Verified from doc
- **Tags:** rasdp, ras dp, ras data path, ras_dp_unc_err, ras_dp_cor_err, slv_rasdp_err_mode, mstr_rasdp_err_mode, rasdp_error_mode_clear_off, rasdp_error_mode_en_off, auto_link_down_en, cfg_uncor_internal_err_sts, cfg_corrected_internal_err_sts, uncorrectable internal error, corrected internal error, aer, internal error, double counting, pcieuncorrectableinternalerrorcount, recover action, ds_term, error mode

## Scope / Question

Are the IIP_AUX SEI bits `RAS_DP_UNC_ERR` / `RAS_DP_COR_ERR` an independent event source that FW
should count separately from the AER internal-error bits? And what, if anything, must FW do when
the controller enters RAS DP Error Mode?

## Findings

### 0. What RAS DP protects, and what its errors mean

RAS DP is **Reliability, Availability, and Serviceability Data Protection**. It detects corruption
inside the PCIe controller in protected RAMs and datapaths/interfaces, outside the separate ECRC/LCRC
wire-protection mechanisms. RAM protection uses ECC on data and parity on addresses. Datapath
protection uses ECC or parity on TLP headers, data, prefixes, and optionally the AXI interface
(depending on configuration) (§29.2.1, p.669).

Consequently, a RASDP error means a protection checker detected a mismatch; it does not by itself
identify the physical root cause. Possible root causes include a memory/datapath fault or transient
corruption, or a protection-code integration/generation/checking mismatch. The databook specifies
the detected error classes, not which physical cause produced a particular event:
- One-bit ECC error: correctable; logged and reported as a correctable internal error (p.681).
- Multi-bit ECC error: uncorrectable; correction is not performed.
- Parity error: uncorrectable (p.680 footnote 107 says the uncorrectable report covers 2-bit ECC
  and all parity errors).

This is distinct from ECRC/LCRC errors, which protect the wire/link portions of traffic (§29.1,
p.668). Do not conclude that a RASDP event means the external PCIe link is corrupt.

### Iridium RTL integration evidence

The checked-in Iridium integration exposes separate single-cycle event inputs `ras_dp_unc_err`,
`ras_dp_cor_err`, `ras_ram_unc_err`, and `ras_ram_cor_err` to the PEP control logic. The datapath
events feed the RAS DP SEI interrupt causes; RAM events feed the SERR/DERR handling. The
`element_labs_ep16sx4_gen7_axi4_subsys_wrap` outputs these signals from the Synopsys PCIe
subsystem. Integration documentation places the RAS datapath protection in `u_app_layer_top`.

The checked-in design therefore contains the integration points for both controller-RAM and
datapath protection errors. It does **not** contain the linked Synopsys implementation or the
project's effective `CX_RASDP*` parameter values in this checkout, so it cannot establish which
optional checkers are enabled or identify a specific internal fault. The comments in
`pcie_iip_device_wrap.v` about `PEP_CTRL_AUX` memories having `ecc_info: none` describe those CSR
block memories, not the PCIe controller's own protected RAMs; the same comment explicitly routes
controller RAM errors through the IIP RAS-DP outputs.

The Synopsys databook also documents intentional RAS DP error injection (1-bit/2-bit per-TLP
injection, configurable through `RASDP_ERROR_INJ_CTRL_OFF`, p.674–677). That is a possible cause
in a test/injection setup, not evidence that the checked-in Iridium design has injected an error.

### 1. RASDP errors ARE the AER internal errors — do not count both

RAS DP (Data Path / RAM protection) covers the parts of the controller outside ECRC/LCRC
protection. Its error handling maps directly onto the AER internal-error status bits:

| RASDP event | Reported as |
|---|---|
| Uncorrectable (2-bit ECC, all parity) | **"Uncorrectable Internal Error Reported (AER)"** (§29.2.4) |
| Correctable (1-bit ECC) | **"Correctable Internal Error Reported (AER)"** (§29.2.5) |

So `RAS_DP_UNC_ERR` / `RAS_DP_COR_ERR` and `CFG_UNCOR_INTERNAL_ERR_STS` /
`CFG_CORRECTED_INTERNAL_ERR_STS` are the **same underlying event observed at two points** —
RASDP is the detector, the AER internal-error bits are the reporting. Counting both toward
`PcieUncorrectableInternalErrorCount` double-counts.

### 2. On uncorrectable RASDP error the controller enters RAS DP Error Mode (or drops the link)

Per `AUTO_LINK_DOWN_EN` in `RASDP_ERROR_MODE_EN_OFF`:
- `=1` → force the PCIe link down; controller resets and resumes normal operation.
- `=0` → enter **RAS DP Error Mode**, asserting `cfg_rasdp_error_mode` /
  `slv_rasdp_error_mode` / `mstr_rasdp_error_mode`.

Behaviour while in error mode (§29.2.4.1) — the controller is functionally wedged:
- RX errored TLPs are forwarded to the application; **all subsequent RX TLPs must be discarded**
  (not guaranteed correct).
- TX: bad TLP nullified (32/64-bit datapath) or **silently dropped** (128/256/512-bit);
  subsequent TLPs likewise; in-progress replay nullified, subsequent replays silently dropped.
- **Credit accounting accuracy is not retained** — best-effort only; may cause performance
  degradation or trigger overflow protection.
- **Wire (host-side) access to CDM/ELBI registers is suspended** to protect the config space.
  DBI access from the application still works — that is how you exit the mode.
- Per [ds-term.md](ds-term.md), `rasdp_err_mode & !PF_UNCERR_*_TERM_MASK` also **auto-triggers
  downstream termination** (MSTR / ELBI).

### 3. Exiting is the application's (FW's) job — this is a genuine RECOVER

The only exit is the application writing **`RASDP_ERROR_MODE_CLEAR_OFF`** through DBI, after
performing whatever cleanup it needs. This register is application-owned controller state, not
host-owned config state — contrast with the AER status registers, which FW must never clear
(see [aer-host-ownership-no-fw-clear.md](aer-host-ownership-no-fw-clear.md)).

This is the textbook shape of a PIW `EL_PIW_IRQ_ACTION_RECOVER` worker per
[piw-irq-action-framework.md](piw-irq-action-framework.md): touch hardware we own to un-wedge it.
If FW never clears it, the controller stays in a degraded TLP-dropping state indefinitely.

### 4. The two findings interlock

Footnote 108: **"The controller only reports uncorrectable errors when it exits RAS DP error
mode."** So the AER uncorrectable-internal status for a RASDP error appears only *after*
`RASDP_ERROR_MODE_CLEAR_OFF` is written. If FW never exits error mode,
`CFG_UNCOR_INTERNAL_ERR_STS` never asserts and `PcieUncorrectableInternalErrorCount` never
increments — the RAS attribute silently reads zero through a real fault.

Correctable RASDP errors have no such gating: "Errors Reported When Detected (Immediately)".

### 5. Practical guidance for FW

- Count `PcieUncorrectableInternalErrorCount` / corrected-internal from **one** source only —
  the AER internal-error path or the RASDP path, not both.
- Wire `SLV_RASDP_ERR_MODE` / `MSTR_RASDP_ERR_MODE` to a real RECOVER that (a) does any needed
  cleanup, (b) writes `RASDP_ERROR_MODE_CLEAR_OFF`, and (c) accounts for the DS_TERM side effect.
- Expect host config reads (including AER) to fail over the wire while in error mode.
- A single-bit RASDP correctable error is reported as correctable even if correction is disabled.

## Sources

- Doc: `~/docs/PCIe EP/controller/DWC_pcie_ctl_ep_databook.pdf`
  - p.668 §29.1 — ECRC wire protection
  - p.669 §29.2.1 — RAS DP overview, RAM + datapath protection scope and mechanisms
  - pp.673–677 — RAS protection types, limitations, and error injection
  - p.679 §29.2.4 — Uncorrectable Error Handling; `AUTO_LINK_DOWN_EN` / `RASDP_ERROR_MODE_EN_OFF`
  - p.680 §29.2.4.1 — RAS DP Error Mode: TLP handling, credit inaccuracy, CDM/ELBI wire-access
    suspension, `RASDP_ERROR_MODE_CLEAR_OFF`, footnotes 104–108
  - p.681 §29.2.5 — Correctable Error Handling; correctable internal error reported to AER
- Iridium RTL: `ip/pcie/pep_macro/rtl/element_labs_ep16sx4_gen7_axi4_subsys_wrap.v:458-462,1020-1024`,
  `ip/pcie/pep_macro/rtl/pcie_iip_device_wrap.v:739-743,2896,2970-3018`,
  `ip/pcie/pep_macro/misc/ELEMENT_LABS_EP_SUBSYS_WRAP_Micro_Architecture.md:286,944-951`.
- Related: [aer-host-ownership-no-fw-clear.md](aer-host-ownership-no-fw-clear.md),
  [aer-handling-completeness.md](aer-handling-completeness.md),
  [ds-term.md](ds-term.md), [piw-irq-action-framework.md](piw-irq-action-framework.md)
