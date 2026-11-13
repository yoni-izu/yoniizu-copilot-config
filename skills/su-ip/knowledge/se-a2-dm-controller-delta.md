# A2 DM controller delta (6.60a Gen7 x2, wrapper CSRs, LTSSM logger, eFEC)

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, dm, dwc, 6.60a, gen7, x2, l0p, dbi, wrapper csr, device_type, ltssm logger, efec, bar, mps, features off

## Scope / Question

What changed in the A2 DM controller and its Element wrapper?

## Findings

- DM 6.60a-lu05, Gen7, x2 per DM (dm16sx2), L0p built in. DBI is reached via AXI→APB→DBI. The A1 TLP gateway is **gone** (elbi is dead code).
- Wrapper CSRs drive device_type (RP/EP), app_dev/bus_num, SRIS, hold_phy_rst, and LTSSM_EN.
- `SE_DM_MPS=1024` is a build-time datapath/accumulator constant, not the negotiated MPS. The DM wrapper exposes runtime negotiated MPS through `CFG_INFO_1`; the traced C2C path does not consume that value automatically. A separate C2C posted-TLP size-limit CSR bounds transmitted TLP size, so firmware must coordinate that limit with runtime MPS and AG split configuration. Generated CSR layout, reset and programming flow remain unverified. `CFG_INFO_2` has width and flit. Speed and EQ presets are only available via DBI. There is no Element CSR for target speed/width/EQ bypass.
- LTSSM logger: one per DM in DM_CTRL_CORE (0x20000/0x60000), with `LTSSM_LOGGER_CTRL` (en, halt-on-full, rst trig), `_RD` (trig+addr), `_WR_ADDR`, `STATE_MASK`/threshold. It holds 256×46b entries (ts32 + reason8 + state6). There is no event freeze.
- eFEC: `EFEC_CTRL` (EFEC_EN/MODE/CRC32) drives the custom flit protocol. The eFEC block is at 0x400 via the DM AUX APB bridge and has CRC fail counters 0..7, lane-error ×16, PM counters, and capture/freeze controls.
- Other new interrupts: L0p status, LTSSM latency/trigger, link EQ request.
- Off in the build: MSI, MSI-X, atomics, ATS, PTM, SR-IOV, ACS, L1SS, SBSA ECAM. On: SRIS, RAS-DES. iATU has 8 inbound / 4 outbound. The DM has no SPMU.
- **BAR aperture qualification:** Neo found that the reviewed DM BAR0/BAR2 38-bit masks are programmable reset defaults, not proof of a fixed 38-bit hardware limit. The C2C candidate describes a 48-bit inter-sled window, but vendor DBI semantics, actual FW programming and full downstream support were not verified. Do not treat 48-bit support as established until the programmed window is read back and exercised.
- A2 PHY identification from this mirror review is Synopsys PCIe7 1.10a_pre1, TSMC 3nm. The exact vendor configuration and relationship to PCIe EP's PHY are unverified; the older PCIe6 collateral listed in this skill is not evidence for the A2 SU integration.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`; follow-up Neo MPS/BAR reports from 2026-11-12.
