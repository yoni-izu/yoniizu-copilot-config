# A2 C2C interrupt lines and cause changes

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, interrupt, dpe, spi, sei, eco, serr, derr, sei_2, fc2psoc_intr, ag_aw_term, msg capture, cic, dead causes

## Scope / Question

Which interrupt lines and causes does an A2 C2C expose?

## Findings

- Each C2C has 6 lines (DPE/SPI/SEI/ECO/SERR/DERR). The 16 per die are flat in `fc2psoc_intr` at bits 432/448/464/480/496/512 + instance, with no OR tree.
- L1 is the C2C cause registers (which aggregate FIU, DM core/aux and DMP SEI). L2 is the new `SEI_2` cause register, folded into a single SEI bit.
- A1→A2: DMP SERR/DERR were replaced by DMP SEI, and the DM hot reset was removed from the viral OR.
- New causes: dns_msg_capture, sei_dmp, sei_agg_second_cause, ag_aw_term (IPSERTL-1571; only when AG is not bypassed), dns axi split wr/rd rsp err, DM efec_intr, isolation/drain unexpected rsp, credit overflow, fork no-hit.
- Dead (tied 0, "TODO REMOVE"): P/NP/CPL TLP capture SPI and TLP discard SEI. Removed: peer_idx no-hit/multi-hit and CPL unknown-fmt/stuck.
- The DNS message-capture select is tied to passthrough, so the FIFO never fills and the interrupt never fires (stub). There is no SW-Rx error BAR and no CIC in SE/C2C/DM (CIC exists only in the PCIe EP macro).
- PCIe messages are all FW-handled: PME/turn-off/PME_TO_Ack (release via app_ctrl), unlock, slot power, INTx (8 causes), vendor rx (msg_rx, poll; no SPI cause), error messages, and vendor TX via msg_ctrl_0..3.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
