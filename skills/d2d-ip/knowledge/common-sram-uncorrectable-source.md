# Common SRAM uncorrectable error source

- **Recorded:** 2026-10-28
- **Status:** Verified from RTL wiring; decoder and vendor interrupt logic unavailable
- **Tags:** sram, uncorrectable, cmn_top, e_data_error, interrupt, ecc

## Scope / Question

What drives the Common Top SRAM uncorrectable error interrupt in the local D2D RTL?

## Findings

- Examined `ip.d2d.rtl` revision `8fe3b95d16a4d46330905cad92a6403852154413`,
  branch `main`, at `/home/yoniizu/projects/iridium/rtl/ip/d2d`.
  Despite the directory name, vendor symlinks target Atlantic2 v12 N3 sources.
  Do not treat this as verification of Iridium frozen RTL.
- In both `el_mid_a_prot_adapt_mem_wrap.v` and
  `el_side_a_prot_adapt_mem_wrap.v`, instance `cmn_ram` of
  `el_mem_top_d2d_d2d_cmn` drives `o_cmn_top_sram_uncorr_err` directly from
  its `e_data_error` output (line 103). Reads use
  `r_vld = sram_sel & sram_nwr_rd` (line 96); read data returns through
  `sram_rdata` (line 102). The exact decoder condition and output timing
  cannot be determined from the instantiation.
- The subsystem connects this signal to the PHY wrapper input
  `i_cmn_top_sram_uncorr_err`. Adapter retry memory has a distinct
  `o_tx_rt_mem_uncorr_err`; do not conflate these sources.
- Common SRAM `o_cmn_top_sram_corr_err` is tied low in these wrappers
  (line 115).
- The separate adapter retry SRAM is `u_adapt_tx_rt_mem`, instantiated as
  64 entries of 530 bits with a 6-bit address (lines 141-146). This is
  adapter transmit replay storage, not common SRAM. UCIe 1.1 section 3.7,
  page 73, requires replay on a received CRC mismatch; section 3.2's
  multi-protocol rules explicitly describe transmitting flits from the TX
  retry buffer. Do not equate one 530-bit memory entry to one complete
  UCIe flit without inspecting the unavailable adapter implementation.
- The memory implementation is consumed through
  `$ENV_NAME/mem_wrap_rtl/d2d/misc/el_mem_top_d2d_d2d_cmn.lst`.
  Vendor files are consumed through `ucie_ip_links`. The local `/vlsi`
  targets were unavailable. Exact ECC classification, injection controls,
  mask polarity and final interrupt aggregation were not RTL-verified.
- Corroboration only, from the older N5 GammaCORE32 MMR v1.0.0_0.5.3:
  `reg_glbl_cmn_top_int1` at GLOBAL_REG offset 0xD0 and
  `reg_glbl_cmn_top_int2` at 0xD4 have SRAM uncorrectable status at bit 0,
  RW1C. Corresponding masks are 0xD8/0xDC bit 0, reset 1.
  These are not verified N3-register definitions.

## Sources

- RTL: `ip.d2d.rtl` -> `d2d/rtl/el_mid_a_prot_adapt_mem_wrap.v:87`
  (`cmn_ram`), `:96` (read request), `:103` (`e_data_error`),
  `:115` (correctable tie-off), `:152` (separate retry error).
- RTL: `ip.d2d.rtl` -> `d2d/rtl/el_side_a_prot_adapt_mem_wrap.v:87`
  (same common SRAM wiring).
- RTL: `ip.d2d.rtl` ->
  `d2d/rtl/eland_gammaN64B1Tn3_ss_e_subsystem_mod.v:835`
  (PHY input), `:950` (memory wrapper instance), `:954` (error output).
- Build list: `d2d/misc/d2d_mid_ctrl_ew_macro.lst:44`.
- Vendor MMR:
  `Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.pdf`,
  sections 3.53-3.56, pages 145-148.
- UCIe 1.1: `ucie_1p1.txt:2082` (TX retry buffer),
  `:3786` (CRC mismatch requires replay), section 3.7, page 73.
