# A2 PCIe EP: Git specification currency and open contracts

**Checked:** 2026-11-13. This note records specification text, not an
approved A2 product definition or a fresh RTL verification. Resolve product
applicability with Architecture and use Neo for current RTL questions.

## Pinned sources

- [A2 project EP Rev 2.0.0](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md):
  its revision history ends on 2026-08-10.
- [General EP Rev 2.0.0](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md):
  despite the same revision label, its history continues to 2026-10-26.
  The later general document is a feature superset, not automatically the
  approved A2 POR. Its capability table still contains Gen6 x16 values.
- [General NoC Rev 1.6](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/core/noc/noc.md)
  and the [A2 NoC Rev 1.7 original](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/raw-specs/Atlantic2_NoC_Specification_Rev_1_7_0.docx).
  The latter contains tracked changes; do not assume all insertions are approved.
- [Project Chip Management Rev 1.0, section 16.10](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/chip-management-specification/chip-management-specification.md#L6856)
  and [general Chip Management, section 16.10](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/system/chip-management/chip-management.md#L9856).
  Neither has been established as the exact A2 Device Manageability
  Specification (DMS).
- [A2 PSoC Rev 2.0.1](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/psoc-specification-Rev2.0.1/psoc-specification.md)
  and [general interrupt specification](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/infrastructure/interrupt/interrupt.md#L425).

The general sources were read at `4c966a63c9bf71eb2f55007f8f00cc05e6164d38`;
the current pinned head above differs by one commit touching only the Rocky
specification. The reviewed EP, NoC, PSoC, interrupt and Chip Management files
are unchanged between those commits.

## Topology and remote access

- [NoC section 2](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/core/noc/noc.md#L636)
  describes one, two or four logical host EPs mapped to physical x4 ports on
  the dies; in one- and two-port modes an unused port may serve another
  external device. Chip Management section 16.10 says up to two EP ports are
  implemented in a chip. These statements have not been reconciled as
  hardware capability versus A2 SKU/POR. Do not select either as the product
  limit without an owner decision.
- A2 NoC Rev 1.7 section 4.8 contains a tracked insertion describing a
  PSoC-mediated remote MNG/CTRL gateway. Confirm whether it applies to the
  early-boot PCIe CSR route, its availability and permissions, and any
  per-die address encoding; it does not by itself prove the only valid route.

## Address translation

- [A2 EP section 6.8](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md#L1939)
  says eight inbound and four outbound iATU regions per function. Historical
  Neo research recorded eight inbound and eight outbound for the A2
  configuration. Verify the current configured IP before choosing a count.
- [General EP section 7.7](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L2528)
  describes eight memory BMU regions, 32 configuration regions, and
  termination, logging and interrupt on no match. Its later
  [section 7.20](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L3356)
  assigns eight per-die INC/GMEM/LMEM regions and reserves entries 8-11.
  Reconcile the sections with the A2 implementation. In particular, do not
  equate a global BMU bypass with the behavior of an enabled BMU whose
  individual regions do not match.
- The [A2 architecture-enhancement original](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/raw-specs/A2_Arch_Enhancments_v1.3.docx)
  describes a 128 KB PCIe BAR region for dynamic multicast, with 256
  512-byte-aligned table slots. This is separate from the INC regions above.
  Its filename says v1.3, but its cover and revision history say Version 0.1,
  June 2026; confirm its authority, A2 POR and implementation.
- [General EP section 7.28](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L3959)
  describes flag identification and producer/consumer ordering modes. A2
  applicability, configuration ownership and required tests are open.

## Boot, timing and reset

- [A2 EP section 6.2](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md#L1808)
  describes a 100 ms delay from stable rails to PERST deassertion and requires
  LTSSM Detect within 20 ms of the end of fundamental reset. The same section
  uses retained, privileged-write SRAM to avoid a fresh SPI PHY image download
  and authentication during warm reset. Whether the PHY memories require
  re-copy on a particular reset must be checked separately.
- [A2 PSoC section 8.3.3](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/psoc-specification-Rev2.0.1/psoc-specification.md#L3396)
  requires warm-reset completion within 20 ms. FLR has a separate 100 ms
  budget ([EP section 5.16](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L877)).
  The EP spec also describes FLR watchdog autocompletion setting CRS, status
  and an interrupt on timeout. Define measurement start and end events; these
  timing statements are not interchangeable.
- EP section 6.2 describes Gen1 fallback after a corrupted SPI PHY image, but
  strikes out its sentence selecting ROM through the copy FSM. Separately,
  [PSoC section 7.6.2](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/psoc-specification-Rev2.0.1/psoc-specification.md#L3204)
  describes hardware-triggered unsecure recovery after both BTL images fail.
  Confirm the triggers and ownership; do not assume these are the same flow.
- [General EP section 7.22](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L3668)
  specifies termination, a programmable delay and isolation, then software
  release of termination after required configuration, including security.

## Capability and RAS questions

- [A2 EP section 6.14](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md#L2212)
  describes PTM read-delay compensation, resynchronization, validity and
  timeout signals, while the capability table marks PTM TBD. Confirm whether
  PTM is in the A2 POR rather than describing it as simply supported or absent.
- [A2 EP section 6.11](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md#L2079)
  describes firmware-generated outbound messages, including VMI and indirect
  MsgD, but marks vendor-message reception as a TODO. Product support and the
  implemented transmit path require separate verification.
- [A2 EP section 6.6](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md#L1918)
  says all PCIe subsystem memories have ECC, whereas historical A2 RTL
  findings report exceptions. Recheck each memory and its firmware-visible
  error path; neither statement proves the current implementation.
- [General EP section 5.27](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L1124)
  describes MSI-X index and optional security checks, and coalescing that
  retains a pending interrupt. [Section 12](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/communication/pcie-ep/pcie-ep.md#L4217)
  describes firmware notification of viral conditions to the host via MSI-X
  and software exit from RAS datapath error mode; confirm A2 applicability.
- The [interrupt specification](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/70b61326fcd3ca2ecea9d1c54c1d9e9c709872d8/specs/infrastructure/interrupt/interrupt.md#L425)
  says an asynchronous wire status clears when its input goes low, while
  section 8.1 describes clearing the corresponding status as W1C. Resolve the
  actual PLIC path and clearing behavior before relying on either statement.

The exact A2 DMS, Synopsys Gen7 controller/PHY manuals and SDK were not
verified in the two Git repositories. Internal integration specs are not
substitutes for those vendor programming references.
