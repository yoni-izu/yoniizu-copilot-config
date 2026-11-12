# A2 multicast table moved to CDUP (planer_mem)

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, multicast, cdup, mc_table_access, planer_mem, planer_mng, dup_disable, gpu multicast, plane

## Scope / Question

Where is the multicast table on A2 and how is it programmed?

## Findings

- The C2C multicast table **moved to CDUP**: 256×18, programmed indirectly through `MC_TABLE_ACCESS` (`ip.infra.rtl.shared` cdup).
- Only the planer_mem CDUP has `CDUP_MC_EN=1`, so there are 2 per die (east/west). Registers: `MC_TABLE_ACCESS` at PLANER_MNG+0x2E4, `CDUP_GENERAL` +0x210, `MC_MASK` +0x214. planer_mng bases are W 0x…EA0000 and E 0x…EC0000.
- Clear `DUP_DISABLE` (reset value 1) last, after the table is populated.
- The CDUP base is not in project_bases; take it from DT/project infra.
- Whether intra-sled multicast is POR is unknown. Plan assumption A-3 says it is not POR.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
