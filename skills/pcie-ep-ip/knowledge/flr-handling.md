# FLR (Function Level Reset) handling — HW/FW contract, handle mode, watchdog, erratum

- **Recorded:** 2026-10-11
- **Status:** Verified from RTL (`ip.pcie.rtl` @ `origin/iridium`) + uArch doc + FW
- **Tags:** flr, function level reset, pep_flr_handler, flr_ctrl, pf_done, vf_done, pf_handle_mode,
  flr_wd, wd_val, app_flr_done, cfg_flr_active, cfg_flr_done, async_pep2psoc_flr_rst_n,
  flr_intr, flr_wd_intr, intr_spi_flr_pf, intr_spi_flr_pf_wd, iip_aux spi, psoc reset on flr,
  prjirdmsw-8989, ippciertl-1423, pivsy-1592, flr watchdog counter, cdc miss, el_cdc_sync,
  el_reset_flr_reset_isr, el_reset_type_flr, handle mode 0 hw, handle mode 1 fw

## Scope / Question
How is PCIe FLR handled on Iridium (A1) — who completes it, what the `FLR_CTRL` CSR does,
why the FW cannot simply "reboot and ACK on init", and what the PIVSY-1592 erratum actually is.

## Findings

### The HW block
`pep_macro/rtl/pep_flr_handler.v`, instantiated **once per PF** (`gen_pf_flr_handler`,
`SNPS_CX_NFUNC`) and **once per VF** (`gen_vf_flr_handler`) inside `pcie_iip_device_wrap.v`.
Runs on `muxd_aux_clk_g`, with the watchdog counter in the `ref_clk` domain
(**ref_clk = 50 MHz**, stated in the module port comment).

Decisive assignments (verbatim, `origin/iridium`):

```verilog
assign async_pep2psoc_flr_active = (!cfg_flr_handle_mode) & cfg_flr_active;  // HW mode
assign flr_intr                  = ( cfg_flr_handle_mode) & cfg_flr_active;  // FW mode
assign ref_flr_wd_intr  = (ref_flr_wd_cnt == ref_cfg_flr_wd_threshold) & ref_flr_wd_cnt_enable;
assign app_flr_done     = cfg_flr_done;                       // NOT gated by handle mode
assign cfg_flr_done_set = cfg_flr_active_fall_pls | flr_wd_intr;
assign cfg_flr_done_ns  = cfg_flr_active_fall_pls ? 1'b0 : flr_wd_intr ? 1'b1 : cfg_flr_done;
assign ref_flr_wd_cnt_set    = ref_app_flr_done;              // el_counter set_value = '0
assign ref_flr_wd_cnt_enable = ref_cfg_flr_wd_enable & ref_cfg_flr_active & (!ref_app_flr_done);
```

And in `pcie_iip_device_wrap.v`:
```verilog
el_async_buf ... .async_in(~async_pep2psoc_pf_flr_active), .async_out(async_pep2psoc_flr_rst_n)
.intr_spi_flr_pf_pulse_intr    (aux_cfg_flr_pf_intr)      // gated by handle_mode
.intr_spi_flr_pf_wd_pulse_intr (aux_cfg_flr_pf_wd_intr)   // NOT gated by handle_mode
```

### `PEP_CTRL_AUX.FLR_CTRL` (offset 0x0) — reset values from `csr_pep_ctrl_aux_fields.v`
| Field | Bit | Type | **ASYNC_RST_VAL** | Notes |
|-------|----|------|---------------|-------|
| `PF_HANDLE_MODE` | 0 | RW | **0 = HW handling** | SW-only (`hw_wr=0`) |
| `WD_ENABLE` | 1 | RW | **1 = WD enabled** | SW-only |
| `PF_DONE` | 2 | RW | 0 | **also HW-written** (`flr_ctrl_pf_done_wr`) |
| `VF_DONE` | 3 | RW | 0 | **also HW-written** |

`PEP_CTRL_AUX.FLR_WD` (0x4) `WD_VAL` **ASYNC_RST_VAL = 0x4C0000** = 4,980,736 ticks
@ 50 MHz = **99.6 ms** (the RTL calls it "100 ms"). 10 ms would be **0x7A120** (500,000).

### Two mutually exclusive modes
- **`PF_HANDLE_MODE = 0` (HW / "automatic") — the A1 reset default, and FW never changes it.**
  `async_pep2psoc_flr_rst_n` is driven **low as a level for the entire duration of
  `cfg_flr_active`** → the PSOC/MuC is reset (matches the SoC-spec reset-domain table: FLR
  resets PSOC MuC/PuC, but **not** PEP and **not** PIW). `flr_intr` is **permanently 0**, so
  the `IIP_AUX` SPI bit `FLR_PF` **never fires in this mode**.
- **`PF_HANDLE_MODE = 1` (FW / "manual")** — no PSOC reset; `flr_intr` raises the `FLR_PF`
  SPI cause so live FW completes the FLR by writing `PF_DONE`.
- **VF FLR handle mode is hardcoded to `1'b1` (always manual)** per the uArch doc
  (`PCIE_IIP_DEVICE_WRAP_Micro_Architecture.md` §5.2) — VFs never notify PSOC, so every VF
  FLR needs FW to drive `FLR_CTRL.VF_DONE` or it falls to the VF watchdog.

### Why "reboot, then ACK at init" cannot complete an FLR (HW mode)
The MuC is **held in reset while `cfg_flr_active` is asserted**, and `cfg_flr_active` only
drops once `app_flr_done` (= `cfg_flr_done` = `FLR_CTRL.PF_DONE`) is driven. The only agent
that can write that CSR is the MuC. The loop is broken **exclusively by the watchdog**:
WD expiry → `cfg_flr_done_set`/`_ns`=1 → `app_flr_done`=1 → controller drops `cfg_flr_active`
→ PSOC reset releases → MuC boots. So in HW mode:
- the WD completing the FLR is **by design**, not a FW omission;
- the ~100 ms WD window also **defines the PSOC reset pulse width**;
- by the time any FW init code runs, `cfg_flr_active` is already 0 and `PF_DONE` has already
  been auto-cleared by `cfg_flr_active_fall_pls`.
- PCIe requires the function to be ready 100 ms after FLR; a full MuC reboot (FLA.ROM →
  FLA.RT → PRIMEBOOT → MNG) is orders of magnitude slower, so FW can never beat the WD.

An init-time `PF_DONE` pulse is therefore **a watchdog-counter reload, not an FLR completion**.

### The erratum (PRJIRDMSW-8989 / RTL IPPCIERTL-1423, failure PIVSY-1592)
`ref_flr_wd_cnt_set = ref_app_flr_done` loads the counter with 0 — but `app_flr_done` crosses
into `ref_clk` through a **plain 2-FF `el_cdc_sync` with no pulse stretch / no `SRC_SMP`**
(contrast: the `cfg_flr_active` crossing right above it *does* use `.SRC_SMP(1)`, and
`flr_wd_intr` uses `el_cdc_crdt`). `app_flr_done` is high only from WD-expiry until the
controller drops `cfg_flr_active` — a handful of `aux_clk` cycles, typically **shorter than one
50 MHz `ref_clk` period (20 ns)** → the synchronizer misses it → **the counter is never reloaded
and stays parked at the threshold**. On the next FLR, `ref_flr_wd_cnt == threshold` is already
true on the first enabled cycle, so `flr_wd_intr` fires **immediately**: the second FLR
auto-completes with a ~0 ms window, which also collapses the PSOC reset pulse to nothing — the
device is not actually reset. That is the PIVSY-1592 symptom.

**FW workaround that works:** a `PF_DONE` 0→1→0 sequence with a **read-back between the set and
the clear**. The MMIO read round-trip from the MuC to the PEP AUX CSR is far longer than two
`ref_clk` periods, so the high phase is guaranteed to survive the CDC and reload the counter.
Crucially, A1's `ref_flr_wd_cnt_set = ref_app_flr_done` carries **no `cfg_flr_active` qualifier**
while `cnt_enable` **does** — so between FLRs the counter merely *freezes*, and a FW pulse issued
at any quiet moment still zeroes it. That is what makes an init-time pulse a valid workaround.

### Fixed in A2 / `main` — three independent changes, none of which exist on A1
`ip.pcie.rtl` @ `origin/main` `pep_flr_handler.v` (the RTL comment states the A1 bug verbatim:
*"app_flr_done is only a few clk cycles wide (cfg_flr_done is cleared on the cfg_flr_active fall,
which the controller drives right after app_flr_done)"*):
1. **Edge-detect + pulse-safe crossing** — `app_flr_done_rise_pls = app_flr_done & !app_flr_done_r`
   (`el_smp`) handed to **`el_cdc_crdt`** instead of synchronizing the level with `el_cdc_sync`.
2. **Idle re-zero** — `assign ref_flr_wd_cnt_set = ref_app_flr_done_pls | (!ref_cfg_flr_active) |
   ref_cfg_flr_wd_cnt_clr;` with the comment *"Clear on: FLR done, no FLR in progress (guarantees
   every FLR starts from 0), SW clear"*.
3. **New SW escape hatch** — `cfg_flr_wd_cnt_clr` input (*"SW clear of the WD counter (level, held
   while 1)"*), which also gates `cnt_enable`.

A1 has **none** of these, which is why the FW-side pulse is the only available remedy there.

### Cause-bit routing (FW-relevant)
`FLR_PF` / `FLR_PF_WD` (and `FLR_VF` / `FLR_VF_WD`) are **`IIP_AUX` SPI pulse interrupts**, i.e.
`PEP_CTRL_AUX.INTERRUPT_INTR_SPI_CAUSE` — not SEI. `PEP_CTRL_AUX` lives in the PoR+warm domain
and is **not** reset by FLR, so a `FLR_PF_WD` raised while the MuC was in reset stays **latched**
and is still set when FW boots; FW must W1C it at init or the IRQ-tree walker trips on a stale
cause. In `INTR_SPI_MASK`, **1 = masked**, so unmasking means clearing bits (the existing
`CII_ACCESS` unmask in `dwc_ep_ctl.c` is the precedent to follow).

### FW side already present (Iridium)
- `ip/psoc/drivers/el_reset/el_reset_isr.c::el_reset_flr_reset_isr()` — gated on DT
  `DT_INST_IRQ_HAS_NAME(0, flr_reset)`, wired on A1 (`proj_infra/boards/arm/a1/el_arm.dts`,
  irq 231). It captures cause `EL_RESET_TYPE_FLR`, notifies KMD (`el_device_state_notify_reset`
  + MSI-X doorbell), halts early engines, runs `el_reset_pre_reset(EL_RESET_TYPE_FLR, die)` for
  every die, then `el_reset_cm7_halt()`. **So a pre-reset FW window on FLR already exists.**
- `el_pcie_ep_reset.c::el_pcie_ep_reset_callback()` is registered via
  `el_reset_register_callback()` and receives FLR, but **early-returns for anything that is not
  `EL_RESET_TYPE_WARM`**. The EP driver masks PSOC out of **WARM only**
  (`el_reset_config_comp_reset_mask(EL_RESET_TYPE_WARM, EL_RESET_COMP_PSOC, …)`), never FLR —
  so PSOC really is reset by FLR.
- Do **not** ack `PF_DONE` from the FLR pre-reset callback: that drops `cfg_flr_active`, which
  releases `async_pep2psoc_flr_rst_n` and cancels the very device reset FLR is supposed to
  perform. The pre-reset callback is for quiesce/cleanup only.
- `el_piw_config` (`ip/pcie/ep/include/el_piw.h`) holds **no** reference to the EP controller
  device, so a PIW IRQ worker cannot reach `dwc_ep_ctl_*` helpers by passing its own `dev`
  (`DEV_PEP_AUX_BASE()` would reinterpret `struct el_piw_config *` as
  `struct dwc_ep_ctl_config *`). `dwc_ep_ctl_config` has `parent_ep_device`;
  `el_pcie_ep_config` has `ep_ctl_device` (accessor `el_pcie_ep_get_ep_ctl_device()`), but
  neither is reachable from the PIW node without a new DT phandle.

## Sources
- RTL: `ip.pcie.rtl` @ `origin/iridium` — `pep_macro/rtl/pep_flr_handler.v`
- RTL (A2 fix comparison): `ip.pcie.rtl` @ `origin/main` — `pep_macro/rtl/pep_flr_handler.v`
- RTL: `ip.pcie.rtl` @ `origin/iridium` — `pep_macro/rtl/pcie_iip_device_wrap.v`
  (lines ~874–945 FLR generate loops + `el_async_buf`; ~2233 SPI intr wiring)
- RTL: `ip.pcie.rtl` @ `origin/iridium` — `pep_macro/rtl/csr_pep_ctrl_aux_fields.v`
  (lines ~925–1005, `csr_field_flr_ctrl_*`, `csr_field_flr_wd_wd_val`)
- Doc: `ip.pcie.rtl` @ `origin/iridium` —
  `pep_macro/misc/PCIE_IIP_DEVICE_WRAP_Micro_Architecture.md` §5 "pep_flr_handler (PF and VF)"
- Spec: `~/docs/Iridium/Iridium SoC spec.docx` Table 4-2 "Reset Domains"
  (see `iridium-prj/knowledge/reset-domains.md`)
- FW: `fw/ip/psoc/drivers/el_reset/el_reset_isr.c`, `el_reset_cause.c`
- FW: `fw/ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_reset.c`
- FW: `fw/ip/pcie/ep/include/el_piw_spi_bits.h` (`enum iip_aux_spi_bit`)
- PR under review: `ip.pcie.sw.fw.ep` `dev/vgomonovych/PIVSY-1592` (commit `cc6f69d`)

## FLR ordering: controller resets the function AFTER app_flr_pf_done

Source: `DWC_pcie_ctl_ep_databook.pdf` (v6.42a-lca01) §22.6 "FLR Signal Interface
Timing", p.632; §22.3 "Operational Notes", p.629.

Databook sequence (PF x):
1. Host writes the FLR bit; controller asserts `cfg_flr_pf_active[x]`.
2. Application terminates TLP **transmission**. The RX interface must NOT be
   halted - the controller keeps delivering pre-FLR requests/completions.
3. Application asserts `app_flr_pf_done[x]` once it has "stopped/reset the PFx
   application logic".
4. **"When the controller detects 1 on `app_flr_pf_done[x]`, it initiates FLR of
   PFx."** Clears the Completion LUT, clears Transaction Pending, resets the
   function. Duration `4 * CX_MAX_TAG * core_clk_period` at Gen1 frequency.
5. Controller de-asserts `cfg_flr_pf_active[x]`.

Consequences for firmware:
- Any function/config-space programming done BEFORE asserting `app_flr_pf_done`
  is **wiped by step 4**. Re-initialization must come strictly after the ack,
  and after the controller's own reset window.
- §22.3 explicitly sanctions this: the 100 ms FLR budget covers quiesce+ack
  only; "the subsequent function-specific initialization sequence by your
  application logic might require additional time... your application logic for
  that function must return a configuration request retry status (CRS)".
  Once the function answers with a non-CRS completion it may not return CRS
  again until the next reset.
- `app_flr_pf_done` is therefore a **teardown-complete** signal, not a
  readiness signal. After a core reboot it is trivially satisfied at boot start.
- Software must quiesce the function (clear BME) before initiating FLR; the
  function must not issue requests during FLR or the completions are dropped.

### A1 observability gap
`pep_ctrl_aux` exposes only `FLR_CTRL` (PF_HANDLE_MODE / WD_ENABLE / PF_DONE /
VF_DONE), `FLR_WD.WD_VAL`, and the pulse-latched `INTERRUPT_INTR_SPI_CAUSE.
FLR_PF[_WD]`. There is **no readable level status for `cfg_flr_pf_active`**, so
FW cannot poll for step 5. Waiting out step 4 requires a bounded delay sized
from `4 * CX_MAX_TAG` Gen1 core clocks.

### CRS control in FW
`dwc_ep_ctl_set_downstream_termination()` is the CRS/DRS control
(`dwc_ep_ctl_api.h:178` - "Set endpoint configuration readiness (CRS / DRS
control)"). Asserted during bring-up (`el_pcie_ep.c:546`) and released in the
management app's `main()` immediately before boot-done, so CRS already covers
the whole FW boot window.
