# A2 PTM end to end: capability, AUX CSRs, PSOC timestamp sync, layer ownership

- **Recorded:** 2026-11-18
- **Status:** Verified from the spec text, the generated SW CSR header, Neo (A2 RTL) and Jira evidence. POR and the chip-level interrupt path are still open.
- **Tags:** a2, ptm, eptm, precision time measurement, psoc timestamp, timestamp_incr, pep_ctrl_aux, ptm_ctrl, ptm_capture, ext-cap list, kmd, pci_enable_ptm, sival, trace timestamp
- **Supersedes and extends:** the PTM bullets in [a2-ptm-ras-security-topology.md](a2-ptm-ras-security-topology.md) and [a2-ptm-ras-diagnostics.md](a2-ptm-ras-diagnostics.md).

## Sources

- **A2 EP spec:** `project.atlantic2.hwarch.specs@5e4361b8`, `specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md`:
  - §5.35 is a one-line feature description.
  - The capability table at line 417 marks PTM as TBD.
  - Tables 6-1 and 6-2 mark PTM message TX and RX as TODO.
  - §6.14 covers the PSOC timestamp sync and the controller interface.
  - §13 lists the "PTM (SPI)" interrupt.
  - The `module.arch.hwarch.specs@714fc578` copy has identical PTM text.
- **PSOC spec:** `project.atlantic2.hwarch.specs@5e4361b8`, `specs/psoc-specification-Rev2.0.1/psoc-specification.md`:
  - §6.10 Timestamp.
  - The M7 `TSVALUEB` port table.
  - Table 14-2: the timestamp is visible on the MNG fabric and reflected only to the CTRL fabric.
- **A2 Overview:** `module.arch.hwarch.specs@714fc578`, `specs/global/overview/Atlantic2_Overview.md`:
  - Line 420: the EP feature table marks PTM as "v".
  - Line 569: the scale-up table marks PTM as "-".
  - Line 1239: trace timestamping is correlated to host time via PTM.
- **SW CSR header:** `ip.pcie.sw.csr@7217b825`, `v2/2_0_0_2p0_1125/prototype/pep_ctrl_aux_regs.h` (+ `_attr.h`).
- **Neo, A2 RTL, 2026-11-17 mirror:**
  - Repos: `ip.pcie.rtl@d5be1ef7`, `ip.pcie.csr@46b50f62`, `project.atlantic2.rtl.eip_pcie@968f4827`, `project.atlantic2.rtl@0d6a8c3d`.
  - Files: `pep_wrap.v`, `pcie_iip_device_wrap.v`, `PEP_CTRL_AUX.yml`, `PF0_PTM_CAP.yml`, `DWC_PCIE_USP.yml`, and `Cdm/PTM/*` in the DWC core.
- **Jira:**
  - IPPCIERTL-1511: broken ext-cap list, plus a CLOCK_CORR accumulation bug.
  - IPPCIESW-612: PLDM PTM end-to-end test.
  - EML-76: SpeedBridge and ePTM questions.
  - IPPCIESW-705 / 706: FW PSOC timestamp sync.
  - IPPCIESW-708 / IPPSOCSW-1255 / IPMMUSW-630: KMD enablers.
  - MODINFSDKSW-16959 and PRJIRDMSW-8769: profiler host-time conversion.
  - SWRND-475: confirm the PTM POR.
- **Confluence:** RPI page 1728643191, "A2 Feedback: Host-Device Time Correlation (PTM)", which links specs PR #392 §5.3.4.

## Findings

### Capability (DWC config space)
- The EP is a **PTM Requester only**. It has no Responder or Root capability.
- **PTM Extended Capability** at PF0 **0x3D0**:
  - ID 0x1F, next pointer 0x3DC.
  - PTM_CAP 0x3D4 reads 0x9: Requester + **ePTM**.
  - PTM_CONTROL 0x3D8: Enable, Root Select (RO 0) and Effective Granularity all reset to 0.
- **PTM Requester VSEC** (ID 3) at **0x3DC**. It holds:
  - CONTROL 0x3E4, with LONG_TIMER resetting to 9.
  - LOCAL 0x3EC/0x3F0.
  - MASTER 0x414/0x418.
  - MASTERT1 0x420/0x424.
  - CLOCK_CORR 0x430/0x434.
- **Controller version:** Neo reports the export as coreKit 6.52a-lca01 Gen7 `ep16sx4`, which disagrees with the 6.60a stated elsewhere. This is unresolved.
- **Local clock:** generated inside the DWC core as a 64-bit ns value with fractional bits, derived from the core-clock period.
  - It is **not** connected to the PSOC timestamp or to any SoC time base.
  - No RTL consumer of PTM time exists in `project.atlantic2.rtl`.
- **Auto-update:** when enabled, the period is 10 ms ((9+1) × 10 × 100 µs).
- **Response timeout:** 100 µs.

### Element AUX CSRs (PEP_CTRL_AUX, all reset to 0)

| Offset | Register | Use |
|---|---|---|
| 0x124 | PTM_CTRL | AUTO_UPDATE_SIGNAL[0] drives the core auto-update input. CAPTURE_MODE[1]: 0 = snapshot on PTM_CAPTURE, 1 = continuous. |
| 0x128 | PTM_MANUAL_UPDATE | WO pulse. The write is **dropped unless PTM_STATUS.TRIGGER_ALLOWED = 1**. |
| 0x12C | PTM_STATUS | CONTEXT_VALID[0], RESPONDER_RDY_TO_VALIDATE[1], TRIGGER_ALLOWED[2], UPDATING[3] |
| 0x130 | PTM_EVENTS | Sticky bits: REQ_DUP_RX[0], REQ_REPLAY_TX[1], TIMEOUT_RAW[2], TIMEOUT_ERROR[3]. Bit 3 is the timeout qualified by `updating`. |
| 0x134 | PTM_CAPTURE | WO. Atomically snapshots the local clock and the correction. |
| 0x138/0x13C | PTM_LOCAL_CLOCK_LO/HI | 64-bit snapshot |
| 0x140/0x144 | PTM_CLOCK_CORRECTION_LO/HI | 64-bit snapshot |

- **SPI interrupt causes** (INTR_SPI_CAUSE/MASK):
  - Bit 16: PTM_REQ_RESPONSE_TIMEOUT (raw).
  - Bit 17: PTM_CONTEXT_VALID_FALL.
  - Bit 18: PTM_CLOCK_UPDATED.
  - There is **no** rising-edge (context-valid) cause.
- **Interrupt routing:** the interrupt goes from `pep_wrap` to `piw_macro`. The path onward to the PSOC MuC is unverified.
- **Wrapper conflict:** `pep_wrap.v` wires all PTM ports, but the `element_labs_ep16sx4_gen7_axi4_subsys_wrap.v` stub ties the inputs and leaves the outputs open. Neo did not determine which wrapper the chip build uses.

### PSOC timestamp (sync target)
- **Counter:** 64-bit, 1 ns units, running on the 50 MHz ref clock (+20 per clock).
- **Writes:** writable through CSR. `timestamp_incr` adds an offset atomically while the counter keeps running.
- **Reset domain:** PSOC POR.
- **Consumers:**
  - It feeds the M7 `TSVALUEB` port (ETM/ITM trace timestamps).
  - It is reflected to the CTRL fabric (host-readable).
  - It also serves as the CoreSight trace global timestamp.
- **Spec §6.14 sync recipe (FW):** Effective = PTM_read + EP_Read_Delay; Delta = Effective − PSOC_TS; PSOC_TS += Delta.
- **Unspecified:** the spec does not define EP_Read_Delay or the resync period.

### Spec defects and contract gaps
- **§6.14.1 has the initiator backwards.** It says the exchanges are "initiated by the host". In PCIe, the **Requester (the EP)** initiates PTM Request, and the RP responds with Response or ResponseD.
- **§6.14.2 names the wrong access path.** It says the EP time is read "via config access", but the HW provides the AUX snapshot CSRs (PTM_CAPTURE + LO/HI).
- **Backward time steps.** `timestamp_incr` adds an offset. A negative delta (PSOC ahead of host) needs a two's-complement add or a slew policy. IPPCIESW-705 requires "no backward steps", and the spec does not address this.

### Silicon or emulation status (IPPCIESW-612 on PLDM edk3, image 1_4_0_1p4_1047)
- **Plain PTM works end-to-end:**
  - Three manual updates each gave CONTEXT_VALID.
  - The host RP was the responder (Intel Xeon 6505P, Linux 6.17, CONFIG_PCIE_PTM=y).
  - The SpeedBridge's transparency to PTM is still unconfirmed (EML-76).
- **IPPCIERTL-1511, Issue 1 (ext-cap list):** the next pointer of the Phys-Layer 64 GT/s cap at 0x1E8 reads 0x258 (which reads 0), so 11 capabilities are unreachable, including PTM.
  - As a result, `lspci` shows no PTM and Linux `pci_enable_ptm()` cannot enable PTM on the EP.
  - It may be a DBI or FW overwrite after enumeration.
- **IPPCIERTL-1511, Issue 2 (clock correction):**
  - CLOCK_CORR equals MASTERT1 (an absolute time), not MASTERT1 − T1.
  - So LOCAL accumulates the master time after every update.
  - The EP local clock stays corrupted until the image is reloaded.
- **ePTM is untested:** the host-side link was Gen2 non-Flit.

## Layer ownership (planned per Jira)
- **HW:**
  - Provides the DWC requester and the AUX CSR snapshots and interrupts.
  - Provides the PSOC timestamp with `timestamp_incr`.
  - Provides no HW path from PTM time to the PSOC timestamp.
- **FW** (MuC, IPPCIESW-705/706):
  - Arm the requester.
  - Service the interrupts.
  - Capture the time, compute the delta and correct the PSOC timestamp.
  - Expose sync status to the KMD.
- **KMD** (IPPCIESW-708):
  - Enable PTM on the RP and EP through the Linux PCI core.
  - Provide an ioctl/sysfs to read and configure the PSOC timestamp.
  - Expose the FW sync status.
- **Host:**
  - The RP/root acts as the PTM Root/Responder.
  - The kernel needs `CONFIG_PCIE_PTM`.
  - The kernel enables PTM on its ports.
- **User SDK / profiler** (MODINFSDKSW-16959): converts device timestamps to host time only while the sync is valid.
