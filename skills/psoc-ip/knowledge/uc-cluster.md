# PSOC uC Cluster (3× Cortex-M7 + Tiny uC) — is there a PCIe uC?

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (PSOC Specification Rev2.0.1)
- **RTL branch/tag:** n/a (spec); RTL `ip.psoc.rtl` iridium..main not yet mined
- **Tags:** psoc, uc, cortex-m7, muc, ptuc, ruc, tuc, cluster, pcie uc, no pcie uc, mailbox, dcls

## Scope / Question
What micro-controllers PSOC instantiates, and specifically **whether PSOC contains a PCIe uC**.

## Findings

**PSOC does NOT contain a PCIe uC.** The PSOC uC cluster instantiates **three Arm Cortex-M7
CPUs** plus a **Tiny uC** — none is a PCIe controller uC:
- **Management uC (MuC)** — secure boot flow, chain of trust, device init, BMC/manageability
  comms, platform comms (PWM/RPM/I2C), error handling & RAS, debug/trace/logging.
- **Power/Thermal uC (PTuC)** — PID power/thermal loop; sensor logic; power read via
  AVSBus/SVID/ADC; throttling; DVFS.
- **Reset uC (RuC)** — device reset flow.
- **Tiny uC (TuC)** — a HW-accel offload engine (NOT one of the 3 M7s): periodically reads the
  Distributed Thermal Sensors (DTS) and power consumption and feeds the PTuC, to remove overhead
  from the power loop. (TuC also listed under "Peripheral Engines".)

"PSOC uC Cluster instantiates **three Cortex-M7 CPU** with their sub-system and provides the
connectivity between the uC and the SoC" (spec §10.4, para 1920).

**Cortex-M7 highlights** (§10, para 1876-1897): ArmV7-M, up to **1 GHz**, 32KB I-cache / 16KB
D-cache, 64b AXI4 master, 6-stage pipeline dual-issue, ETM/DWT/ITM/FPB trace, 240 external
interrupts, MPU (16 regions), **SECDED ECC** on caches/TCM, **Dual-Core Lock-Step (DCLS)**, 32b→64b
AXI extension via 16 configurable regions. **8 HW exclusive monitors** for device-memory semaphores.

**Where the PCIe uC actually lives:** the PCIe EP spec §4.1 describes a dedicated **PCIe uC** (own
ICCM/DCCM + ROM) as a HW *option* inside the **PCIe EP macro** (RTL = the PIW `piw_core`) — NOT in
PSOC. **However that PCIe uC is stubbed / not populated / has no FW** (`piw_core.v` is a gen_stub
on both `iridium` and `main`; confirmed by the PCIe FW owner). PCIe integration is done by **HW
handlers** in `pcie_iip_device_wrap`, and the **PCIe FW runs on the PSOC MuC (Cortex-M7)** — which
reaches the EP over the fabric/ELBI. PSOC's MuC communicates with other SoC uCs via **general
mailboxes**; MuC↔HSM uses a dedicated mailbox inside the HSM (§10.5, para 1929-1932).

**PSOC's relationship to PCIe is platform/reset/boot only** (no PCIe uC):
- Reset: PRSTn enters the reset manager and propagates to the PCIe sub-system; on WD reset the
  **PCIe host link stays active** (downstream txns get dummy responses); PRSTn driven from a PAD
  (default) or `PCIe_EP_rst_n` from the boot manager (§8).
- Boot: unsecure-boot recovery "**Enable PCIe at Gen1 (running from ROM)**" (§7, para 1570).
- Platform guidelines PCIe (§20.12): common-clock 100 MHz ref + 100 Ω, Wake/Power-break GPIOs.

## Sources
- Doc: `~/docs/PSoC/PSOC Specification Rev2.0.1.docx` — "Micro Controllers" (para 1853-1965),
  uC Cluster (1919-1923), uC Communication (1929-1934), Cortex-M7 (1876-1918); PCIe reset (1658,
  1664, 1696), boot ROM PCIe Gen1 (1570), Platform Guidelines PCIe (2316-2318).
- Cross-ref: `atlantic2-prj` → a2-pcie-ep.md (the PCIe uC = PHY FW CPU inside `ip.pcie.rtl`).
