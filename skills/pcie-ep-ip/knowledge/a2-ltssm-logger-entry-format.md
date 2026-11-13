# A2 LTSSM logger entry format, clocking and candidate extra fields

- **Recorded:** 2026-11-12
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, ltssm logger, entry format, timestamp, core_clk, data rate, pm_current_data_rate, link width, cfg_neg_link_width, smlh_link_up, rdlh_link_up, l0p, flit, ippciesw-519
- **Source:** `neo-ask --chip a2`, mirror 2026-11-09: ip.pcie.rtl@77b45f1 (`pep_macro/rtl/ltssm_logger.v`, `pcie_iip_device_wrap.v`, `pep_wrap.v`), ip.pcie.csr@6904415 (`yml/pep_macro/PEP_CTRL_CORE.yml`). Context: Ehud Vardi (designer) email 2026-11-12 says he will add the current data rate.

## Findings
- **Entry word:** 46 bits in RAM, with no spare bits.
  - Bits [31:0] hold the timestamp.
  - Bits [37:32] hold the LTSSM state.
  - Bits [45:38] hold the state reason.
- **Depth:** 256 entries, hardcoded in the wrap address widths.
- **Timestamp:** a free-running 32-bit counter clocked by `core_clk`.
  - It counts while the logger is enabled and is cleared by a logger reset.
  - It has no wrap flag. It wraps after about 4.3 s at 1 GHz.
  - `core_clk` scales with the rate. Ehud gives A1 Gen1..6 as 125/250/250/500/1000/1000 MHz; the A2 Gen7 value is unknown.
- **Write trigger:** a change of `smlh_ltssm_state` only.
  - If both the old and the new state are masked, the transition counts toward the threshold.
  - Once the threshold is reached, further transitions are suppressed silently, with no drop indication.
- **CSR readback:** `LTSSM_LOGGER_ENTRY_DATA` uses bits [13:0] (state [5:0] and reason [13:6]).
  - Bits [31:14] are free (18 bits), so new fields fit in the existing register.
  - New fields still need a RAM `WIDTH` increase and new readback wiring.
- **Already wired into the logger but unused:** `smlh_ltssm_previous_state`, `smlh_ltssm_in_l0p` and `smlh_ltssm_l0s_state`.
- **Cheap candidate signals at the wrap:**

  | Signal | Width | Clock |
  |---|---|---|
  | `pm_current_data_rate` (present, no consumer) | 3 bits | domain unverified |
  | `smlh_link_up`, `rdlh_link_up` | 1 bit each | `core_clk` |
  | `smlh_ltssm_state_rcvry_eq` | 1 bit | `core_clk` |
  | `cfg_neg_link_width` | 6 bits | `aux_clk`, so CDC or a quasi-static argument |
  | `cfg_pcie_cap_flit_mode_stat` | 1 bit | `aux_clk` |
  | `usp_eq_redo_executed_int` | 1 bit | `core_clk` |
  | `smlh_req_rst_not` / `link_req_rst_not` | 1 bit each | edge-detected in `core_clk` |

- **Bit budget for the 18 free readback bits** (proposal sent to Ehud, 2026-11-12):
  - Rate 3 + width 3 (encoded) + link-up 2 + wrap flag 1 + dropped flag 1 + L0p/rcvry_eq 2 + flit 1 = **13 bits**. Adding all three reset/EQ-redo bits makes **16**, which fits.
  - With the raw 6-bit `cfg_neg_link_width`, the core set is 16 bits and the full set is **19 bits, which does not fit**. Encode the width, or drop some of the reset/EQ-redo bits.
- **PIPE-side signals:** `mac_phy_rate`, powerdown and per-lane `rxelecidle` exist only at the `pep_wrap` boundary in `pclk`. Using them needs routing and CDC.
- **Not available:** EQ phase number, directed-speed-change or upconfigure flags, L0p width, per-lane receiver-detect results and timeout counters. Some of these may be encoded in the Synopsys state reason, which is undecoded.
