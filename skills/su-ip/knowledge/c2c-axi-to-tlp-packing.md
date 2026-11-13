# C2C AXI-to-TLP Packing (burst → TLP, multi-transaction accumulation)

- **Recorded:** 2026-11-03
- **Status:** Verified from A1 + A2 C2C specs and Iridium RTL (`c2c_tlp_transmit_pipe_p_data_gen.v`); MPS-split path of the compliant/DM-AXI bridge not traced
- **Tags:** c2c, axi, tlp, burst, packing, accumulation, aggregation, mps, p2p_mode, tlp_trans_p_cfg, wait_threshold, coalescing, compliant mode

## Scope / Question
Does a multi-byte AXI request from the NoC go over C2C as one packet (TLP)? Are multiple AXI transactions combined into one TLP?

## Findings
- **One AXI burst → one TLP (not one TLP per beat).** A1 spec §AXI-to-TLP Mapping: "AXI burst is converted into a single TLP" (AW+W → posted MWr; B → posted MWr; AR → MRd or MWr; R → Cpl or MWr). The AR/R → posted MWr option is a deliberate configurable A1 special case: AXI imposes no AR/AW ordering, so carrying reads in posted MWr avoids PCIe non-posted ordering stalls when posted and non-posted TLPs interleave (A1 spec "AR- can be configured to be mapped either to Non-Posted MRd TLP or Posted MWr TLP"; "the hardware can be configured to map AR, R channels using Posted MWr TLPs"). The standard PCIe mapping is AR → MRd, R → CplD; which one the A1 HW uses by default was not verified.
- **Bounded by MPS**: if the burst exceeds MPS (or MRRS for reads), HW splits it into multiple TLPs with unique IDs and merges the responses back into one AXI response (A1 spec §Decomposition). DM MPS in RTL: `SE_DM_MPS = 1024*8` bits = 1024 B (`se_cls_pkg.v`).
- **A1 optimized mode also packs *several* AXI transactions/channels into one TLP** (A1 spec: "A single TLP might instantiate several AXI channels"; Max Payload 1024 B, Max Accumulation 512 B). RTL `c2c_tlp_transmit_pipe_p_data_gen` P pipe:
  - Accumulates only when `p2p_mode` is 1 (top input; CSR override `C2C_CFG.P2P_MODE_OVRD_EN/VAL`, bits 16/17).
- Iridium straps: C2C[0..6] p2p_mode=1 (frozen se_cls.v), switch port C2C[7] p2p_mode=0 (a1_chip.v:6274), so the switch port never coalesces unless `C2C_CFG.P2P_MODE_OVRD_EN` is set.
  - Closes the TLP when any of these is true: accumulated bytes > `TLP_TRANS_P_CFG_SIZE.THRESH`; the next item would exceed `.MAX`; the relaxed-ordering bit changes; or the idle counter reaches `TLP_TRANS_P_CFG.WAIT_THRESHOLD`.
  - A full write (AW+all W) or read response (R) is **never split across TLPs** ("FULL TRANS SIZE - WRITE AND READ RESPONSE CANNOT SPLIT"); if it would overflow MAX, the current TLP is closed first.
  - If `p2p_mode` is 0, each transaction closes its TLP on its last beat, so there is one AXI transaction per TLP.
- Reset (Iridium SE v2 1.3.0 regs): `C2C_TLP_TRANS_P_CFG_SIZE` (0x2c) = 0x02000400 → MAX = 1024 B, THRESH = 512 B; `C2C_TLP_TRANS_P_CFG` (0x30) = 0x10 → WAIT_THRESHOLD = 16 cycles. The SE FW changes only WAIT_THRESHOLD, through the `wr_coal` SV shell command (`el_c2c_sv_configure_wr_resp_coalescing`); 0 = no accumulation.
- AXI data path width `C2C_TLP_FMT_DATA_WDT = 512` bits (64 B beat).
- **A2 PCIe-Compliant Mode differs:** "Aggregation of multiple transactions into a single TLP at Tx is prohibited"; it uses standard MWr/MRd, standard BE, MPS/MRRS compliance and no AXI attributes in the TLP. Instead, the **Rx** side has a 512 B aggregation buffer per Requester (separate read and write buffers) that merges contiguous incremental requests before they go to the NoC. It does not cross a 512 B line and excludes FLAG and AG-MC/AG-UC traffic (A2 spec §7.1.4).

## Sources
- Historical secondary A1 source: `PCIe C2C.docx` (exact Git original unverified), paras 266–274 (mapping), 333–347 (multi-channel TLP, MPS 1024 / accumulation 512), 514–539 (decomposition)
- A2 revalidation source: `project.atlantic2.hwarch.specs:specs/pcie-c2c-Rev2.1.1/pcie-c2c.md`. Historical secondary locators: paras 308–322 (compliant mode, no Tx aggregation), 329–374 (Rx aggregation buffer)
- RTL: `/home/yoniizu/projects/iridium/rtl/ip/se/c2c_macro/rtl/c2c_tlp_transmit_pipe_p_data_gen.v` (`close_current_accum_tlp`, `send_native_full_trans_ins_byte`, `max_size_hard_limit_ins_byte`, `cnt_wait_above_thresh`, FSM)
- RTL: `.../c2c_top.v:630` (`p2p_mode_int` override); `ip/se/cluster/rtl/se_cls_pkg.v:25` (`SE_DM_MPS`); `c2c_pkg.v:73`
- Regs: `/home/yoniizu/projects/iridium/two/fw/build_artifacts/include/ips/se/v2/1_3_0_1p3_870/prototype/c2c_regs.h` / `c2c_regs_attr.h` (`C2C_TLP_TRANS_P_CFG_SIZE__RST=0x2000400`, `C2C_TLP_TRANS_P_CFG__RST=0x10`)
- FW: `fw/ip/se/drivers/c2c/el_c2c_sv.c:420-456`, `el_c2c_shell_sv.c:608-650`
