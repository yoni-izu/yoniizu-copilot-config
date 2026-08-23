# PCIe Clocking Scheme (Common-Clock / SRNS / SRIS) — FW config path

**Status:** Verified from **SNPS PHY documentation** (source of truth) + FW + SDK.
Recorded 2026-08-17.

## SOURCE OF TRUTH — SNPS PHY/PCS documentation

**PCS Wrapper Databook v1.80 §5.9 "Rx Clocking Mode Settings", Table 5-6 (p172)** — the
definitive per-lane signal settings. The PHY supports CC / SRNS / SRIS; SRNS/SRIS support
100 MHz or 25 MHz refclk, CC supports 100 MHz only:

| Clocking Mode | `pipe_laneX_cmn_refclk_mode` | `pipe_laneX_sris_mode_en` |
|---------------|:---:|:---:|
| SRIS | 0 | 1 |
| SRNS | 0 | 0 |
| CC   | 1 | 0 |

**PCS Databook §5.8 "Enabling SRIS, SRNS, and Common Clock" (p171)** — PLL context + SSC:
- SRIS: `pipe_laneX_cmn_refclk_mode=0`, `pipe_laneX_sris_mode_en=1`,
  `phyN_pll0/1_cmn_refclk_mode=0` (select PLL SRIS context); `phyN_pll0/1_ssc_en_i`=0 if the
  spread is on the *input* refclk, =1 if the PHY generates SSC on the MPLL clocks.
- SRNS: same as SRIS but `sris_mode_en=0`, `ssc_en_i=0`.
- CC:   `phyN_pll0/1_cmn_refclk_mode=1` (p69: "For CC mode tie it to 1, for SRIS/SRNS tie
  this signal to 0 using programmable register").

### Why both methods exist (design rationale)
The scheme is a **physical board property** (refclk routing), fixed at design time. "Fixed" vs
"negotiated" only choose **who drives the PHY's CDR-mode PIPE inputs** (`ppw_wrap.v` mux):
- **Fixed** = FW/CSR drives them → PIPE/PHY still fully sees the correct mode from bit 0; the
  controller's dynamic negotiation output is just disconnected from the PHY CDR. Deterministic;
  correct for a **soldered, co-designed platform part** (our EP) where the host + board topology
  are known. Avoids the Recovery/`rxX_standby` re-latch machinery and the conservative 25 µs
  SRIS start-lock.
- **Negotiated** = the controller's LTSSM drives them, adapting to the link partner. Exists for
  **interoperable / unknown-topology devices** (add-in cards, hot-plug, generic EPs) that must
  discover the host's clocking at runtime; PCIe mandates the Common-/Slot-Clock exchange + SRIS
  negotiation for that. "Start at SRIS" (widest CDR) locks before the topology is known, then
  tightens.
- **Alignment is a FW duty in fixed mode:** forcing the PHY CDR is only half — the controller
  still advertises a mode on the wire (Common-Clock-Config, Slot-Clock-Config, TS1 SRIS). FW MUST
  set the controller side to match (EP-side: clear Slot-Clock-Config for non-CC, `APP_SRIS_MODE=1`
  for SRIS) or the link partner and our PHY disagree → CDR mismatch. Fixed does NOT self-correct.

**Fixed vs Negotiated (PCS §5.9, p172)** — the two application methods, per docs:
- **Fixed at POR**: pick one mode and hold it. If mode is **fixed CC and NOT negotiated**,
  set `upcs_pipe_config[13]=1` to force the uPCS to apply the setting to the PHY without
  waiting for `pipe_rxX_standby`.
- **Negotiated** (PCIe Base + PIPE 6.2.1 `SRISEnable`): inputs must **start at SRIS** and may
  step SRIS→SRNS→CC during Config LTSSM. `pipe_laneX_sris_mode_en` is driven by the
  controller during link training; `pipe_laneX_cmn_refclk_mode` changes when the associated
  controller register updates. **A live mode change only latches into the PHY when the
  controller drives the link to Recovery and toggles `pipe_rxX_standby` (wait for
  `pipe_rxX_standby_status`).** (`upcs_pipe_config[13]=0` default: sampled only on standby.)

**CDR lock times (PHY Databook Table 4-6, p68)** — `rxN_cdr_ssc_mode_i`: CC(0)=0.5 µs,
SRIS(1)=25 µs, SRNS(2)=2 µs. **PHY config settings** (PHY Databook §4.9, p65): use the
"PCIe-CommonClock / PCIe-SRIS / PCIe-SRNS Mode" rows of the *DWC SerDes Configuration
Settings Reference Manual* — this is what the SDK `f_refclk` (COMMON/SRNS/SRIS × 100M/25M) picks.

## Placement (shared PHY driver — EP + scale-up)

The `dwc_pcie6_phy` driver serves **both** the PCIe EP and the scale-up (SE/C2C) links —
same PHY IP. Clocking mode is therefore a **per-PHY-instance** property that must live on the
**PHY devicetree node** (`el,pcie-phy.yaml`/`snps,pcie6-phy.yaml`), NOT on the EP/PPW node.
Motivating case: eventually enable **SRNS on the scale-up PHYs while the EP stays
Common-Clock**. The wrapper CSRs that carry these signals differ per integration (EP → PPW_CTRL;
SE → its own wrapper), so the apply must be driven per-instance through the PHY node's own
register handles, not a single EP-level write.

## SE (scale-up) wrapper = DMP_CTRL — deferred to IPPCIESW-628 (feature is EP-only)

The SE C2C ports use the **same `snps,pcie6-phy` PHY** but a **different wrapper**: `DMP_CTRL`
(owned by the `el_dmp_ctrl` driver, compat `el,dmp-ctrl`, `el_phy_wrap_ctrl` API), NOT PPW.
Integrating clocking on SE is **deferred to IPPCIESW-628** (decouple PPW from the vendor PHY
driver); the clocking-scheme feature (IPPCIESW-616) is **EP-only** for now. Recorded findings for
that follow-up:
- **`se_phy` nodes have NO `ppw_ctrl_regs`** (only `phy_ctrl_regs`/`mcu_iccm`/`mcu_dccm`). The EP
  driver `dwc_pcie6_phy` currently **requires** `ppw_ctrl_regs`, so it cannot serve SE as-is —
  hence the ticket. (A quick `DT_INST_REG_ADDR_BY_NAME_OR(...,0)` was tried and reverted; the
  proper fix is decoupling, not a workaround.)
- **`DMP_CTRL_PHY_CTRL` is a SINGLE register** holding ALL clocking fields (unlike PPW which
  splits `LANE_SRIS_MODE_EN` into `PHY_CTRL_1`). Same field names/semantics:
  `USE_CTRL_COMMON_REFCLK_MODE`(bit7)/`USE_CTRL_SRIS_MODE`(bit8), `PHY_PLL0/1_CMN_REFCLK_MODE`
  (2 bits each — SE = **1 macro**), `LANE_CMN_REFCLK_MODE`(4 bits)/`LANE_SRIS_MODE_EN`(4 bits —
  SE port = **x4**). Field widths encode the SE geometry, so lane mask = `MSK>>SFT` = 0xF.
- Design for IPPCIESW-628: the scheme stays the **`se_phy` DTS property** (source of truth); the
  SE bring-up (`el_se_phyfw.c` `el_se_phyfw_start_all_cpus`, **before releasing the PHY CPU**)
  would read it via `el_pcie_phy_get_clk_config(el_c2c_get_phy_dev(c2c))` and apply on the DMP via
  a new `el_phy_wrap_ctrl` op `clk_apply(dev, die_id, scheme, method)` (die-aware).
- **Consistency:** EP's two PHYs share one PPW_CTRL → checked at **runtime** in
  `el_pcie_ep_linkup_cfg` (each EP PHY must match PHY 0). A global BUILD_ASSERT is WRONG because
  it would force EP and SE to the same scheme. SE ports are independent (per-port DMP) — no
  cross-port check needed.

## RTL-VERIFIED wrapper mux — `USE_CTRL_*` meaning (ip.pcie.rtl `ppw_macro/rtl/ppw_wrap.v`)

```verilog
assign lane_sris_mode_en    = cfg_use_ctrl_sris_mode          ? {PIPE_NL{mac_phy_sris_enable}}      : cfg_lane_sris_mode_en;
assign lane_cmn_refclk_mode = cfg_use_ctrl_common_refclk_mode ? {PIPE_NL{mac_phy_commonclock_enable}} : cfg_lane_cmn_refclk_mode;
```

- `USE_CTRL_COMMON_REFCLK_MODE=1` / `USE_CTRL_SRIS_MODE=1` → the **controller (PIPE-negotiated)**
  signals `mac_phy_commonclock_enable` / `mac_phy_sris_enable` drive the PHY. **= Negotiated method.**
- `USE_CTRL_*=0` → the **CSR fields** `cfg_lane_cmn_refclk_mode` / `cfg_lane_sris_mode_en` drive
  the PHY (FW-forced). **= Fixed method.**
- **This is the OPPOSITE of the SV helper**, which set `USE_CTRL_*` on the APP/forced path.
  SV is wrong; RTL is truth. FLA.ROM agrees with RTL (force-mode clears `USE_CTRL_*=0`).
- **`PHY_PLL0/1_CMN_REFCLK_MODE` and `PHY_PLL*_SSC_EN` CSR fields — WIRED on Iridium, UNWIRED on Atlantic-main.**
  - **Iridium** (`iridium` + `iridium_rev2p4_be` branches, `ppw_wrap.v`): these DO reach the PHY macros —
    `phy0_pll0_cmn_refclk_mode = cfg_phy_pll0_cmn_refclk_mode[1:0]`, `phy1_... = [3:2]` (one 4-bit CSR
    field packs BOTH macros, 2 bits each); `phy0/1_pllX_ssc_en_i = cfg_phy_pllX_ssc_en[0/1]`. Per PHY
    doc p69 the per-macro value is **1 for CC, 0 for SRIS/SRNS** ⇒ ROM's `PHY_PLL*_CMN_REFCLK_MODE=0x5`
    (= `01` per macro = CC) **is meaningful on Iridium and must be written**.
  - **Atlantic main**: same fields are `//TODO - remove` / not wired — there the ROM PLL writes are no-ops.
  - Only the per-lane `cfg_lane_cmn_refclk_mode[7:0]` (lanes 0–3=phy0, 4–7=phy1) + `cfg_lane_sris_mode_en[7:0]`
    go through the USE_CTRL mux above; the PLL fields bypass the mux and are always CSR-driven.
  - `common_refclk_mode` CSR bit is "not in use" on both branches (ppw_wrap comment).
  - **One PPW_CTRL per die packs both PHY macros** (LANE fields split 0–3/4–7; PLL field split [1:0]/[3:2])
    → a per-PHY-instance apply must RMW only its own slice, and the two EP PHY nodes must agree
    (build-assert). This is why clocking sits on the PHY node but PPW should eventually split out.

### Reset default decode (Iridium FW headers `ppw_ctrl_regs_attr.h`)
`PPW_CTRL_PHY_CTRL__RST = 0x185` → bits{0,7,8}: `COMMON_REFCLK_MODE=1`,
`USE_CTRL_COMMON_REFCLK_MODE=1`, `USE_CTRL_SRIS_MODE=1`; `PHY_PLL0/1_CMN_REFCLK_MODE=0`,
`LANE_CMN_REFCLK_MODE=0`. `PPW_CTRL_PHY_CTRL_1__RST=0xFF` → `LANE_SRIS_MODE_EN=0xFF`.
So at reset the **controller/PIPE path is selected** (USE_CTRL_*=1) → mode follows negotiation,
which defaults to Common-Clock (Slot-Clock-Config resets to 1). The `LANE_*` CSR reset values
only matter once `USE_CTRL_*` is cleared. **⇒ "CC is the HW default" is TRUE.**

### Force-apply (`upcs_pipe_config[13]`) — NOT needed for our feature
Doc §5.9 recommends `upcs_pipe_config[13]=1` only to push a mode change to an **already-running**
PHY without a `pipe_rxX_standby` cycle. We apply the **fixed** scheme at bring-up **before the
PHY leaves reset/standby**, so the PHY samples `cfg_lane_*` naturally at POR — no force-apply
needed. (User's conclusion confirmed; precise reason = bring-up-time apply, not "CC default".)

## Corrected FIXED-method register sequence (doc Table 5-6 + Iridium RTL)
Per EP PHY macro, on `PPW_CTRL` (one per die — RMW own slice), before LTSSM / before PHY out of reset:
1. `USE_CTRL_COMMON_REFCLK_MODE=0`, `USE_CTRL_SRIS_MODE=0` (select CSR/FW-forced path).
2. Per Table 5-6 + PLL context (Iridium wires the PLL field; per-macro value 1=CC / 0=SR*):
   - **CC**:   `LANE_CMN_REFCLK_MODE=0xFF`, `LANE_SRIS_MODE_EN=0x00`, `PHY_PLL0_CMN_REFCLK_MODE=0x5`, `PHY_PLL1_CMN_REFCLK_MODE=0x5`
   - **SRNS**: `LANE_CMN_REFCLK_MODE=0x00`, `LANE_SRIS_MODE_EN=0x00`, `PHY_PLL0/1_CMN_REFCLK_MODE=0x0`
   - **SRIS**: `LANE_CMN_REFCLK_MODE=0x00`, `LANE_SRIS_MODE_EN=0xFF`, `PHY_PLL0/1_CMN_REFCLK_MODE=0x0`
   (0xFF/0x5 are all-8-lanes / both-macros; a strictly per-macro apply writes only its lane byte-half
   and its 2-bit PLL slice.)
3. `PHY_PLL*_SSC_EN`: leave at reset for CC/SRNS; for SRIS set per SSC source (0 = spread on input
   refclk, 1 = PHY generates SSC on MPLL). Follow-up detail; ROM leaves it at reset.
4. EP-side only (not in shared PHY driver): for non-CC clear Slot-Clock-Config (DBI RO-write
   dance); for SRIS set `PEP_CTRL_AUX.APP_SRIS_MODE=1` so the controller advertises SRIS to the
   host (TS1 SRIS bit / `DO_DESKEW_FOR_SRIS`).

## SV code vs ROM vs RTL/doc — discrepancy record (why SV must NOT be copied)

`el_pcie_ep_sv_clk_mode_set()` (`drivers/el_pcie_ep/el_pcie_ep_sv.c`, SV-only) diverges from the
RTL-verified truth and from FLA.ROM `fla_rom_configure_clk_mode()`:

| Aspect | SV helper (WRONG) | FLA.ROM (correct) | RTL/doc truth |
|--------|-------------------|-------------------|---------------|
| `USE_CTRL_*` on **APP/forced** | **SETS** `USE_CTRL_COMMON_REFCLK_MODE`+`USE_CTRL_SRIS_MODE`=1 | keeps `USE_CTRL_*`=0 in force mode | `USE_CTRL_*=1` hands the mode to the **controller/PIPE** (negotiated); forced path needs `USE_CTRL_*=0` (`ppw_wrap.v` mux) |
| `COMMON_REFCLK_MODE` bit | toggles it (CC:set / else clear) on APP path | never uses it | CSR `common_refclk_mode` is **"not in use"** in RTL — dead bit |
| Method model | `PIPE` vs `APP` enum, but APP wired backwards | eFuse `PCIE_CLK_MODE_SEL`: force vs explicit-signaling | Fixed = `USE_CTRL_*=0`+drive CSR lanes; Negotiated = `USE_CTRL_*=1`+let controller drive |
| Mode source | function args (shell only) | GPIO straps + eFuse override | (FW feature: DTS property) |
| Slot-Clock-Config clear | **not done** | cleared for SRIS/SRNS via DBI RO-write dance | required so host sees non-CC |
| `APP_SRIS_MODE` | set only when `mode==SRIS && method==APP` | set=1 for SRIS force, =0 otherwise | controller-side SRIS advertisement (EP) |
| PLL fields | writes `PHY_PLL0/1_CMN_REFCLK_MODE=0x5` (CC) via `DWC_EP_CTL_PLL_CMN_REFCLK_CC_MODE`) | same 0x5 for CC | correct **on Iridium** (wired); no-op on Atlantic-main |
| Scope | grabs `devs[0]` globally, not per-`dev` | per-fixed-instance | per-PHY-instance needed (EP + scale-up) |

**Net:** SV's only reusable pieces are the field/mask names and the CC PLL value `0x5`. Its
`USE_CTRL_*` polarity, `COMMON_REFCLK_MODE` toggling, missing Slot-Clock-Config clear, and global
`devs[0]` access are all wrong for a production apply. Build the new path from the ROM sequence +
Table 5-6 + the `ppw_wrap.v` mux, not from SV.

## Summary

Three reference-clock architectures are supported end-to-end:

| Scheme | Meaning | SDK `clk_arch` | Wrapper regs |
|--------|---------|----------------|--------------|
| **Common Clock (CC)** | Both link ends share one refclk. **Reset/default.** | `DWC_PCIE6_X4_NS_CLOCK_ARCH_COMMON` (0) | PLL cmn-refclk mode = CC, LANE_CMN = ALL, SRIS off |
| **SRNS** | Separate Reference **No** Spread — independent refclks, no SSC. | `..._CLOCK_ARCH_SRNS` (2) | PLL cmn-refclk mode = 0 (not-common), SRIS off |
| **SRIS** | Separate Reference **Independent** Spread — independent refclks, each with own SSC. | `..._CLOCK_ARCH_SRIS` (1) | PLL cmn-refclk = 0, `LANE_SRIS_MODE_EN`, `APP_SRIS_MODE` |

> Note the SDK enum ordering is COMMON=0, **SRIS=1, SRNS=2** (not COMMON/SRNS/SRIS).
> FW's own `enum el_pcie_ep_clk_mode_e` uses CC=0, SRIS=1, SRNS=2 as well
> (`include/el_pcie_ep_sv.h`).

## Two independent layers

1. **SNPS PHY SDK** (`drivers/snps_pcie6_phy/sdk/.../sdk_powerup.c`) — runs **inside the PHY
   FW blob** on the PHY's own CPU. Its `ptr_cfg->clk_arch` + `ptr_cfg->f_refclk`
   (`dwc_pcie6_x4_ns_powerup_cfg`) select PLL context + ref range. `f_refclk` encodes both
   arch and freq: `COMMON/SRNS/SRIS_F_REFCLK_{100M,25M}` (enum `dwc_pcie6_x4_ns_f_ref_clk`).
   FW on the MuC does **not** call these directly — they are compiled into the prebuilt PHY
   image the MuC streams into PHY ICCM/DCCM.

2. **PHY-wrapper / EP-controller CSRs** (FW-writable from the MuC) — the FW-side knob:
   - `PPW_CTRL_PHY_CTRL`: `USE_CTRL_COMMON_REFCLK_MODE`, `USE_CTRL_SRIS_MODE`,
     `COMMON_REFCLK_MODE`, `PHY_PLL0/1_CMN_REFCLK_MODE`, `LANE_CMN_REFCLK_MODE`
   - `PPW_CTRL_PHY_CTRL_1`: `LANE_SRIS_MODE_EN`
   - `PEP_CTRL_AUX_APP_CTRL`: `APP_SRIS_MODE`
   `USE_CTRL_*` selects **APP-forced** vs **PIPE-negotiated** application of the mode
   (`enum el_pcie_ep_clk_method_e`: `PIPE`=0 default, `APP`=1).

## Authoritative production sequence — FLA.ROM (trust this, NOT the SV code)

The real bring-up lives in `baremetal/fla_rom/src/el_pcie_minimal.c`
(`fla_rom_configure_clk_mode`, step `SEQ_CONFIGURE_CLK_MODE`, runs **before**
`SEQ_ENABLE_LTSSM`). Mode source = **GPIO straps** (`GPIO_PAD_STRAP` SRIS/SRNS bits),
with an **eFuse `PCIE_CLK_MODE_SEL` override** that selects one of two application methods:

**Force mode (eFuse=0)** — FW writes the PHY registers directly. Always first:
`USE_CTRL_COMMON_REFCLK_MODE=0`, `USE_CTRL_SRIS_MODE=0`. Then per mode:
- **SRIS**:  `APP_SRIS_MODE=1`; clear Slot-Clock-Config.
- **SRNS**:  `APP_SRIS_MODE=0`, `LANE_SRIS_MODE_EN=0`; clear Slot-Clock-Config.
- **Common**: `APP_SRIS_MODE=0`, `PHY_PLL0_CMN_REFCLK_MODE=0x5`, `PHY_PLL1_CMN_REFCLK_MODE=0x5`,
  `LANE_CMN_REFCLK_MODE=0xFF`, `LANE_SRIS_MODE_EN=0`. (Slot-Clock-Config left at default=1.)

**Explicit-signaling mode (eFuse=1)** — rely on PIPE/link-training negotiation, minimal setup:
- **Common**: only `PHY_PLL0/1_CMN_REFCLK_MODE=0x5`.
- **SRIS/SRNS**: no PHY config; clear Slot-Clock-Config.

**Slot-Clock-Config bit** (`LINK_CONTROL_LINK_STATUS_REG.SLOT_CLK_CONFIG`) must be **cleared
for SRIS/SRNS** (Common leaves the default=1). Clearing requires the DBI RO-write dance:
set `MISC_CONTROL_1_OFF.DBI_RO_WR_EN`, `dsb`, W0 the slot bit, `dsb`, clear DBI_RO_WR_EN, `dsb`.
`PLL_CMN_REFCLK_MODE_COMMON_CLK = 0x5`, `LANE_MODE_ALL_ENABLED = 0xFF`.

> The FLA.ROM `force_mode` (eFuse) is the real "APP-forced vs PIPE-negotiated" knob and it
> keeps `USE_CTRL_*=0` in BOTH branches. **The SV helper `el_pcie_ep_sv_clk_mode_set()`
> disagrees** — it sets `USE_CTRL_*` when method==APP and toggles `COMMON_REFCLK_MODE`. Treat
> the SV file as UNVERIFIED; the ROM sequence + register-reference docs are the source of truth.

### SV helper (do NOT copy blindly)
`el_pcie_ep_sv_clk_mode_set(method, mode)` in `drivers/el_pcie_ep/el_pcie_ep_sv.c` is SV-only
(`CMakeLists_sv.txt`/`CONFIG_EL_PCIE_EP_SV`, shell-only, grabs `devs[0]`). The **runtime Zephyr
EP driver** has no clocking apply path yet — that is the gap this task fills (DTS-selected scheme).
Must be applied **before LTSSM enable**.

## Where clocking would hook into production bring-up

Init chain: `el_pcie_ep_init` → `el_pcie_ep_bring_up` (`el_pcie_ep.c`) →
`el_pcie_ep_do_linkup` → `el_pcie_ep_linkup_cfg` (set link speed, VDM drop) →
`el_pcie_ep_linkup_enable` (LTSSM). The clocking-scheme apply must land in
`el_pcie_ep_linkup_cfg` (or bring_up before do_linkup), i.e. before LTSSM.

## DTS / topology facts

- EP PHY nodes: `pcie_phy0`/`pcie_phy1` (`compatible = "snps,pcie6-phy", "el,pcie-phy"`),
  `proj_infra/soc/dts/a1_hsi.dtsi` ~line 876. Two PHY wrappers (EP_PPW_0/1) per EP; **one
  PPW_CTRL per die** (in EP_PPW_0) whose CPU_RUN_REQ broadcasts — both PHY nodes point
  `ppw_ctrl_regs` at that single block. ⇒ **one clocking scheme per EP link**, so an
  EP-controller-node property is a better fit than a per-PHY-node property.
- Bindings: `dts/bindings/el,pcie-phy.yaml` (vendor-agnostic PHY class),
  `snps,pcie6-phy.yaml` (includes it), `dts/bindings/pcie_ep/el,pcie-ep.yaml` (EP driver).
- PHY driver config `struct dwc_pcie6_phy_config` (`include/dwc_pcie6_phy.h`) currently:
  `phy_ctrl_base`, `ppw_ctrl_base`, `ccm_regs[]` — no clocking field yet.

## Host-side spec note (from `snps_ep_ctl` tests)

Clocking negotiation surfaces in config space: Link Control `Common_Clock_Config` bit,
`Slot_Clock_Config`, and TS1 SRIS bit; Port Force `DO_DESKEW_FOR_SRIS`. CC/SRNS/SRIS map:
CC → Common_Clock_Config=1; SRNS → CC=0, SRIS-bit=0; SRIS → CC=0, SRIS-bit=1.
