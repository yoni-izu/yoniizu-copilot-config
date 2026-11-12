# A2 D2D interrupts (6 outputs, cause @CMN+0xF0, SEI 10 causes, SPI bit0 'LINK_UP' = link-down), link-down = backpressure + viral (no HW termination); counter widths

- **Recorded:** 2026-11-11
- **Status:** Neo RTL-verified + spec
- **Tags:** a2, d2d, interrupts, sei, spi, link down, backpressure, viral, error injection, ras, counters, rdi, fdi_active_cnt, cycle_count, dummy flit, mctp, pldm

## Scope / Question
A2 D2D interrupt map, link-down behaviour and the counters FW can expose.

## Findings

- 6 interrupt outputs; cause registers at `D2D_CMN` + 0xF0; error injection at + 0xF8.
- SEI has 10 causes (CDUP RSP_ERR = bit 9). **SPI cause bit 0 is named `LINK_UP` but is the link-down cause** — naming trap.
- Spec: interrupt 0 = link event, interrupt 1 = link error.
- On link down the bridge **backpressures** and sends **viral** to the PSoC, plus an interrupt. There is no hardware link termination; recovery is FW re-init (verif script `d2d_re_init.lua`).
- Counters: RDI counters 8-bit saturating; dummy-flit counters 32-bit; `FDI_ACTIVE_CNT` and `CYCLE_COUNT` 64-bit; outstanding-transaction counters exist.
- No MCTP/PLDM logic in D2D RTL — manageability over D2D is FW-only on the MNG-over-sideband path.

## A2 vs A1

Link-down behaviour and degrade/repair are FW-controlled as on A1. FIU serr/derr moved to the slave-CSR interface on A2.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- A2 spec Rev 2.0.0 ¶755-775, ¶807-811.
