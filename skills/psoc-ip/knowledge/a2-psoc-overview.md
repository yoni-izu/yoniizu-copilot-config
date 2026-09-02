# PSOC A2 Overview + Rev2-vs-Rev1 (A2-vs-A1) Delta

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (PSOC Rev2.0.1 vs Rev1.2.0)
- **A2-vs-A1 delta:** New on-die **crypto accelerators (SHA-384/256/512, ECC P-384)**; HSM =
  **Caliptra** sub-system + HSM Wrapper; otherwise the PSOC structure is carried from A1.
- **Tags:** psoc, a2, overview, crypto, sha384, ecc p-384, hsm, caliptra, puf, boot, reset,
  slow io, fla, mailbox, multi-die, delta

## Scope / Question
What PSOC is in A2 and what changed vs the A1 (Iridium) Rev1 spec.

## Findings
- PSOC = the **platform/management sub-system**: overview, **slow IOs** (SPI, I2C, I3C, UART,
  AVSBus, SVID, ADC, MDIO/SMI, PWM, RPM, JTAG, RMII), **peripheral engines**, **boot**, **reset**,
  **GPIO/straps**, the **uC cluster** (3× Cortex-M7 + TuC — see uc-cluster.md), clock/reset
  domains, binning & clock gating, trace/debug, fabric mapping, interrupts, RAS, security,
  **multi-die implications**, **platform guidelines**, **HSM integration**, **FIU**.
- **Peripheral engines** include: Copy Engine, **Duplication (Dup) Engine** (broadcast rate to
  engine families), local SRAMs, PoR/GP flops, write-once regs, **TuC**, WDT, timers, timestamp,
  **crypto**, **Mailbox** instances, **eFuse controller**, transaction drain, error aggregator,
  **TRNG**, remote debug (Eth/I2C/SPI), HW semaphore.
- **Boot**: PLL config, fault-injection detection, boot types, **FLA (Firmware Loading Agent)**,
  secure + unsecure boot, unsecure-boot recovery (enable I2C + PCIe Gen1 from ROM).
- **Reset**: HW reset manager, SW reset flow, reset types/config/notification, gradual reset,
  reset order, **multi-die reset notification**, PSOC sub-system reset.
- **HSM Integration**: the **Caliptra** sub-system extended by an **HSM Wrapper** — PUF IP,
  **WhoAmI** register, SoC eFuse, **FLA**, isolation (keep HSM functional across a PSOC reset),
  security enforcement, address manipulation, transaction drain, AXI split.

## A2 vs A1 (Iridium) delta
- **NEW crypto-accelerator sections** in Rev2 not in Rev1: **SHA-384 / SHA-256 / SHA-512** and
  **ECC P-384** (hash + elliptic-curve engines for secure boot / attestation / measurement —
  aligns with the A2 Overview "Secure boot using ECC-384 with external TPM").
- Rev2.0.1 (A2): 2450 paras / 22 tables vs Rev1.2.0 (A1): 2271 paras. Section taxonomy otherwise
  matches A1; most content is refinement + the crypto additions + Caliptra/HSM elaboration.
- (To fully characterize the A2 delta, diff `ip.psoc.rtl` `iridium..main` — 544 commits — not yet
  mined.)

## Sources
- Doc: `~/docs/PSoC/PSOC Specification Rev2.0.1.docx` (H1/H2 map; crypto §SHA/ECC P-384; HSM
  Integration §; Boot §; Reset §) vs `~/docs/PSoC/PSOC Specification Rev1.2.0.docx` (A1 baseline).
- Cross-ref: `ras-manageability` (manageability/RAS/power-thermal protocol content),
  `atlantic2-prj` (project context).
