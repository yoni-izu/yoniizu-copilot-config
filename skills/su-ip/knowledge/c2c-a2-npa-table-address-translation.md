# A2 C2C NPA table, ReqID CAM removal and address translation

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, npa table, lut, peer index, dev_index_offset, req id cam, default peer, explicit ack cam, inc table, dpa_to_npa, npa_to_dpa, 2k peers

## Scope / Question

What replaced the A1 LUT and ReqID→peer CAM, and what must FW program for address translation on A2?

## Findings

- The NPA table is a 2048×32b ECC memory at C2C+0x8000-0xA000 (A1: 256 CSR entries). It is indexed by an 11-bit peer/dev index taken from the AXI address at `CCE_CFG.DEV_INDEX_OFFSET` (`cfg_dev_index_offset`). Max peers went from 256 to 2048 (`C2C_MAX_PEERS=2048`). A 32b entry write is atomic; there is no RTL interlock for dynamic reconfiguration.
- The ReqID→peer CAM was **removed** at HEAD (c88a452 "C2C - 2K desc", 2026-11-04). `c2c_req_id_2_peer_idx_cam.v` is still in the filelist but is not instantiated. `REQ_ID_DEF_PEER_EN` is gone and there is **no default peer**. The `c2c_dm_cfg_{aw,ar}_misc_req_id_offset` fields only locate the ReqID.
- An explicit-ack ReqID↔NPA/BDF CAM remains: 32 entries, 5-bit index, in the slave pipe for non-INC RS/AR (33cbdc1). Whether FW can program it is unverified, and a CAM entry spans 2 registers, so writes are not atomic.
- INC table: 8×32b (inferred) with 3 FW-selectable address bits.
- DPA→NPA classes A2/GPU/INC are selected by addr[63:62], with masks. NPA→DPA forms are A2/MC/INC (`ADDR_TRANS_2/3/MC`). Address translation also grew exp-ack NPA/BDF mapping, rx intra-sled, and default-peer-index weighting.
- DM BARs: only BAR0 and BAR2 (BAR4 off), with a 38-bit mask (256 GiB). The "reserved-address BAR" is the C2C window CSRs (EOC/EOB/explicit ack), not a DM BAR.
- Not found: an NPA-memory ECC interrupt, a reserved-address base CSR, or intra-sled mode bits.
- See also the reachability risk in se-a2-topology-address-map.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
