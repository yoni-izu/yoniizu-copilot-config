# A2 C2C/DM counters, SPMU, traffic generator and link info

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, counters, spmu, parse_stat, crc, efec counters, ras-des, cpl timeout, margining, traffic_gen, ltssm logger, oob ownership

## Scope / Question

What observability does A2 SE expose for FW counters, shell and attributes?

## Findings

- C2C 64b manageability counters cover Tx/Rx AW/W/B/AR/R/credit. The C2C SPMU has 70 events (AXI, TLP, compression) at +0x524. `parse_stat` is a snapshot taken through a read trigger. The DM has no SPMU.
- CRC counters exist only in eFEC (`CNT_CRC_FAIL_0..7`). The completion-timeout counter is 32 bits. RAS-DES is only reachable via DBI.
- Margining: only `APP_MARGINING_READY`/`SW_READY`; everything else is via DBI.
- Link info: width+flit in `CFG_INFO_2`; speed and EQ only via DBI. LTSSM logger: 256 entries per DM. There is no LTSSM-timeout or credit-stall counter, so these must be derived.
- traffic_gen sits in the master pipe (`c2c_cfg_traffic_gen_en`): LFSR rd/wr to an address range, token-bucket rate limit, outstanding limit, rd error capture, 4 KB `TRAFFIC_GEN.yml` block. Its POR status is unknown.
- No OOB ownership flop was found in any A2 repo.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
