# A2 C2C spec 2.1.1 vs RTL gaps and HW not covered by FW plan

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, c2c spec, gaps, security ranges, sw-rx bar, message capture, ecam, cic, checksum, encryption, traffic gen, termination, ect, uncovered hw

## Scope / Question

Which C2C 2.1.1 spec features are absent from the A2 RTL, and which RTL features have no FW owner?

## Findings

- Spec features **absent or stubbed** in the RTL (ip.se.rtl 33cbdc1): security ranges, SW-Rx BAR, message capture (stub), SBSA ECAM (param 0), indirect TLP gateway, CIC, E2E checksum, link encryption (key DB stub), and OOB ownership.
- RTL features with **no FW story** in the original A2 SE plan: LTSSM logger, debug funnel (DBG view), traffic gen, memory ECC/injection gateway, AXI termination blocks, DMP EQ/termination tables/retimers/rescal, ECT, clock gating, early-reset notify, fork/arb weights, flag-aggregation interrupt, whoami pins, PLL-lock/refclk-detect status, p2p_mode-driven defaults, and dpa_to_npa masks.
- Note: the CSR mirror (generated se.csr) is missing on Neo, and the C2C spec and Synopsys docs are not on Neo. Offsets cited from the uarch doc must be re-checked against the generated CSR.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
