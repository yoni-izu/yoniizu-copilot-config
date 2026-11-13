# A2 D2D Current Specification Reconciliation

- **Recorded:** 2026-11-12
- **Status:** Pinned specifications and bounded Neo findings; several A2 contracts remain open
- **Tags:** a2, d2d, spec revision, fatal error, gateway, ucie mailbox, mtp, idle traffic, itg, cdup, vdup

## Scope / Question

Reconcile the current Git-maintained D2D, PSOC and NoC specifications with the A2 mirror and
CSR headers. Older knowledge entries with broader mailbox or fatal-termination claims are
superseded by the qualified findings below.

## Findings

### Specification identity

- The [A2 project D2D document](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/d2d-specification-Rev2.0.0/d2d-specification.md#L148)
  has `Rev2.0.0` in its path, but both its Word and Markdown covers say `1.0.0` and its
  revision history ends at `1.1.0` on 2025-11-13.
- The [general D2D document](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/4c966a63c9bf71eb2f55007f8f00cc05e6164d38/specs/communication/d2d/d2d.md#L153)
  adds revision `2.1.0` on 2026-10-05 for idle traffic generation (ITG). Its applicability
  to A2 has not been established. The exact A1 Rev1 original and A1/A2 Alphawave integration
  manuals were not identified in the two mapped Git catalogs.

### Fatal errors versus link-down

- [Project D2D §7.6](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/d2d-specification-Rev2.0.0/d2d-specification.md#L1667)
  describes backpressure and an interrupt on link-down.
  [§12.3](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/d2d-specification-Rev2.0.0/d2d-specification.md#L2866)
  separately says hardware should terminate D2D-targeted transactions with error on a fatal
  error, configurable per fabric.
- Neo found status/viral notification and link-down backpressure in the inspected A2 paths;
  D2D FIU error-containment inputs were tied off in those paths. Reset-isolation termination
  is a separate mechanism, with wrapper-variant and shared-FIU caveats. PSOC response and
  eventual upstream timeouts were not traced. This is an **open spec/implementation
  conflict**, not proof that no possible termination path exists or that the spec was waived.

### Pre-link gateway and remote access

- [Project PSOC §19.4](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/psoc-specification-Rev2.0.1/psoc-specification.md#L4685)
  describes the SPI D2D gateway as the bootstrap path for initial firmware to secondary dies.
  FW must ensure only one active primary, avoid ownership handoff with outstanding/replay
  traffic and issue at most one in-flight read. Access is privileged, with non-D2D-FW
  accesses blocked by default.
- [General PSOC §19.4.6](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/4c966a63c9bf71eb2f55007f8f00cc05e6164d38/specs/system/psoc/psoc.md#L4987)
  and A2 NoC Rev 1.7 §4 describe a transparent 256 MB gateway aperture: 128 MB each for MNG
  and CTRL. It is distinct from the software-operated indirect gateway registers.
- Neo traced the two front ends to a shared PSOC gateway engine and found no UCIe mainband
  training or PHY-firmware dependency in the examined RTL paths. Gateway SPI initialization
  is required; effective NoC aperture enable, reset/clock/pin-mux sequencing and package
  connectivity were not fully verified. UCIe management-sideband routing is a separate
  transport and must not be conflated with the SPI gateway.
- [IPD2DSW-209](https://elementlabs.atlassian.net/browse/IPD2DSW-209) and
  [IPD2DSW-213](https://elementlabs.atlassian.net/browse/IPD2DSW-213) request validation of an
  Alphawave sideband-init ROM image. This is a distinct concept from the SoC boot-ROM sequence;
  whether the latter is a prerequisite for validating the former is unconfirmed. A common SoC
  ROM image and the physical ROM count per package are also unconfirmed.

### UCIe mailbox and manageability transport

- [Project D2D §6.3](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/d2d-specification-Rev2.0.0/d2d-specification.md#L874)
  and the A2 AW CSR define a UCIe Adapter sideband mailbox for indirect link-partner
  register access, including a trigger. This does **not** by itself establish an
  application-level inter-die mailbox.
- [Project D2D §8.8.2.8](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/specs/d2d-specification-Rev2.0.0/d2d-specification.md#L2598)
  describes indirect management-transport-packet (MTP) generation with doorbell and
  completion interrupt. Neo did not find that path in the inspected A2 wrappers and
  name-searched only part of the vendor IP, so its implementation remains **unverified**.
  Distinguish UCIe MTP, sideband VDM/register transport and software MCTP/PLDM.
- [IPD2DSW-214](https://elementlabs.atlassian.net/browse/IPD2DSW-214) is a four-die
  **MB Link** check, not an application-mailbox requirement: the original
  [IPD2DSW-93](https://elementlabs.atlassian.net/browse/IPD2DSW-93) records linking the D2Ds.
  The related [IPD2DSW-215](https://elementlabs.atlassian.net/browse/IPD2DSW-215) asks for
  the two Alphawave D2D IPs to link at maximum speed.

### Idle traffic generation

- [General D2D §8.12](https://github.com/Elmnt-Internal/module.arch.hwarch.specs/blob/4c966a63c9bf71eb2f55007f8f00cc05e6164d38/specs/communication/d2d/d2d.md#L2692)
  describes a 64-bit idle timer, separate idle threshold and refresh interval, programmable
  LFSR seed and configuration updates only while the relevant transmit direction is idle
  and ITG is inactive. Whether this general revision is an A2 requirement is open.
- A2 tag `1_4_0_1p4_1047` and Neo show a 32-bit `FLIT_FILL_TIMEOUT` shared with normal
  flit padding, `DUMMY_FLIT_CR` enable/random/clear fields, and no separate threshold,
  interval or seed in the inspected paths. The LFSR seed inputs were tied off there; this is
  not an exhaustive search of all wrappers or a conformance verdict.
- Program the timeout before enable. TX counts whole idle dummy flits, padding counts 124 B
  halves and RX counts dropped 124 B regions including padding; the counters saturate and
  the clear bit must be released after asserting it. A control per endpoint may already
  provide independent transmit-direction control; one register per endpoint is not itself
  evidence of a per-direction mismatch.

### Topology and wrapper CDUP

- A2 NoC Rev 1.7 §2 describes one horizontal and two vertical x32 connections per die,
  consistent with 12 connected endpoints across four dies. The exact wrapper-to-package map
  remains to be checked.
- NoC Rev 1.7 §9.12.4 names the D2D-side duplication engines HDUP/VDUP. Neo traced the
  mid-wrapper `cdup_vdup` instance to the vertical D2D data path: `CDUP` is the shared IP
  name, while `VDUP` describes this instance's system role. It handles write channels;
  reads bypass it. The D2D driver API consumer and its reset/replay contract remain open.

## Sources

- A2 NoC: [Rev 1.7 Word original](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs/blob/15bd28840a84cc265db7b8c3de745775be800b2d/raw-specs/Atlantic2_NoC_Specification_Rev_1_7_0.docx), §§2, 4 and 9.12.4.
- Neo A2 mirror snapshot 2026-11-09: `ip.d2d.rtl@9fc7ec3`, `d2d/rtl/d2d_fiu.v:459`,
  `d2d/rtl/d2d_fdi_general.v:394`, `d2d/rtl/d2d_pkt_enc.v:120` and
  `d2d/rtl/d2d_mid_rtr.v:549`; `ip.d2d.csr@5e98931`, `yml/D2D_CMN.yml:9,677` and
  `yml/AW_DVSEC1_REG.yml:880`. These are paraphrased findings, not source excerpts.
- Neo gateway trace: `ip.psoc.rtl@2bcf174cae83`, `psoc_spi_gw/rtl/psoc_spi_gw_req_arb.v:228`
  and `ic/rtl/psoc_pio_ic.v:2029`; `ip.infra.rtl.shared.atlantic2@9bc4f713e6d0`,
  `noc_units/rtl/noc_addr_decode.v:507`. Effective configuration and downstream boot behavior
  remain partly unverified.
