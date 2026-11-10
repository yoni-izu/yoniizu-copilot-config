# C2C PEER_CREDIT per-peer allocation

- **Recorded:** 2026-11-01
- **Status:** Verified from A1 FW and Iridium-frozen RTL; basis for seven peers requires topology confirmation
- **Tags:** c2c, peer_credit, credits, switch, dts, performance, backpressure

## Branch state (updated 2026-11-02)
- `proj_infra` **`origin/a1_power_on_branch`**: all four port-7 nodes `num-switch-peers = <7>` via `c005159e [IPSESW-410] Set C2C port-7 peer count to seven` (2026-11-01) → CTRL=36, DATA=292, `PEER_CREDIT=0x01240024`. Also underlies the infra #1566 branch `dev/akshay.bharad/se-lut-o-bdf` (the se_lut_scheme_o image flashed on pd02).
- `proj_infra` **`origin/main`**: still `<16>` via `5f652e54 [SW-0] dts: a1: set C2C port7 num-switch-peers to 16` → CTRL=16, DATA=128. The seven-peer change was NOT merged to main.
- Always state which branch when quoting the peer count; the earlier "16" in this file was the main value.

## Findings
- A1 FW's `c2c_program_peer_credit_impl()` divides the reset pools (CTRL=256, DATA=2048) by `num-switch-peers` for VIA_SWITCH links, then writes the two fields to `PEER_CREDIT`. The four A1 port-7 die nodes currently specify 16 peers, so they yield CTRL=16 and DATA=128.
- The screenshots shared on 2026-11-02 show Maxim asking Hanna to try reset value `0x08000100`; they do not establish that 36/292 was his intended target. That reset value decodes to CTRL=256 and DATA=2048. The `a1_power_on_branch` setting is instead CTRL=36 and DATA=292; do not attribute either request to Maxim without the original message/context.
- Property propagation is direct: `EL_C2C_INIT` initializes `el_c2c_config.num_switch_peers` with `DT_INST_PROP_OR(inst, num_switch_peers, 1)`; `c2c_program_peer_credit_impl()` reads that field and is called during C2C hardware init. The calculation runs only for `EL_SE_LINK_VIA_SWITCH`; the A1 port-7 DTS nodes set that link type. The current repo-wide DTS search found no overlay that overrides `num-switch-peers`.
- For divisor 7, FW computes 36 and 292, both below the 9-bit and 12-bit field maxima, so the clamps preserve them. RTL CSR layout puts CTRL in bits [8:0] and DATA in [27:16], so the composed register word is `0x01240024`; FW writes that word to the C2C `PEER_CREDIT` register. This is a source-level trace/calculation, not a build or hardware readback with the property changed.
- Iridium-frozen RTL defines 256 control credits and 2^17 receive-data bytes divided by a 512-bit (64-byte) AXI data beat, yielding 2048 data credits. `PEER_CREDIT` seeds the per-peer remote-credit counters; those counters track releases/consumption and their available credits gate transmission. The requested values fit the control/data fields and counters.
- The divisor is a topology/resource-allocation contract, not just a tuning knob. Seven is safe only if no more than seven peers can consume the shared pool concurrently. Retaining 16 peers while advertising 36/292 each could over-allocate the 256/2048 shared budgets; confirm the seven-peer limit against the actual switch/sled topology before changing DTS.
- **Why reset credits could improve latency:** in Iridium-frozen RTL, `peer_credit_trig_wr` loads the programmed limits and seeds each peer's tracked available-credit counters (`c2c_remote_credit_manager_top.v`). The transmit path gates AR on nonzero AR credit and AW on nonzero AW credit plus enough W credit for the transaction length (`c2c_tlp_transmit_pipe_buffers.v`). Thus changing from 36/292 to 256/2048 can enlarge the in-flight window substantially (DATA: 292×64 B ≈18.7 KB vs 2048×64 B=128 KiB) and avoid bubbles while waiting for credit returns. This plausibly explains a small measured latency improvement (~320 ns) if the test was credit-limited; it does not prove that the reset value is safe for the actual switch fan-in or that credit starvation caused the result. Compare repeated same-workload runs and per-peer credit/overflow status before making it the default.

## PEER_CREDIT write = live counter reload (verified 2026-11-05)
- Every write to `PEER_CREDIT` raises `peer_credit_trig_wr`, which (a) latches the max-credit thresholds and (b) **overwrites every peer's available-credit counter** (AW, W, B, AR and R, for all `C2C_MAX_PEERS`) with the programmed value. The trigger takes priority over consume/release events in the same cycle. Outstanding in-flight accounting is discarded. (`c2c_remote_credit_manager_top.v:57-72, 88-106, 124-142, 160-178, 196-214, 232-250` @ 9cb3d7d)
- So the register is "static config" only if it is written once at init. Rewriting it at runtime acts as a credit-counter reset. That is why the customer's `devmem ... 0x08000100` restored latency in aqua-external/Discussion#82, and it is the mechanism behind the IPPSOCSW-1266 workaround (die0 TuC0 rewrites CTRL=0xFF/DATA=0x7FF on D0..D3 every 1 µs; infra#1609 and psoc_mng#546).
- Side effects: a release that arrives after a reload pushes a counter above max, so `*_ovf` (`counter > max`) fires. With each peer reloaded to the full pool every period, credit backpressure effectively disappears. Receive-buffer protection then depends on drain rate. The 1 µs period is well below the time needed to fill 128 KiB at Gen5 x4.
- Bug-triage phenomenon: PH-019.

## Sources
- FW: `/home/yoniizu/projects/iridium/a1_power_on_004/fw/ip/se/drivers/c2c/el_c2c.c:439-494` (`c2c_program_peer_credit_impl`)
- FW: `/home/yoniizu/projects/iridium/a1_power_on_004/fw/ip/se/drivers/c2c/el_c2c.c:1128-1145` (`DT_INST_PROP_OR` populates `num_switch_peers`); `:929-944` (C2C hardware init calls credit programming)
- FW: `/home/yoniizu/projects/iridium/a1_power_on_004/fw/ip/se/dts/bindings/el,c2c.yaml:48-54` (`num-switch-peers` contract)
- FW: `/home/yoniizu/projects/iridium/a1_power_on_004/fw/proj_infra/soc/dts/a1/a1_hsi.dtsi:2365-2448` (four port-7 nodes, each configured for 16 peers)
- RTL: `ip.se.rtl`, Iridium-frozen branch at `9cb3d7d548760ce84215da2561dbd61620a9e00f`, `c2c_macro/rtl/c2c_pkg.v:242,280-293` (512-bit beat, 128-KiB receive buffer, credit totals and counter widths)
- RTL: same branch, `c2c_macro/csr/rtl/csr_c2c_fields.v:153,2558-2594` (CTRL/DATA field widths, bit slices and reset values 0x100/0x800)
- RTL: same branch, `c2c_macro/rtl/c2c_remote_credit_manager_top.v:58-72,92-106,128-142` (programmed per-peer thresholds/counters)
- RTL: same branch, `c2c_macro/rtl/c2c_tlp_transmit_pipe_buffers.v:342-347` (AW transmission waits for control and data credits)
- RTL: same branch, `c2c_macro/rtl/c2c_tlp_transmit_pipe_buffers.v:161-165,342-347` (AR/AW scheduler credit gates); `c2c_macro/rtl/c2c_remote_credit_manager_top.v:57-72,87-102,261-274` (credit limit loads, counter seed, peer status)
