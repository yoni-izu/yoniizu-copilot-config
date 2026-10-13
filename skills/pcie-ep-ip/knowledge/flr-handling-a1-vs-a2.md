# FLR handling — `pep_flr_handler` mode gating, WD auto-complete, and the A1 counter erratum

- **Recorded:** 2026-10-11
- **Status:** Verified from RTL (`ip.pcie.rtl` branches `iridium`, `iridium_rev2p4_be`, `main`) + generated CSR headers + FW
- **Tags:** flr, function level reset, pep_flr_handler, flr_ctrl, pf_handle_mode, handle mode, hw handling, fw handling, app_flr_done, cfg_flr_done, cfg_flr_active, flr watchdog, flr_wd, wd_val, wd_enable, flr autocompletion, flr_intr, flr_wd_intr, async_pep2psoc_flr_active, intr_spi_flr_pf, iip_aux_spi_bit_flr_pf, psoc reset on flr, el_reset_flr_reset_isr, prjirdmsw-8989, ippciertl-1423, pivsy-1592, errata, 100ms, 0x4c0000, ref_clk 50mhz, a1 vs a2

## Scope / Question
How an FLR is completed on the Element EP (A1/Iridium vs A2/Atlantic): who drives `app_flr_done`,
what the FLR watchdog does, how the HW-vs-FW handling mode is selected, and the A1 erratum where
the watchdog counter is not re-zeroed between FLRs.

## Findings

### 1. `app_flr_done` is a CSR level, and `FLR_CTRL.PF_DONE` is the only SW path to it
`pep_flr_handler.v` (all branches):
```verilog
assign app_flr_done = cfg_flr_done;                                  // pure level from the CSR bit
assign cfg_flr_done_set = cfg_flr_active_fall_pls | flr_wd_intr;
assign cfg_flr_done_ns  = cfg_flr_active_fall_pls ? 1'b0 : flr_wd_intr ? 1'b1 : cfg_flr_done;
```
- The DWC core holds the function in reset until `app_flr_done`.
- Only two setters of `cfg_flr_done`: a host/FW CSR write, or `flr_wd_intr` (HW auto-complete).
- HW **auto-clears** the bit on the `cfg_flr_active` falling edge — so after a real FLR the bit
  self-clears. With **no FLR in flight there is no falling edge**, so a FW write of `PF_DONE=1`
  would stick forever and instantly auto-complete the *next* FLR. **FW must always pulse 1→0.**

### 2. HW-handling and FW-handling are strictly mutually exclusive — `FLR_CTRL.PF_HANDLE_MODE`
```verilog
//HW handling - indication towards PSOC
assign async_pep2psoc_flr_active = (!cfg_flr_handle_mode) & cfg_flr_active;
//FW handling - indication towards uC
assign flr_intr                  = ( cfg_flr_handle_mode) & cfg_flr_active;
```
- `PF_HANDLE_MODE = 0` → PSOC gets the FLR-active notification (→ **PSOC/MuC is reset**);
  `flr_intr` is **permanently 0**, so the `INTR_SPI_CAUSE.FLR_PF` cause bit **never sets**.
- `PF_HANDLE_MODE = 1` → `flr_intr` fires; `async_pep2psoc_flr_active` is held low, so the
  **PSOC is NOT reset** and FW survives to service the interrupt.
- **`flr_wd_intr` is NOT gated by the mode** (`ref_flr_wd_intr = (cnt == threshold) & cnt_enable`),
  so `INTR_SPI_CAUSE.FLR_PF_WD` latches in **both** modes. `FLR_PF` fires only in mode 1.

### 3. Reset values (A1, `csr_pep_ctrl_aux_fields.v` + `pep_ctrl_aux_regs_attr.h`)
`FLR_CTRL` @ `PEP_CTRL_AUX + 0x0`, reset **0x2**:

| Bit | Field | Type | Reset | Note |
|-----|-------|------|-------|------|
| 0 | `PF_HANDLE_MODE` | RW | **0** | 0 = HW handling (PSOC reset), 1 = FW handling (uC interrupt) |
| 1 | `WD_ENABLE` | RW | **1** | FLR auto-complete watchdog enabled out of reset |
| 2 | `PF_DONE` | RW (+HW wr) | 0 | drives `app_flr_done` |
| 3 | `VF_DONE` | RW (+HW wr) | 0 | |

`FLR_WD.WD_VAL` @ `+0x4`, reset **0x4C0000** = 4,980,736 ticks.
**`ref_clk` is 50 MHz — stated in the module port comment** ("SoC refclk (50Mhz) - to count
100ms with a fixed clock freq") ⇒ 4,980,736 / 50 MHz = **99.6 ms ≈ 100 ms**. A 10 ms window
would be **500,000 = 0x7A120**. The conversion needs no guessing.

⇒ **Out of reset the EP is in HW-handling mode with a ~100 ms auto-complete watchdog.** The
watchdog is the *intended* completion mechanism in mode 0 — FW never completes an FLR unless
`PF_HANDLE_MODE` is explicitly set to 1.

### 4. The A1 erratum (PRJIRDMSW-8989 / RTL IPPCIERTL-1423 / symptom PIVSY-1592)
A1 (`iridium`, `iridium_rev2p4_be` — byte-identical):
```verilog
assign ref_flr_wd_cnt_set    = ref_app_flr_done;                                   // LEVEL
assign ref_flr_wd_cnt_enable = ref_cfg_flr_wd_enable & ref_cfg_flr_active & (!ref_app_flr_done);
```
`ref_app_flr_done` is `app_flr_done` through a plain 2-flop `el_cdc_sync` into the **50 MHz**
`ref_clk`. When HW itself completes the FLR, `cfg_flr_done` is asserted and then cleared again
on the `cfg_flr_active` fall a few **controller-clock** cycles later — far narrower than a
`ref_clk` period ⇒ **the synchronizer misses it ⇒ the counter is never re-zeroed.** The counter
only *freezes* between FLRs (`cnt_enable` requires `cfg_flr_active`), so it stays at the
threshold. The next FLR therefore satisfies `cnt == threshold` on its first enabled cycle and
**auto-completes essentially immediately** — the host is told the function is ready while the
device-side reset has not settled. That is the "half-reset function" symptom.

A2 (`main`) fixes it three ways:
```verilog
assign app_flr_done_rise_pls = app_flr_done & (!app_flr_done_r);   // edge, crossed via el_cdc_crdt
assign ref_flr_wd_cnt_set    = ref_app_flr_done_pls | (!ref_cfg_flr_active) | ref_cfg_flr_wd_cnt_clr;
assign ref_flr_wd_cnt_enable = ref_cfg_flr_wd_enable & ref_cfg_flr_active & (!ref_cfg_flr_wd_cnt_clr);
```
1. rising edge crossed as a **pulse** (`el_cdc_crdt`) instead of a level — with an explicit RTL
   comment that the level "can be missed by a flop synchronizer into the slower ref_clk";
2. `| (!ref_cfg_flr_active)` — counter is held at 0 whenever no FLR is in progress
   ("guarantees every FLR starts from 0");
3. a new SW escape hatch `cfg_flr_wd_cnt_clr` (`FLR_CTRL` bit, level, held while 1).
**None of these exist on A1** — A1 must work around it in FW.

### 5. The viable A1 FW workaround
Because `ref_flr_wd_cnt_set = ref_app_flr_done` is a **level** with no `cfg_flr_active`
qualifier, a FW-driven `PF_DONE` 1→0 pulse zeroes the counter **at any time, FLR in flight or
not**. An MMIO write + read-back + write holds the bit high for hundreds of ns–µs, i.e. many
50 MHz `ref_clk` periods — comfortably captured by the synchronizer (unlike the few-cycle HW
pulse). So a pulse issued at driver init is a sound erratum workaround: it restores the full
~100 ms window for the next FLR. It does **not** make FW the FLR completer.

### 6. A1 signal destinations
- `flr_intr` → `aux_cfg_flr_pf_intr` → `intr_spi_flr_pf_pulse_intr` →
  `PEP_CTRL_AUX.INTERRUPT_INTR_SPI_CAUSE.FLR_PF` (sticky W1C) → PIW IIP aux **SPI** tree → PLIC → MuC.
  FW bits: `IIP_AUX_SPI_BIT_FLR_PF` / `_FLR_PF_WD` (`el_piw_spi_bits.h`).
- `flr_wd_intr` → `aux_cfg_flr_pf_wd_intr` → `...INTR_SPI_CAUSE.FLR_PF_WD` (ungated by mode).
- `async_pep2psoc_flr_active` → PSOC reset manager → NVIC **231** `"flr_reset"`
  (`proj_infra/boards/arm/a1/el_arm.dts`) → `el_reset_flr_reset_isr()`
  (`ip/psoc/drivers/el_reset/el_reset_isr.c`): capture cause → `el_device_state_notify_reset()`
  → MSI-X doorbell → `el_reset_halt_early_engines()` → `el_reset_pre_reset(EL_RESET_TYPE_FLR, die)`
  per die → `el_reset_cm7_halt()`. **So FW-side FLR cleanup already exists in mode 0** — it runs
  in the PSOC reset ISR, not in the EP driver.
- PEP CSRs are PoR+warm only (not in the FLR domain) ⇒ `INTR_SPI_CAUSE` FLR bits and the WD
  counter **survive the FLR**, and a post-FLR reboot can observe/clear them.

### 7. Timing consequence — FW cannot ACK an FLR after a reboot
In mode 0 the FLR resets the MuC. A staged reboot (FLA.ROM → FLA.RT → PRIMEBOOT → MANAGEMENT) is
orders of magnitude longer than the 100 ms watchdog, so a "reboot, then ACK at driver init"
scheme can **never** complete the FLR itself — the watchdog always wins. Such an init-time ACK is
only an erratum workaround (counter re-zero), never an FLR completion path. Completing an FLR in
FW requires `PF_HANDLE_MODE = 1`, which keeps the MuC alive — and then FW owns, within ~100 ms,
all the device-side quiesce/reset that the PSOC reset previously did for free.

## Sources
- RTL: `rtl/pcie/pep_macro/rtl/pep_flr_handler.v` — branches `origin/iridium`,
  `origin/iridium_rev2p4_be` (identical) vs `main` (A2 fix)
- RTL: `rtl/pcie/pep_macro/rtl/csr_pep_ctrl_aux_fields.v` — `csr_field_flr_ctrl_*`
  (`ASYNC_RST_VAL`), `csr_field_flr_wd_wd_val` (`32'h4C0000`)
- RTL: `rtl/pcie/pep_macro/rtl/pcie_iip_device_wrap.v:910,912,2233,2234` — signal routing
- Header: `fw/build_artifacts/include/v2/0_0_0_0p0_0000/prototype/pep_ctrl_aux_regs_attr.h`
  (`PEP_CTRL_AUX_FLR_CTRL__RST 0x2`, `PEP_CTRL_AUX_FLR_WD__RST 0x4c0000`)
- FW: `fw/ip/psoc/drivers/el_reset/el_reset_isr.c` (`el_reset_flr_reset_isr`),
  `fw/proj_infra/boards/arm/a1/el_arm.dts:461-465` (NVIC 231 `"flr_reset"`)
- FW: `fw/ip/pcie/ep/include/el_piw_spi_bits.h:129-132`
- Related: `iridium-prj` knowledge `reset-domains.md` (FLR resets PSOC/MuC, not PEP/PIW)
- Context: PR `Elmnt-Internal/ip.pcie.sw.fw.ep#430`, branch `dev/vgomonovych/PIVSY-1592`
