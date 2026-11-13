# A2 C2C operating mode, straps, credits, aggregation and CCE

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, optimized mode, compliant mode, strap, p2p_mode, exp_ack_ebr_drop, credits, peer credit, rx aggregation, tx coalescing, ag_split_mode, dmt, cce, inc ctx, eob, eoc, explicit ack, flag identify, checksum

## Scope / Question

What does FW configure on the A2 C2C datapath, and what is HW-managed?

## Findings

- `OPTIMIZED_MODE_EN` CSR (b499147) steers the path. The straps are tied to 1, so the reset default is optimized. The p2p pin only sets reset defaults; `P2P_MODE_OVRD_EN/VAL` exist. `TRANS_NP_ACTIVE` forces compliant mode for AR/R.
- The workplan's reviewed mode-normalization set is **eight** strap-dependent values: `EXP_ACK_EBR_DROP` (inverted), `OPTIMIZED_CRED_EN`, `OPTIMIZED_MODE_EN`, both `INTRA_SLED_MODE_EN`, `POS_EN`, `EBR_EN`, and `CCE_AG_P2P_BYPASS`. `CCE_HANDLER_BYPASS` and `FAB_FORK_SEL` were separate strap/identity observations, not part of that eight-field planned reset-normalization set. The verification lua compliant config writes C2C14/15 only, clearing six fields.
- Credits: in compliant mode the C2C credit checks are bypassed (standard PCIe FC in the DWC). In optimized mode FW programs the initial peer credits (peer_credit_ctrl/data + trig_wr), cred_rate_gap/limit and optimized_mode_resp_address. Credit **release is HW** (`c2c_cfg_optimized_cred_en`).
- The UPS rate/outstanding limiters, EBR/POS/early-B and compression blocks match A1, but the compression glue changed (~841 lines in c2c_top) and needs re-verification.
- Aggregation: wr_agg/rd_agg are Rx re-aggregation (≤512B), with enable (bring-up), timeout, flush, per-op enable, INC no-agg and 4 weights. Tx coalescing is `tlp_trans_p_cfg_{size_thresh,size_max,wait_threshold}`. There is **no** aggregation chunk register; the chunk is CCE `AG_SPLIT_MODE` (none/128/256/3=drop), renamed from `cce_cfg_ag_uc_split_mode`.
- The C2C candidate requires Rx aggregation enabled in compliant mode and disabled in optimized mode. Neo found separate read/write aggregation enables but no hardware gate from the operating-mode bit; FW must explicitly configure and test both. The reviewed aggregators do not establish the candidate's >255/256 Requester-ID overflow behavior; the older default-peer CAM is not instantiated and dispatch-ready is tied low for peer indices 256 and above.
- CCE: there is no "mode ID" register; op_type is 4 bits from the address at `cce_cfg_op_type_offset`. The DMT (128 entries) is HW-managed; FW only sets masks, parse offsets and the aggregation threshold. INC needs `CCE_INC_CTX` (ORCH_EN reset 0, CONTEXT_SIZE), and the EOB address must match CCE `INC_EOB_ADDR_BASE/MASK`.
- Explicit ACK tunneling is for non-INC RS/AR; INC RS/AR uses early responses, DMT and EOC/EOB handling. Independent per-ComGroup `INC_RS`/`INC_AR` controls are upstream in the aligner's management-writable table; C2C and CCE have separate global gates. EOB is replicated to 4 CCEs. EOC carries a 12-bit context_id. `AG_SPLIT_MODE=3` selects termination; the exact response/capture behavior is unverified, so do not call it a silent drop.
- Flag identify (base/mask) is confirmed. E2E checksum is tied off and not POR.
- FW-programmed with unverified defaults: DM-AXI glue (AW/AR misc offsets in C2C_DM_CFG), fabric arbitration weights (see `fc_configure_c2cs.lua`), byte-swap/full-strobe/AW-ID-wrap, and dns_axi_split.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
- Follow-up sources: Neo A2 reports on aggregation/requester overflow and INC ownership/response handling, mirror snapshot 2026-11-09; candidate C2C and CCE sources pinned in `se-a2-spec-rtl-reconciliation-2026-11-13.md`.
