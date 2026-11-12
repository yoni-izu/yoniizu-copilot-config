# A2 DMP/PHY bring-up: PHY CPU start, CCM load, DM1 tie-off, fake PHY

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, dmp, phy, pcie7, phy cpu, run req, pmd override, iccm, dccm, ccm, fw load, dm1 tie-off, refclk, sris, pll lock, palladium, fake phy

## Scope / Question

How does FW start the A2 PHY CPU and load PHY FW, and what does Palladium model?

## Findings

- PHY: Synopsys PCIe7 1.10a_pre1, TSMC 3nm, shared by DM0/DM1.
- There is **no** DMP CSR run/halt/ack, SRAM-init-done or ext-load-done handshake. The uarch `CPU_RUN_REQ`/`CPU_IND` is stale (Gen6). To start the CPU, set the PMD register-block override-enable plus the run-req value, then read run-ack, all via DMP 0x80000. The CPU also auto-wakes on refclk enable. Offsets are unverified.
- PHY FW load path: DM0 window → dmp_misc fork → AXI2AHB → CCM (ICCM 0x90000, DCCM 0xB0000). A miss on the DMP fork returns DECERR.
- PHY-global (PHY/PLL/CPU/CCM) resets are the OR of both DMs and assert only when **both** DMs hold PHY reset; per-lane resets are per DM. `APP_HOLD_PHY_RST` on one DM does not reset the CCM. Order: release at least one DM, load the CCM, then set run.
- The DM1→DMP config port is tied off ("TEMPORARY"), and an access through it hangs. DM1's DBI (0x60000-0x63000) is reachable through C2C1. All PHY and CCM work goes through DM0. "Once per group" is FW policy only.
- Refclk: only the SRIS/common-refclk override and "use controller mode", plus PLL-lock/refclk-detect status in the DMP CSR. There is no general FW refclk select.
- Palladium fake PHY: a Cadence Gen7 model at the DM PIPE that **bypasses the DMP**. It needs no PHY CPU, CCM or refclk programming, so DMP/PHY stories are off the Palladium link-up path.
- The PHY interrupt is replicated to both DMs. L1.1/L1.2 are impossible. Gen7 TX EQ table entries are unknown.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
