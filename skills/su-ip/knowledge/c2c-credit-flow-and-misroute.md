# C2C credit flow end-to-end + credit-misroute mechanism (PH-019)

- **Recorded:** 2026-11-05
- **Status:** Verified from Iridium RTL ip.se.rtl @ 9cb3d7d (c2c_macro); packing-misroute hypothesis retracted (port 7 p2p_mode=0); true root cause open
- **Tags:** c2c, credit, crdt, peer_credit, p2p_mode, accumulation, misroute, backpressure, PH-019, aqua-external#82

## Credit loop (receiver-managed, per peer)
1. Rx: one shared SRAM FIFO per channel for all peers (AW/AR/B 256 deep, W/R 2048 x 64 B = 128 KiB). A full FIFO -> `buff_ready=0` -> the parser stalls -> PCIe link-level FC backpressure. There is **no drop** (`c2c_tlp_receive_pipe_tlp_fmt_buffer.v`).
2. On FIFO **pop** (drain to the NoC), the local credit is released and tagged with the `peer_idx` stamped at parse time (fmt_buffer:517-540). The peer_idx comes from the CAM lookup of the TLP requester ID (`c2c_req_id_2_peer_idx_cam.v`; no-hit -> idx 0 plus a maskable interrupt).
3. The local credit manager accumulates per peer -> RR arbiter + gap/rate limiter -> a CRDT message (each field saturates at 255) addressed to `dpa_to_npa_table[peer]` (`c2c_local_credit_manager_top.v`). Side bug: the b_cred overflow check uses `aw_credit[WDT:8]` (copy-paste).
4. The CRDT message goes to the Tx scheduler as a 5th WRR agent alongside AW/AR/B/R (`c2c_tlp_transmit_pipe_scheduler.v:135-150`, weight `schd_arb_wgt_credit`), i.e. it rides the same posted path as data.
5. Sender: the remote credit manager keeps per-peer counters; consume on Tx; incoming CRDT adds to the counter of the peer_idx derived from **the TLP requester ID**. AW is gated on AW plus enough W credit. A PEER_CREDIT write **overwrites the live counters** (see c2c-peer-credit-allocation.md).

## p2p_mode semantics (verified 2026-11-05) — packing-misroute hypothesis RETRACTED
- `p2p_mode` = point-to-point port with ONE peer. At frozen ip.se.rtl 9cb3d7d, `c2c_tlp_transmit_pipe_buffers.v:161,342,395` select `single_peer_remote_credits` when p2p_mode=1 and `multi_peer_remote_credits[peer_idx]` when it is 0. Tx coalescing (`p_data_gen`) is enabled only when p2p_mode=1 (frozen `c2c_tlp_transmit_pipe_top.v:429`, `c2c_top.v:566` CSR override).
- Straps: ports C2C[0..6] are tied `'1` (frozen `cluster/rtl/se_cls.v:3119,4004,...,8429`). Port C2C[7] (the switch port, inside `pcie_ep_cls`) is tied `'0` (`project.iridium.rtl fullchip/rtl/a1_chip.v:6274` `.async_c2c7_p2p_mode('0)`). No FW writer of `C2C_CFG.P2P_MODE_OVRD` was found in ip/se.
- Therefore port 7 has per-peer credits and **no** multi-message TLPs, so the "CRDT rides a TLP addressed to another peer" mechanism cannot occur there. Retracted.
- The A1 receive-side CAM (`c2c_req_id_2_peer_idx_cam`) is separate from the A2 `dns_pipe_fork` CAM, which forces idx 0 under `cfg_optimized_mode_en`. A2 main (0b9a7bd) redefines p2p_mode as optimized-vs-compliant; do not mix the two revisions.
- PH-019 numbers (A 83 vs ~105, C 43 vs ~26; sum ~conserved) point to **B misattributing releases between peers** (Rx peer_idx stamping in the P parser / CAM, or the local credit manager indexing), not to credit loss. Open.

## NoC drain context (Iridium NoC spec)
- 8 C2C ports/die x Gen6 x4 32 GB/s = 256 GB/s ingress (A1 FW caps the link at Gen5 -> 128 GB/s). GMEM is 1 TB/s per die, shared with the VCores (768 GB/s max) -> oversubscribable. Remote-die destinations also cross D2D (hD2D 64 GB/s per instance). Spec para 1062-1071 itself warns of HOL blocking, and its E2E credits cover only the SoC->C2C/PCIe direction.
- 128 KiB Rx data FIFO fills in ~4 us at Gen6 (~8 us at Gen5) once the drain stops.

## Sources
- RTL /tmp copy of ip.se.rtl c2c_macro @ 9cb3d7d: tlp_transmit_pipe_scheduler.v, tlp_transmit_pipe_p_data_gen.v, local/remote_credit_manager_top.v, tlp_receive_pipe_tlp_fmt_buffer.v, tlp_receive_pipe_p_tlp_parser.v, req_id_2_peer_idx_cam.v, c2c_top.v

## p2p_mode=0 deep-check (frozen 9cb3d7d, 2026-11-05)
- Tx: one TLP per item. Close condition is LAST beat or a len=0 item (`p_data_gen.v:291,296,184`). CRDT has its own address = `local_credit_transmit_req_addr` (`scheduler.v:283`) and is masked while a W/R burst is in progress (`scheduler.v:115`). Cross-peer co-packing is impossible.
- Rx P parser: a multi-TLP byte window with per-TLP header slots (addr/req_id/byte_ptr). An item is consumed only if it fits in the head TLP's remaining bytes (`p_tlp_parser.v:534-536`). The shift to the next TLP happens only at byte_ptr==0 or on NULL padding (`:369`). The CAM looks up the head slot's req_id (`:556`), so items are tagged with their own TLP's req_id. No parser mis-tag mechanism was found.
- CAM (`c2c_req_id_2_peer_idx_cam.v`): no-hit → idx 0; multi-hit → lowest idx. Duplicate or zero requester IDs in REQ_ID_TO_PEER_IDX_TABLE (or peers sharing a BDF behind the switch) would silently merge peers.
- A uniquely-matched but WRONG table entry fires neither no_hit nor mult_hit, and credit OVF is not guaranteed. Only detectable by comparing REQ_ID_TO_PEER_IDX_TABLE against the requester IDs actually observed per peer.
- Correct received data does NOT validate peer_idx: data is routed by the address in the TLP, credits by CAM(req_id). Both come from the same parser header slot, so correct data clears slot selection only, not the CAM mapping.
- Interrupts: `c2c_sei_{p,np,cpl}_parse_peer_idx_{no,mult}_hit` (+ req_id info reg), `c2c_sei_credit_{aw,w,b,ar,r}_ovf` (available > max; per-peer bitmap in peer_h/peer_l info). There is **no underflow interrupt** (the starved peer is invisible). Also `c2c_sei_{p,np,cpl}_parse_unknown_fmt/stuck` and `c2c_sei_c2c_{p,np,cpl}_tlp_discard`. The every-1 µs PEER_CREDIT reload resets the counters before an OVF can build up, so the workaround hides the evidence.
- Code nit: `p_tlp_parser.v:665` uses C2C_REC_CPL_PARSER_WIN_NATIVE_BEATS in the P parser.
