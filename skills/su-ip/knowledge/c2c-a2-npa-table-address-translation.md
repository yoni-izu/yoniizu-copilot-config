# A2 C2C NPA table, ReqID CAM removal and address translation

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, npa table, lut, peer index, dev_index_offset, req id cam, default peer, explicit ack cam, inc table, dpa_to_npa, npa_to_dpa, 2k peers

## Scope / Question

What replaced the A1 LUT and ReqID→peer CAM, and what must FW program for address translation on A2?

## Findings

- The NPA table is a logical 2048×32b ECC memory at C2C+0x8000-0xA000 (A1: 256 CSR entries). It is indexed by an 11-bit peer/dev index taken from the AXI address at `CCE_CFG.DEV_INDEX_OFFSET` (`cfg_dev_index_offset`). Max peers went from 256 to 2048 (`C2C_MAX_PEERS=2048`). A 32b entry write is atomic; there is no RTL interlock for dynamic reconfiguration.
- **Correction / integration risk (Neo A2 mirror review, 2026-11-12):** the candidate C2C spec describes dedicated 2048-entry A2 and 1024-entry GPU tables, but the reviewed RTL uses one shared 2048-entry A2/GPU table; equal indices therefore share an entry. Also, the canonical C2C management path appears not to reach the table: the per-C2C management window ends at 0x8000 exclusive, and the macro front-end sends the 0x8000–0x9fff range to the DM/DMP side before the local NPA decoder. The full top-level NoC/FIU path and DM-side response were not traced, so do not claim all possible routes are impossible. Confirm an accessible programming route before FW depends on it.
- The ReqID→peer CAM was **removed** at HEAD (c88a452 "C2C - 2K desc", 2026-11-04). `c2c_req_id_2_peer_idx_cam.v` is still in the filelist but is not instantiated. `REQ_ID_DEF_PEER_EN` is gone and there is **no default peer**. The `c2c_dm_cfg_{aw,ar}_misc_req_id_offset` fields only locate the ReqID.
- An explicit-ack ReqID↔NPA/BDF CAM remains: 32 entries, 5-bit index, in the slave pipe for non-INC RS/AR (33cbdc1). Whether FW can program it is unverified, and a CAM entry spans 2 registers, so writes are not atomic.
- INC table: 8×32b (inferred) with three programmable selectors drawing from address bits [47:40] in the reviewed RTL. This differs from the candidate specification's [53:51] description; agree the input address format and index contract before programming.
- DPA→NPA classes A2/GPU/INC are selected by addr[63:62], with masks. NPA→DPA forms are A2/MC/INC (`ADDR_TRANS_2/3/MC`). Address translation also grew exp-ack NPA/BDF mapping, rx intra-sled, and default-peer-index weighting.
- DM BARs: only BAR0 and BAR2 (BAR4 off) were found in the reviewed build. The 38-bit mask is a programmable reset default, not a proven fixed aperture; the candidate spec describes a 48-bit inter-sled window. Verify vendor DBI semantics, actual FW programming and read-back before asserting the supported size. The "reserved-address BAR" is the C2C window CSRs (EOC/EOB/explicit ack), not a DM BAR.
- The CCE generates the 11-bit local-sled sentinel 0x7ff, while the traced SE RS DMT-allocation check examines only eight bits at a configured offset. Values with low eight bits 0xff may also match; the offset and end-to-end address path remain unverified. Test local and valid remote indices before relying on the sentinel distinction.
- Not found: an NPA-memory ECC interrupt, a reserved-address base CSR, or intra-sled mode bits.
- See also the reachability risk in se-a2-topology-address-map.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Additional Neo reports: `neo_se_review` session, A2 NPA-window trace and the 2026-11-12 spec/RTL review. Project C2C candidate: `project.atlantic2.hwarch.specs@15bd28840a84cc265db7b8c3de745775be800b2d`, `raw-specs/PCIe_C2C_Rev_2.2.0.docx` (filename says 2.2.0; cover says 2.1.1 and contains tracked changes).
- Raw earlier answers: session `neo_se_review/answers/q01–q13.md`.
