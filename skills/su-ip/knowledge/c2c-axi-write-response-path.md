# C2C AXI Write Request and B-Response Path

- **Recorded:** 2026-10-28
- **Status:** Verified from Iridium RTL; port-7 switch mapping not traced
- **Tags:** c2c, axi, write, aw, w, b, response, counters

## Scope / Question
Whether an AXI write sent over the Iridium C2C/PCIe path uses AW outbound and B inbound, and how to interpret AW activity without B activity.

## Findings
- Yes, at the AXI interfaces the write address (AW) and write data (W) flow toward the remote target, and the target's B response flows back. AW alone is not a complete write: the corresponding W beat(s), including the final beat, must also be accepted.
- The AXI-to-TLP formatter forwards AW and W valid/ready independently and maps the returning TLP-pipeline B valid/ready and response/ID back onto the originating AXI B channel. Thus AW/B are logical AXI channels at the interfaces, not literal AXI wires transported over PCIe.
- On the receiving side, C2C TLP AW/W are presented to the downstream AXI master interface; B valid/response/ID are taken from that downstream interface and returned toward the C2C transmit path.
- The receiver's AW-outstanding counter increments on `c2c_tlp_fmt_aw_valid && c2c_tlp_fmt_aw_ready`. It decrements on a B response handshake or specific isolation/early-response-release handshakes. An AW count rising with no B can therefore indicate an outstanding write leak and eventually trigger AW throttling.
- That AW-outstanding count is an internal RTL signal, not shown as a software-readable counter in the inspected RTL. `AW_OUTS_LOCK_TH` / `AW_OUTS_REL_TH` are thresholds controlling throttling, not a live count.
- On the C2C upstream-to-DM write path, `DM_UPS_WR_OUTSTANDING` is 512. The rate-limiter's internal AW outstanding counter increments on an accepted AW and decrements on a returned B handshake; its optional CSR limiter stalls new AWs at the programmed `ups_pipe_outs_limit_aw_max_outs` when `ups_pipe_outs_limit_aw_en` is set. This is the design's outstanding-tracking/ID-space bound, not a statement that all 512 are currently enabled or attainable under all traffic/credit conditions.
- In the generated Iridium SE v2 1.3.0 register artifacts, `C2C_UPS_PIPE_OUTS_LIMIT_AW` at SE-relative offset `0x1F4` resets to `0x00000200`: `MAX_OUTS=512`, `EN=0`. Thus the optional limiter is disabled at hardware reset; firmware or other software may change the live value.
- The transmit-side staging FIFOs are separate from outstanding capacity: AW and B control FIFOs are each depth 4; W data FIFO depth is 8 beats. These queue entries are not the total number of unresponded transactions.
- If observed counters correspond to those transmit FIFO occupancies, AW=4 and W=8 are exactly both FIFOs full. The AW FIFO only pops when the scheduler grants transmission, W data is available, and (in P2P mode) remote AW and sufficient W credits are available; W FIFO pops on scheduler grant. Thus the matching saturation points strongly indicate transmit-side queue backpressure/stall, not a 4-transaction outstanding limit. Verify the counter source before treating this as definitive.
- The master- and slave-side write-response interrupt pulses require a completed B handshake with nonzero BRESP (`BVALID && BREADY && |BRESP`). They report error responses, not every successful write; absence of this interrupt does not prove that B was absent.
- For register-only diagnosis, compare software-readable sticky error/interrupt causes and recorded BRESP-info before/after a controlled run, and inspect any target-specific write/error counters that the system exposes. A successful AXI write may leave no C2C error interrupt. The available Iridium RTL does not establish what system-level switch port 7 maps to or expose a normal-B completion counter.

## Sources
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_axi_master_pipe_axi_2_tlp_fmtr.v` (`c2c_tlp_fmt_aw_valid`, `c2c_tlp_fmt_w_valid`, `axi_slave_if.b_valid`)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_axi_slave_pipe_tlp_fmtr_2_axi.v` (`aw_outs_dec`, `el_counter_aw_outs`, `axi_master_if_int.{aw,w,b}_valid`)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_top.v` (`axi_m_pipe_c2c_tlp_fmt_b_valid`)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_axi_master_pipe_top.v` and `ip/se/c2c_macro/rtl/c2c_axi_slave_pipe_top.v` (write-response error interrupt pulse conditions)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/cluster/rtl/se_cls_pkg.v` (`DM_UPS_WR_OUTSTANDING=512`)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_axi_ups_pipe_rate_limiter.v` (optional programmed outstanding limiter)
- RTL: `/home/yoniizu/projects/iridium/rtl` → `ip/se/c2c_macro/rtl/c2c_pkg.v` and `ip/se/c2c_macro/rtl/c2c_tlp_transmit_pipe_buffers.v` (AW/B control FIFO depth 4; W data FIFO depth 8)
- Generated registers: `/home/yoniizu/projects/iridium/two/fw/build_artifacts/include/ips/se/v2/1_3_0_1p3_870/prototype/c2c_regs.h` (`C2C_UPS_PIPE_OUTS_LIMIT_AW` offset and field positions) and `c2c_regs_attr.h` (`C2C_UPS_PIPE_OUTS_LIMIT_AW__RST`)
