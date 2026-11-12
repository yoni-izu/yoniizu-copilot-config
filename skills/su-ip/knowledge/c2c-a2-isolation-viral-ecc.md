# A2 C2C isolation/drain, viral, security and memory ECC

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, isolation, drain, viral, viral_reset_trig_wr, isolation_reset_trig_wr, one-shot, pos, poison, fiu, secure check, security level, encryption, ecc, memory gateway, injection

## Scope / Question

What RAS/security controls does the A2 C2C provide to FW?

## Findings

- Isolation/drain works per trigger (ext viral, int viral, int isolation): outbound drop/isolate_rsp, inbound terminate/isolate_rsp, drain_cnt (override via iso_drain_cnt_en/isolation_drain_cnt_val). The int_isolation input is tied 0.
- There is **no drain-done/complete status**, only pulse IRQs (rd/wr drain activated, unexpected rsp). `viral_reset_trig_wr`/`isolation_reset_trig_wr` are 1-cycle strobes that clear the sticky state and the drop/isolate enables. Clear the source first, or the state re-sets.
- Viral: `c2c_cfg_viral_mask` is 5 bits (3 FIU viral + DERR + 0) → async_viral.
- PoS means posted-write serialization, not poison. Poison passes through; FIU_RAS has POISEN_ENABLE/ERR_INJECT.
- Security: there is no C2C security-range logic. Only `FIU_AXI_SECURE_CHK` (per-unit SL vectors + fail capture) and the FIU_WP window exist. The received SL comes from the TLP (no override); the aggregation SL is a CSR constant; FIU has AR/AW_USER_SL_OVRD. The DNS compliant handlers use SL=0 (TODO).
- The encryption key DB slave is a terminated stub marked "NOT IN POR". The E2E checksum is tied off.
- ECC: 25 memories (C2C_mem.yml) behind the generic gateway `MEMORY_SELECT`/`MEMORY_ECC_INJ` (SERR/DERR, ready)/STS/MASK/ADDR/SYND/CLR/CNTR (generic 0x180-0x1AC; C2C offsets unverified). Not every memory is injectable (rec_buff_b flop has no ECC; drain address tables are stubbed). `c2c_ras.v` is the SPMU only.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
