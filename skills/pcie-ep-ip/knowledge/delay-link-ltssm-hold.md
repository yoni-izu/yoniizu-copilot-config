# "Delay link" — `LINK_DN_CTRL.DELAY_LINK_TRAINING` / `DELAY_LINK_DN_RST` (PEP wrapper HW LTSSM hold)

- **Recorded:** 2026-10-04
- **Status:** Verified from DWC EP databook + generated CSR headers + FW + RTL (`ip.pcie.rtl` branch `iridium`)
- **Outcome:** filed **IPPCIESW-678** (High, MS8.3) — dual-consumer race on `INTR_SPI_CAUSE`
- **Tags:** delay link, delay_link_training, delay_link_dn_rst, link_dn_ctrl, app_ltssm_enable,
  app_ltssm_en, ltssm hold, hot reset, link down, smlh_req_rst_n, link_req_rst_n, core_rst_n,
  flushing mode, reset mode, dbi reprogram, detect.quiet, ext_hot_rst_ind_en, ext_hot_rst_ind_cnt,
  pep_hot_rst_handler, handle_early_hot_reset_warning, handle_post_hot_reset, reset default 0x202,
  dm_ctrl scale up 0x200, intr_spi_mask 0x3f, el_irq_tree, el_irq_mon, el_shared_irq,
  iip_core_spi_bindings, piw0_spi_iip_core, uIRQ, w1c double consumer, STRUCT_SECTION_ITERABLE

## Scope / Question
What is the "delay link" feature, and is it enabled (by HW reset default or by FW)?

## Candidate Firmware Update (2026-10-04)

The "FW never writes LINK_DN_CTRL" statements below describe the pre-change
snapshot used for the original investigation. EP commit `2ea505b` adds
`delay-link-dn-rst` / `delay-link-training` DT integer properties (0 or 1), with
defaults 0/1 preserving the old policy. `dwc_ep_ctl_linkup_impl()` now programs
only those bits, fences the write, then enables LTSSM. A host MMIO-spy test of
the actual function verified all four combinations and unchanged unrelated bits.

Project-infra commit `8fb44b6d` sets both properties to 0 for `ep_ctl0` and
`ep_ctl1` on the A1 ARM/ASIC board. Starting at 0x202, the expected live value
after link-up configuration is 0x200 (stretch count 64 preserved). Other boards
retain the binding defaults. `bringup_dump` shows both bits as DISABLED, with
raw field values retained; one-bit mask/disable fields account for active-low
polarity and multi-bit values remain numeric.

This is a configurable hold-policy mitigation, not the fix for the double-owner
W1C cause race. Hardware auto-retraining and end-to-end hot-reset recovery still
require on-device qualification; no reproduction is claimed by the host test.

## The underlying Synopsys requirement

The DWC EP controller has **no register** for this. The databook says the application must drive
`app_ltssm_enable = 0` **immediately / combinatorially** at two distinct moments:

| Goal | Trigger the app must react to | Databook |
|------|-------------------------------|----------|
| **Delay the controller/bridge reset** — keep the AXI bridge in *Flushing Mode* so it keeps accepting+terminating subordinate requests and FW can still read status out over DBI | `smlh_req_rst_n` → 0 (early warning that hot reset / link-down reset is imminent) | EP databook p.109, Fig. 2-30 |
| **Delay link re-establishment** — park the LTSSM in Detect.Quiet so FW can reprogram **non-sticky** registers over DBI before training restarts | `core_rst_n` → 0 (equivalently right after `link_req_rst_n`) | EP databook p.102 Fig. 2-25 (cold), p.109 Fig. 2-31 (hot), p.110 |

Databook caveat (p.102/p.110): `app_ltssm_enable` must be de-asserted **while still in Detect**.
If it is de-asserted (or the controller is reset) out of Detect, the LTSSM jumps straight back to
Detect — a transition not defined in the PCIe Base Spec that **may cause a PIPE protocol
violation**. "Immediately (combinatorially)" is far too fast for a FW ISR → this *must* be HW.

## Element's HW implementation

`PEP_CTRL_CORE.LINK_DN_CTRL` @ **PEP_CORE base + 0x40** (`pep_ctrl_regs.h`):

| Bit(s) | Field | Mask | Meaning |
|--------|-------|------|---------|
| 0 | `DELAY_LINK_DN_RST` | `0x1` | HW clears `APP_LTSSM_EN` on `smlh_req_rst_n` **falling edge** → postpones Reset Mode |
| 1 | `DELAY_LINK_TRAINING` | `0x2` | HW clears `APP_LTSSM_EN` on first `core_rst_n` de-assert → parks LTSSM in Detect.Quiet |
| 2 | `EXT_HOT_RST_IND_EN` | `0x4` | **Unrelated to LTSSM** — enables an *extended* (stretched) hot-reset indication to PSOC |
| 9:3 | `EXT_HOT_RST_IND_CNT` | `0x3f8` | 7-bit stretch count for that indication |

RTL (`pep_macro/rtl/pep_hot_rst_handler.v`):

```verilog
assign smlh_req_rst_not_fall_pls      = smlh_req_rst_not_r & (!smlh_req_rst_not);
assign smlh_req_rst_not_ltssm_en_clr  = cfg_delay_link_dn_rst & smlh_req_rst_not_fall_pls;
assign core_rst_n_ltssm_en_clr        = cfg_delay_link_training & (~core_rst_n_r)
                                        & async_first_core_rst_n_deassert_buf;

assign cfg_app_ltssm_en_clr = smlh_req_rst_not_ltssm_en_clr | core_rst_n_ltssm_en_clr;
assign app_ltssm_en         = cfg_app_ltssm_en
                              & (!smlh_req_rst_not_ltssm_en_clr)
                              & (!core_rst_n_ltssm_en_clr);
```

### The two terms are INDEPENDENT and fire at DIFFERENT times

`cfg_app_ltssm_en_clr` is the **OR** of two separately-gated terms — a very common misreading is
to assume `DELAY_LINK_DN_RST=0` means "nothing parks the LTSSM". It does not.

| | `DELAY_LINK_DN_RST` | `DELAY_LINK_TRAINING` |
|---|---|---|
| Trigger | `smlh_req_rst_not` **falling edge** (early warning, *before* the controller resets) | `core_rst_n` **asserted** (the actual reset, later in the same event) |
| Shape | **1-cycle pulse** | **level**, held for the whole reset |
| Purpose | hold off *Reset Mode* — bridge keeps flushing / DBI still readable | hold off *link training* — park in Detect.Quiet to reprogram non-sticky regs |
| Iridium default | 0 → never fires | 1 → fires on every core reset |

So on a hot reset with the shipped `0x202`: `APP_LTSSM_EN` **survives** the `smlh_req_rst_n` phase
and is then **cleared** by the `core_rst_n` phase. It does *not* stay set.

### Truth table

| `DELAY_LINK_DN_RST` | `DELAY_LINK_TRAINING` | `APP_LTSSM_EN` after hot reset / link down | FW needed? |
|---|---|---|---|
| 0 | 0 | **stays 1** — `cfg_app_ltssm_en_clr` is permanently 0, CSR never written, and the CSR is not in the `core_rst_n` domain | **no — HW auto-retrains** |
| 1 | 0 | cleared at `smlh_req_rst_n`↓ | yes (early-warning handler) |
| **0** | **1** | cleared at `core_rst_n` — **Iridium/A1 default `0x202`** | **yes** (post-reset handler) |
| 1 | 1 | cleared twice | yes, both handlers |

### ⚠ What asserts `smlh_req_rst_not` — NOT just SBR

Databook §2.4.3 (p.106), verbatim: *"A DSP can hot reset a USP by sending two consecutive TS1
ordered sets with the hot reset bit asserted, **or by pulsing the `app_init_rst` input**...
**Alternatively, during normal operation, the link might fail and go down. After this link-down
event, the controller requests the DWC_pcie_clkrst.v module to hot-reset the controller. There is
no difference in the handling of a link-down reset or a hot reset**."*
Fig. 2-29 annotates `smlh_req_rst_not` as *"Early warning... informing it that **the link has gone
down**"*; the note says *"Maintain full control of `app_ltssm_enable` during Hot Reset **(and link
down events)**"*; the reset-generation diagram (p.105) labels the Hot Reset source as *"From
link_down event **or** remote partner's TS hot-reset"*.

Four triggers, all converging on the same `smlh`↓ → `link_req`↓ → `core_rst_n` sequence:
1. Remote TS-protocol Hot Reset (RP writes SBR)
2. Local `app_init_rst` pulse
3. **Link failure during normal operation** — BER, EI-exit failure, Recovery timeout, LTSSM → Detect
4. RAS-DP uncorrectable error with `AUTO_LINK_DOWN_EN=1` in `RASDP_ERROR_MODE_EN_OFF` (p.678)

**(3) is unconditional — no config bit gates it.** Composed with `DELAY_LINK_TRAINING=1`, this means
**a transient BER-induced link blip takes the device permanently off the bus unless FW services
the SPI cause.** If the DBI reprogramming window between `core_rst_n` and retraining is not
actually needed, clearing `DELAY_LINK_TRAINING` makes link recovery autonomous.

### Why a CSR clear is needed at all (not just the mask)

1. **Combinatorial mask** on the controller-facing `app_ltssm_en` — meets the databook's
   "immediately (combinatorially)" rule. Only covers the duration of the clear term, which for
   `DELAY_LINK_DN_RST` is a single cycle.
2. **Write-back clear of the CSR** — this is what makes the hold *stick*
   (`pcie_iip_device_wrap.v`):
   ```verilog
   .app_ltssm_en_val_data_r (cfg_app_ltssm_en    ),
   .app_ltssm_en_val_wr     (cfg_app_ltssm_en_clr),
   .app_ltssm_en_val_wr_data('0                  ),
   ```
   Needed because **`APP_LTSSM_EN` (PEP_CORE + 0x30, `__RST 0x0`) is NOT in the `core_rst_n`
   domain** — PEP CSRs are PoR+warm only (see `rasdes-counter-reset-persistence.md`). A core
   reset would otherwise leave the enable set. ⇒ **FW must re-write `APP_LTSSM_EN = 1`**;
   it never self-releases.

### Supporting signals (verified, not guessed)

- Instantiated as `.clk(core_clk) .rst_n(core_rst_n)` — so `core_rst_n_r` is a plain
  reset-detect flop (`el_smp`, `data_in=1'b1`, reset by `core_rst_n`); `~core_rst_n_r` ==
  "core is in / just leaving reset".
- `async_first_core_rst_n_deassert_buf` = **sticky "core_rst_n has de-asserted at least once
  since power-up"** latch: `el_smp` in the `pcil_clk` domain, reset by
  `pcil_pep_power_up_rst_n`, `data_in=1'b1`, `data_in_en` = CDC-synced `core_rst_n_r`. It
  suppresses the park on the **very first** core reset out of PoR. Practically invisible
  (`APP_LTSSM_EN` resets to 0 anyway and FW sets it during normal bring-up), but it is why the
  cold-boot path never sees a park.

`EXT_HOT_RST_IND_*` is a separate datapath (7-bit `el_counter`, set to 0 on `link_req_rst_n` fall,
counts to `cfg_ext_hot_rst_ind_cnt`):
`async_pep2psoc_hot_rst_n = link_req_rst_not & ~(cfg_ext_hot_rst_ind_en & cnt_inc);`
i.e. it stretches the hot-reset pulse toward PSOC so a short pulse isn't missed.

## Is it enabled? — YES for DELAY_LINK_TRAINING, NO for DELAY_LINK_DN_RST

**Reset value `LINK_DN_CTRL = 0x202`** (`pep_ctrl_regs_attr.h::PEP_CTRL_PEP_CTRL_CORE_LINK_DN_CTRL__RST`).
Identical in **every** regmap variant shipped in `build_artifacts/include` (v1 `3_0_11_3p0_1389`,
v2 `0_8_4`, `0_9_3`, `1_0_5`, `1_0_7`, prototype) — so this is not a variant-dependent default.

| Field | Reset | State |
|-------|-------|-------|
| `DELAY_LINK_DN_RST` | 0 | **disabled** |
| `DELAY_LINK_TRAINING` | **1** | **ENABLED by HW default** |
| `EXT_HOT_RST_IND_EN` | 0 | disabled |
| `EXT_HOT_RST_IND_CNT` | 0x40 (64) | preloaded but inert (EN=0) |

**FW never writes `LINK_DN_CTRL`.** Whole-tree grep finds only reads/dumps:
- `dwc_ep_ctl.c` — two `sys_read32` + `FIELD_GET` (the two ISR handlers below)
- `dwc_ep_ctl_reg_dump.c` — `DEBUG_READABLE_REG` / `BRINGUP_REG` listing
- `dwc_ep_ctl_bringup_fields.c` — field-name table for the dump

⇒ **effective runtime value = the reset value 0x202.** Nothing (DTS, Kconfig, FLA.ROM, SV)
changes it. Both bits are *readable policy knobs* for FW, not FW-programmed ones.

Scale-up contrast: `DM_CTRL_DM_CTRL_CORE_LINK_DN_CTRL` (offset 0x2c) resets to **0x200** —
same `CNT=64` but `DELAY_LINK_TRAINING = 0`. The EP deliberately enables it; the SU/DM port
does not.

## Consequences in the FW (`dwc_ep_ctl.c`, SPI interrupt handler)

`dwc_ep_ctl_handle_spi_intr()` dispatches on `PEP_CTRL_CORE.INTR_SPI_CAUSE`:

- `SMLH_REQ_RST_N_FALL` → `handle_early_hot_reset_warning()`
  Reads `DELAY_LINK_DN_RST`; it is **0**, so the function **early-returns**. The
  `set_config_ready(false)` (CRS hold-off) and the `/* TODO: Stop outbound data transmission */`
  are both dead code today. Benign — the link is down anyway so host config can't reach the EP.
  Its `ltssm_en_set(dev, true)` is **not** a bug: per databook Fig. 2-30, *"the bridge enters
  Reset Mode when it has finished Flushing Mode **and app_ltssm_enable=1**"* — FW re-arms the
  enable to release the bridge once it has finished its DBI reads.
- `LINK_REQ_RST_N_FALL` → logged only, no action.
- `LINK_REQ_RST_N_RISE` → `handle_post_hot_reset()`
  Reads `DELAY_LINK_TRAINING`; it is **1**, so it calls `ltssm_en_set(dev, true)` to un-park the
  LTSSM, then `set_config_ready(true)`. **Not redundant — it is the only re-assert path.**
  If this never runs, the LTSSM stays in Detect.Quiet **forever** (the clear is latched in a CSR
  that nothing else writes); only a power cycle recovers the device.

### Delivery is polling-only by construction (NOT a mask bug)

`PEP_CTRL_CORE.INTERRUPT.INTR_SPI_MASK` @0xac has **`__RST = 0x3f`** — all six causes **masked**
(`1` = masked: the ISR does `mask_val | BIT` under the comment *"Mask the interrupt before
processing"*). **Nothing in FW ever clears it**, and that is *consistent*, not a bug:

- The `snps,ep-ctl` DTS nodes (`a1_hsi.dtsi` L876 `ep_ctl0`, L1277 `ep_ctl1`) declare **no
  `interrupts` / `interrupt-names` property at all** ⇒ `DT_INST_IRQ_HAS_NAME(inst, intr_pcie_spi)`
  and `DT_INST_IRQ_HAS_IDX(inst, 0)` are false ⇒ the `IRQ_CONNECT` is **compiled out**, and
  `config_irq` / `irq_set_enabled` fall back to `NULL`. `dwc_ep_ctl_set_irq_enabled()` early-returns.
  There is no HW IRQ path to unmask *for*.
- Delivery is therefore 100 % via `CONFIG_EL_IRQ_MON` polling, which reads `INTR_SPI_CAUSE`
  directly. `INTR_SPI_CAUSE.__RST = 0x0` and **the cause latches regardless of the mask**, so the
  all-masked default is harmless on the polling path.

**Neither `el_irq_tree` nor the EP driver ever writes a MASK register.** (Don't confuse frameworks:
`el_shared_irq` *does* unmask — `mask_reg &= ~(1u << idx)` on callback registration — but the PCIe
EP is an `el,irq-tree-node`, not an `el,shared-irq` node. `el_shared_irq` serves mbx, pll, mem_ecc,
spi_dma, axi_drain.) `struct el_irq_tree_cause_desc` has no MASK-offset field at all; its
`ignore_mask` is a *software* read-time filter (`tree_read32(...) & ~desc->ignore_mask`).

### ⚠⚠ REAL BUG: two consumers race on one W1C cause register → IPPCIESW-678

The IRQ tree **covers this exact block with zero handlers bound**, yet still clears the cause.

`a1_hsi.dtsi` L1196-1202 wires a tree node straight at the PEP core SPI cause:
```dts
piw0_spi_iip_core: piw0-spi-iip-core {
    compatible = "el,irq-tree-node";
    device = <&ep_ctl0>;
    device-reg-name = "pep_ctrl_core_regs";
    cause-desc = <&iip_core_spi>;
};
```
but `el_piw_spi_tree.c` L286-287 binds nothing to any of its six bits:
```c
static const struct el_irq_tree_cause_bit_binding iip_core_spi_bindings[IIP_CORE_SPI_BIT_COUNT] = {
	0};
```
and `el_irq_tree.c`'s walker, for an asserted bit with **no handler and no aggregator child**, does
`LOG_ERR("uIRQ: %s/%s")` and then `w1c_and_retry()` — **which writes the cause value back, clearing it.**

Meanwhile `dwc_ep_ctl.c` registers `EL_IRQ_MON_HANDLER_REGISTER(..., dwc_ep_ctl_handle_spi_intr,
<pep_core_base> + INTR_SPI_CAUSE)` — **the same address**. Both are `STRUCT_SECTION_ITERABLE`
entries, so **dispatch order is link order**, not semantically controlled.
`dwc_ep_ctl_handle_spi_intr()` returns early on `intr_cause == 0`.

⇒ If the tree is dispatched first it **steals** `LINK_REQ_RST_N_RISE`, `handle_post_hot_reset()`
never runs, and the LTSSM stays parked — a dead link until power cycle.

Live on `mng` / `el_arm_a1_asic` + the palladium overlays: `a1_piw_irq_trees_enable_all.dtsi` is
included by 10 board overlays across `mng` and `primeboot`.

**Diagnostic:** grep a post-hot-reset log for **`uIRQ: IIP_CORE/`**. If that string appears, the
EP handler is being starved. (Not yet reproduced on HW — found by inspection.)

**Lesson (general):** in this codebase a W1C cause register must have exactly **one** owner.
`EL_IRQ_MON_HANDLER_REGISTER` on an address that is also reachable from an `el,irq-tree-node`
is always a bug, and an all-NULL `*_bindings[]` next to a wired-up tree node is the smell.

**This makes `handle_post_hot_reset()` load-bearing**: with the default 0x202, HW holds
`APP_LTSSM_EN` low after every hot reset / link-down reset, and the link only comes back because
that ISR re-asserts it. If the SPI interrupt is masked, unrouted, or the handler is skipped, the
EP stays in Detect.Quiet forever after a hot reset and looks like a dead link.
(See also `detect-quiet-stuck-debug.md` — `app_ltssm_enable=0` is listed there as a cause of a
permanent Detect.Quiet; this is the mechanism that can produce it.)

Note the asymmetry recorded in `app-ctrl-crs-drs.md`: `set_config_ready(false)` sits *below* the
`delay_link_dn` guard while `set_config_ready(true)` sits *outside* the `delay_link_training`
guard — with the shipped defaults that means readiness is re-asserted without ever having been
de-asserted.

## Sources
- Doc: Synopsys **DWC PCIe EP Controller Databook** v6.42a-lca01 — p.102 (Fig. 2-25 delay link
  training after cold reset), p.108–109 (Fig. 2-29/2-30 AXI bridge flushing + delaying the
  controller hot reset), p.109 (Fig. 2-31 delay link training after hot reset), p.110 (Detect-only
  caveat / PIPE protocol violation).
- CSR: `build_artifacts/include/**/prototype/pep_ctrl_regs.h` (offset 0x40, masks),
  `pep_ctrl_regs_attr.h` (`__RST 0x202`); `dm_ctrl_regs{,_attr}.h` (offset 0x2c, `__RST 0x200`).
- FW: `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl.c`
  (`handle_early_hot_reset_warning`, `handle_post_hot_reset`, `ltssm_en_set`,
  `dwc_ep_ctl_handle_spi_intr`), `dwc_ep_ctl_bringup_fields.c`, `dwc_ep_ctl_reg_dump.c`.
- RTL: `pep_macro/rtl/pep_hot_rst_handler.v` (whole module — clear terms, `el_smp` reset
  detectors, `el_counter` pulse stretcher) + `pep_macro/rtl/pcie_iip_device_wrap.v` (L1020
  instantiation with `.clk(core_clk) .rst_n(core_rst_n)`, L2783-2785 CSR write-back clear,
  L2788-2789 `link_dn_ctrl_delay_*_data_r` sources).
  **Verified on `Elmnt-Internal/ip.pcie.rtl` branch `iridium`** — `pep_hot_rst_handler.v` there is
  byte-identical to the deployed A2 tree (`/home/yoniizu/projects/iridium/four/rtl/pcie`, which
  is Gen7 `ep16sx4` = Atlantic, non-git). `iridium_rev2p4_be` differs only by an added
  `// @SuppressProblem -type mix_reset_non_reset_signal` lint comment. The `0x202` reset value
  is from the Iridium v1/v2 regmaps.
- Databook: §2.4.3 p.106 (hot reset vs link-down equivalence, `app_init_rst`), Fig. 2-29 p.107,
  Fig. 2-30/2-31 p.108, reset-generation diagram p.105, RAS-DP `AUTO_LINK_DOWN_EN` p.678.
- IRQ plumbing: `infra/drivers/el_irq_tree/el_irq_tree.c` (walker: handler → child → `uIRQ`
  LOG_ERR; `w1c_and_retry()`), `infra/include/el_irq_tree.h` (`el_irq_tree_cause_desc` =
  `cause_off` + software `ignore_mask`, **no MASK offset**), `infra/include/el_irq_mon.h`
  (`STRUCT_SECTION_ITERABLE` ⇒ link-order dispatch),
  `infra/drivers/el_shared_irq/el_shared_irq.c` (the framework that *does* unmask — not used here),
  `ip/pcie/ep/drivers/el_piw/el_piw_spi_tree.c` L264-294 (`iip_core_spi_bits_const`,
  all-NULL `iip_core_spi_bindings`), `ip/pcie/ep/include/el_piw_spi_bits.h` L116-123,
  `proj_infra/soc/dts/a1/a1_hsi.dtsi` L876/L1196-1226/L1277,
  `proj_infra/soc/dts/a1/a1_piw_irq_trees_enable_all.dtsi` (included by 10 board overlays).
