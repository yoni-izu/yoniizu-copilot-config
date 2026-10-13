# core_clk derivation in Iridium PCIe-EP RTL — and why it may be inactive

- **Recorded:** 2026-09-17
- **Status:** Verified from RTL (`ip.pcie.rtl` @ `origin/iridium`)
- **Tags:** core_clk, max_pclk, pclk, pipe_clk, ppw_wrap, ppw_macro, coreclkctl, ref_clk,
  DWC subsystem, phy black box, maxpclkreq, maxpclkack, dfx observation clock, clock not toggling,
  detect.quiet stuck, LTSSM clock

## Clock-derivation chain (RTL-traced, iridium branch)
```
P_REFCLK0/1_P/N_IN  (external differential PCIe RefCLK pads) ──► ppw_macro (lines 3269-3272)
      │
      ▼
ppw_wrap  (Synopsys PHY SerDes hard macro — BLACK BOX, no behavioral RTL in repo)
      │  TX PLL locks to the external RefCLK → generates per-lane
      ▼
pclk[7:0]   (PIPE clock, one per lane)                     ← what shows on DFX obs bus
      │
      ▼
ppw_macro   selects fastest lane / rate → max_pclk
      │  governed by maxpclkreq_n / maxpclkack_n + pclk_rate handshake
      ▼
max_pclk    (single fastest PIPE clock)
      │
      ▼
element_labs_ep8sx8_gen6_axi4_subsys  (DWC hardened EP subsystem, internal coreclkctl)
      │  integer-divide of max_pclk, same phase, UNGATED
      ▼
core_clk    (output of subsys_wrap) ──► MAC/LTSSM/controller  AND  piw_macro
```

## RTL evidence (repo `ip.pcie.rtl`, branch `origin/iridium`)
- `cluster/rtl/pcie_ep_top.v`
  - **PHY reference = external PCIe RefCLK pads.** `P_REFCLK0/1_P/N_IN` (lines ~93-96) route to
    `ppw_macro` (lines ~3269-3272) → `ppw_wrap` (the PHY). This is the clock the PHY TX PLL locks
    to; it is NOT sourced from any on-die PCIE_PLL.
  - `ref_clk` is a **top-level cluster input port** (line ~68) but it goes to **`piw_macro`**
    (line ~2767, `.ref_clk(ref_clk)`), i.e. the PCIe interface wrapper (NoC/controller functional
    side) — **NOT** the PHY reference. Do not conflate `ref_clk` with the PHY RefCLK.
  - `pclk` is an internal `wire [7:0]` (line ~939) with **no RTL driver** and connects ONLY to
    `ppw_macro.pclk` (line ~3307) ⇒ confirms the PHY that makes pclk is a **hard macro**, not
    behavioral RTL. In pure RTL sim pclk/max_pclk/core_clk are undriven → they exist ONLY from
    the physical PHY on silicon/emulation.
  - Instances: `element_labs_ep8sx8_gen6_axi4_subsys_wrap` (1902), `piw_macro` (2402),
    `ppw_macro` (3253), `tcu_macro`, `ftlb_macro`, `dma_pdma_macro`.
- `pep_macro/rtl/element_labs_ep8sx8_gen6_axi4_subsys_wrap.v`
  - `core_clk` is an **output** (line ~314); `max_pclk` is an **input** (line ~103). Inside,
    instance `element_labs_ep8sx8_gen6_axi4_subsys` produces core_clk from max_pclk (DWC
    coreclkctl). A `gen_stub.py` branch ties `core_clk = '0` when
    `EL_..._SUBSYS_WRAP_IS_STUB==1` (build-config, not silicon).
- `ppw_macro/rtl/ppw_macro.v`
  - `pclk` **input** (line ~82), `max_pclk` **output** (line ~83). Sub-instance
    `ppw_wrap ppw_wrap` (~line 192) is the Synopsys PHY; DFX/observation taps are gated by
    `std_cg` clock-gates `dfx_pma_2g_cg / 1g / 500m / 100m / pcs_1g_cg` → these drive
    `dfx_pma_2g_clk … dfx_pcs_1g_clk` (the clocks visible on the observation bus).
  - max_pclk selection is governed by `pclk_pep2ppw_mac_phy_maxpclkreq_n`,
    `pclk_ppw2pep_phy_mac_maxpclkack_n`, `pclk_pep2ppw_mac_phy_pclk_rate`.

## CORRECTION to earlier KB (`detect-quiet-stuck-debug.md`)
Earlier text said "MNG PCIE_PLL sets the controller **core_clk** rate" and (in a first pass) that
MNG PCIE_PLL feeds the PHY `ref_clk`. **Both are wrong per RTL:**
- The **PHY reference is the external PCIe RefCLK pads** `P_REFCLK0/1_P/N_IN`, not any on-die PLL.
- **`core_clk` is derived from `max_pclk` (pipe_clk) inside the DWC subsystem**, not from the
  MNG PCIE_PLL.
- The top-level `ref_clk` port feeds **`piw_macro`** (PCIe interface wrapper), not the PHY.
What the MNG PCIE_PLL (`fla_rom_configure_pll_clk_div()`) actually drives is **outside this PCIe
cluster** (MNG/clock domain) and is NOT proven from this repo — do not assert it feeds the PHY
ref or core_clk. Candidate destination is the PIW/controller-side functional `ref_clk`, but that
is unverified. Bottom line: **core_clk is a function of the PHY pclk (locked to external RefCLK),
not of the MNG PLL divider.**

## Why core_clk may NOT be active (RTL-grounded)
1. **max_pclk not toggling (root cause)** → because pclk not toggling → PHY (`ppw_wrap`) TX PLL
   not locked, external **PCIe RefCLK (`P_REFCLK0/1`) absent/wrong**, PHY held in reset, or PHY in
   powerdown (P2). core_clk is "never gated; runs until pipe_clk ceases" (databook p.79) → if
   max_pclk stops, core_clk stops.
2. **maxpclkreq_n / maxpclkack_n handshake stuck** → PHY never presents max_pclk at the expected
   rate, so the subsys has no valid max_pclk to divide.
3. **Reset asserted** on the PHY/subsys path: `async_ppw_rst_n`, `phy_rst_n`, `core_rst_n`,
   `power_up_rst_n`, `perst_n` → coreclkctl held, core_clk absent even if max_pclk toggles.
4. **PHY is a black box** → if bring-up relies on RTL-only sim, pclk/max_pclk/core_clk are
   undriven; must run on emulation/silicon with the real PHY (and PHY-FW up) to get core_clk.
5. **Build stub** (`IS_STUB==1`) ties core_clk to '0 — a config/integration issue, not silicon.

## Connect to the observed "~1 GHz pclk" symptom
The DFX observation taps are the **gated PMA/PCS clocks** (`dfx_pma_2g/1g/500m/100m_clk`,
`dfx_pcs_1g_clk` via `std_cg` in `ppw_macro`). A ~1 GHz reading there is a raw PMA/PCS pipe tap,
**not** the divided `core_clk` the MAC uses. core_clk = integer-divided max_pclk inside the DWC
subsys and does NOT appear on those PMA taps. So "1 GHz on obs bus" does not prove core_clk is
alive — if core_clk (MAC clock) is dead, LTSSM can't leave Detect.Quiet (matches
`SMLH_LTSSM_STATE` frozen while config-plane writes still land).

## Sources
- RTL: `ip.pcie.rtl` @ `origin/iridium`: `cluster/rtl/pcie_ep_top.v`,
  `pep_macro/rtl/element_labs_ep8sx8_gen6_axi4_subsys_wrap.v`, `ppw_macro/rtl/ppw_macro.v`.
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.78–79 (core_clk derived from pipe_clk; never gated).
- KB: `detect-quiet-stuck-debug.md`, `detect-exit-refclk-requirement.md`.
