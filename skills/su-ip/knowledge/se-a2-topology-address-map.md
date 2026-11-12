# A2 SE topology and address views

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, atlantic2, topology, se_cls, c2c_cls, se_top, address map, mng, ctrl, dbg, offsets, dm base

## Scope / Question

How is the A2 Scale Engine instantiated per die and where are the blocks in the MNG view?

## Findings

- Per die: 2 `se_cls` × 4 `c2c_cls` (SE_TOP) = 8 SE_TOP. Each SE_TOP = 2 C2C + 2 DM + 1 DMP + 1 x4 Gen7 PHY (2×2 split: DM0 lanes 0-1, DM1 lanes 2-3). Total per die 16 C2C / 16 DM / 8 PHY (`se_cls_pkg.v:41-67`).
- "Leader/follower" is not an RTL concept. DM0 owns the PHY control port; the DM1→DMP port is tied off (`dmp_macro.v:80,107`, "TEMPORARY").
- Offsets inside SE_TOP: C2C0 0x0, DM0 core/aux/USP/DSP 0x20000/0x21000/0x22000/0x23000, C2C1 0x40000, DM1 0x60000-0x63000, UPCS 0x70000, PMD 0x80000, ICCM 0x90000 (128K), DCCM 0xB0000 (64K), DMP_CTRL 0xC0000. The uarch doc values 0x38000/0x39000 for DM are stale.
- Per-C2C MNG map: 0x6000 main CSR (SPMU moved +0x488 → +0x524), 0x7000 C2C_TABLES (was C2C_CORE), 0x8000-0xA000 NPA table memory. The DBG funnel at 0x7000 is on a separate APB bus (DBG view), not a conflict.
- There are 3 address views: MNG (config), CTRL (encryption key banks, a terminated stub that is not POR), and DBG (STM/CTI/ETF).
- **Risk:** the `se_cls_pkg` per-C2C window is 32 KB (ends at 0x8000), so the NPA table at +0x8000 may be unreachable over MNG and decode into the DM/DMP fork. This may be WIP; raise it with RTL.
- Channel-id was removed (41c9072, IPSERTL-15018). There are 2 FIU MEM masters (MEM0/MEM1). The A1 CAPTURE_MSG_TLP and INJECT_TLP blocks are gone.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
