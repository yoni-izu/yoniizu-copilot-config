# Stuck in LTSSM Detect.Quiet (never reaches Detect.Active) — debug

- **Recorded:** 2026-09-17
- **Status:** Verified from doc (controller EP databook) + KB cross-ref
- **Tags:** detect.quiet, detect quiet, stuck in detect, ltssm not advancing, app_ltssm_enable,
  core_clk gated, pclk vs core_clk, max_pclk, phystatus, app_hold_phy_rst, cold reset sequence,
  12ms timeout, linkup debug, cpu_run_ack, perst held, pll lock, pclk 1ghz,
  core_clk_detect_ind, core_clk_running_poll, ltssm clock not running, ltssm cannot advance

## Scope / Question
FW sees pclk live (~1 GHz on observation bus) but the link never leaves **Detect.Quiet**.
Enumerate causes.

## Key protocol fact
Detect.Quiet has a **guaranteed 12 ms timeout → Detect.Active** (also exits early on Electrical
Idle break). So *permanent* residence in Detect.Quiet is NOT a "no receiver / no terminations"
problem (that stalls in Detect.**Active**). It means the **LTSSM engine isn't advancing or is
held disabled**. Live pclk is necessary but NOT sufficient.

## core_clk & LTSSM — what is STATED vs INFERRED (do not overclaim "proof")
The databook has **no explicit sentence** "LTSSM cannot leave Detect without core_clk." What is
actually stated, and the honest chain:
- **p.79 Table 2-4:** `core_clk` is "the primary clock for the controller" (native_core_clk /
  radm_clk_g / rxadm_ram_core_clk are gated versions of it). LTSSM is controller core logic ⇒
  it's in the core_clk domain — **architectural inference, not a stated theorem**.
- **p.78:** "In normal operation (L0/D0), core_clk … derived from pipe_clk." Detect is a **D0/
  active** PM state (not L1/L2/Reset) ⇒ `muxd_aux_clk = core_clk` in Detect (aux only substitutes
  in L1/L2/Reset).
- **p.330** ("LTSSM state transitions don't take effect because the LTSSM clock is not running")
  is stated for **L1 only** (core_clk deliberately removed there). It shows the principle + names
  the clock (core_clk) but is **NOT** a statement about Detect. Do not cite it as Detect proof.
- **Corroborating (NOT proof):** HW `CORE_CLK_DETECT_IND` bit + FW `core_clk_running_poll()`
  gating `ltssm_en_set()` show only that Synopsys's bring-up flow *treats* core_clk-running as a
  precondition. Vendor intent, not an architecture theorem.
- **Rate-change paradox (LTSSM sets core_clk rate yet is clocked by it):** no contradiction —
  LTSSM changes the *rate (frequency)* via `mac_phy_rate`, never *stops* the clock; the switch is
  **glitch-free** ("requires two cycles of the core_clk to perform the clock switch," PHY keeps
  supplying cycles, p.91) — like a CPU reprogramming its own DVFS while executing. Also rate
  changes occur in **Recovery.Speed after link-up**, irrelevant to leaving Detect.Quiet (fixed
  Gen1 rate there).
**Takeaway:** core_clk alive is a *necessary* condition for LTSSM to run (primary clock,
un-substituted in D0/Detect); `CORE_CLK_DETECT_IND=0` is a strong smoking gun — but frame it as
architecture+vendor-flow, not as an explicit databook proof.

## core_clk vs pipe_clk (databook-authoritative — DO read before blaming/absolving core_clk)
- LTSSM/MAC runs on **core_clk**; it is **NOT optional**. But core_clk is **derived from
  `pipe_clk`**: "In normal operation (L0/D0), core_clk and core_clk_ug are derived from
  pipe_clk" (p.78); "core_clk and pipe_clk must have the same phase and frequencies that are
  integer multiples of each other" (p.78); Fig 2-10 (p.79): "Normally all clocks are derived
  from pipe_clk. core_clk is never gated or shut off. It runs until pipe_clk ceases."
- So "need core_clk" and "have pclk" are NOT independent: if a valid pipe clock reaches the
  controller and reset/isolation is released, core_clk exists. Real question = **is a valid
  pipe/core clock at the RIGHT RATE reaching the MAC past isolation+reset?**
- **core_clk is derived from the PHY pipe clock (RTL-verified), NOT from MNG PCIE_PLL.**
  RTL chain (see `core-clk-derivation-rtl.md`): external **PCIe RefCLK pads `P_REFCLK0/1`** → PHY
  `ppw_wrap` TX PLL → `pclk[7:0]` → `ppw_macro` → `max_pclk` → DWC subsys coreclkctl
  integer-divides → `core_clk`. The top-level `ref_clk` port feeds **`piw_macro`**, not the PHY.
  What the **MNG PCIE_PLL** (`fla_rom_configure_pll_clk_div()`, DIV=4 Gen1) drives is outside this
  PCIe cluster and is **unverified** — it does NOT feed the PHY RefCLK (external pads do) and does
  NOT set core_clk. So a live `core_clk` requires a valid PHY `max_pclk` past reset. Seeing 1 GHz
  "pclk" does NOT prove core_clk is live.
- **Rate sanity:** Gen1 (2.5 GT/s) pipe/core clock is LOW (~62.5–250 MHz by datapath width).
  A **1 GHz** reading ⇒ likely `max_pclk` or the *undivided* PLL output (DIV/mux not applied) —
  a red flag, not proof of a good core clock.

## Prime suspects (priority order)
1. **`app_ltssm_enable = 0`** — Controller parks in Detect.Quiet BY DESIGN when low.
   Databook: "Unless app_ltssm_enable is de-asserted immediately in Detect state, the controller
   continues to link up." Cold-reset order: PHY makes stable clocks (max_pclk/pclk) → THEN
   app sets `app_ltssm_enable=1` → training starts.
2. **Controller core clock absent / wrong rate** — LTSSM FSM + 12 ms Detect timer run on
   **core_clk** (derived from pipe_clk, gen-rate). On Iridium the gen-rate is set by the MNG
   PCIE_PLL div/mux — if DIV=4/mux-switch didn't land, PLL unlocked, or you're probing
   `max_pclk`, the MAC has no usable core_clk. **`SMLH_LTSSM_STATE` invisible/frozen while
   config writes still land is the tell**: DBG_SIG (0x44) is in the PEP wrapper plane (config/AXI
   clock, alive), but the LTSSM field is sampled from the core_clk (MAC) domain — frozen at
   Detect(=0) if core_clk is dead. This defeats even the guaranteed 12 ms Detect.Quiet timeout.
3. **Controller still in reset** — `power_up_rst_n`, `xxx_core_rst_n`/non_sticky/sticky, or
   `perst_n`/`phy_perst_n` still asserted. (Iridium: PRSTn-to-PSOC masking `PRST_SELECT_SOURCE`
   quirk can leave PERST effectively asserted.)
4. **`app_hold_phy_rst = 1`** — intentionally disables link training for PHY-register
   reprogramming; if never dropped, parks in Detect.
5. **PhyStatus stuck asserted / PHY-FW not ready** — MAC waits for PhyStatus de-assert (PHY
   ready) before driving Detect. Check `PPW_CTRL_CPU_IND.CPU_RUN_ACK` @ 0x100000000488010.
6. **1 GHz clock may be `max_pclk`, not `pclk`** — this PHY exposes BOTH `max_pclk` and `pclk`.
   1 GHz is a plausible max_pclk; the datapath `pclk` the MAC consumes may differ. Confirm the
   observed clock is the real pclk and its rate matches the controller's configured PIPE rate
   (PLL locked to WRONG rate can toggle a clock the controller won't accept as PHY-ready).

## Disambiguation reads
- LTSSM state (confirm Quiet vs Active): `PEP_CTRL_CORE_DBG_SIG` @ 0x100000000440044,
  `SMLH_LTSSM_STATE` b[8:3].
- PHY-FW health: `PPW_CTRL_CPU_IND.CPU_RUN_ACK` @ 0x100000000488010.
- PLL truly locked (not skip-lock-forced): `EL_CM_CM_PLL0_STAT_1.PLL0_LOCK_INT` b15
  (macro0 0x1000000004830c0).

## Disambiguation additions (core-clock hypothesis)
- Read back `PLL_CLK_DIV_SEL_3` (~0x…9c1090): confirm `CFG_CLK_DIV=4` AND
  `CFG_CLK_OUT_MUX_SEL=1` stuck, and MNG PCIE_PLL reports locked.
- Probe the *controller* core_clk (not PHY pclk); Gen1 expect ~62.5–250 MHz, not 1 GHz.
- If 0x44 reads a fixed/zero pattern, PEP core still isolated/unclocked (recheck `PCIE_EP_ISO`
  clear @ ~0x…1c218 + reset release), not merely "in Detect".

## Sources
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.78–79 (core_clk derived from pipe_clk, same-phase,
  integer-multiple, never gated; Fig 2-10), p.93 (clock-gen: pipe_clk→divider→core_clk→native_core
  where LTSSM lives), p.101 (app_ltssm_enable → parks in Detect.Quiet),
  p.102–103 (cold-reset sequence; app_hold_phy_rst; Fig 2-25 Detect on core_clk), p.109 (reset
  waveform Detect.Quiet→Active on core_clk), **p.330 (LTSSM state transitions fail when the LTSSM
  clock is not running)**, p.651 (core_clk rate controlled by LTSSM via mac_phy_rate).
- FW proof (LTSSM gated on core_clk-detect): `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c`
  `core_clk_running_poll()` (reads `PEP_CTRL_AUX_INT_SIG.CORE_CLK_DETECT_IND`) called before
  `ltssm_en_set(true)` in `dwc_ep_ctl_linkup_impl()`; aborts linkup with -ETIMEDOUT if unset.
- FW: `fw/baremetal/fla_rom/src/el_pcie_minimal.c` `fla_rom_configure_pll_clk_div()`
  (MNG PCIE_PLL DIV=4 for Gen1 + CFG_CLK_OUT_MUX_SEL).
- KB: `linkup-debug-phy-status-regs.md`, `phy-pll-lock.md`, `detect-exit-refclk-requirement.md`,
  `prstn-psoc-fuse-masked.md`, `core-clk-derivation-rtl.md` (RTL-verified core_clk chain +
  correction of the MNG-PLL→core_clk overstatement).
