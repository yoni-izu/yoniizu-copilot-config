# DM BAR sizing — shadow (CS2) mask vs CDM BAR register, and the DBI_RO_WR_EN gate

- **Recorded:** 2026-10-04
- **Status:** Verified from doc (DM databook v6.42a-lca01) + EP databook + RTL (`ip.se.rtl`) + FW driver
- **Tags:** bar, bar mask, bar sizing, shadow register, dbi_cs2, cs2, dbi2, dbi_ro_wr_en,
  misc_control_1_off, bar0_type, prefetchable, aux dbi, dbi_ctrl, ext_lbc, lbc_dbi_ack,
  dm controller, dm_configure_bar, dwc_dm_ctl, ep controller, bar_size_cfg, iatu bar match

## Scope / Question

Is `dm_configure_bar()` in `ip.se.sw.fw` correct? Should BAR sizing go to the **shadow**
(CS2) register rather than the BAR register directly — and should the DM mirror the PCIe-EP
driver (`dwc_ep_ctl.c::bar_size_cfg()` + `dbi_ro_wr_en_set()`)?

## Findings

### 1. BAR mask IS a shadow register, reached only with CS2 — and the DM FW already does this

- The BAR mask register lives **at the same address as the BAR register** (BAR0 → 0x10,
  BAR1 → 0x14). It is **write-only**, invisible on the PCIe wire, and reachable **only** by
  asserting `dbi_cs` **and** `dbi_cs2`. "DBI2, CS2, dbi_cs2, DBI_CS2, Dbi2" all mean the same.
  (DM databook §3.6.1.1 Table 3-9 p.182, §3.6.8.1.7 p.227, §3.6.8.2.2 p.229)
- `mask = size - 1`; mask bit = 1 → that address bit is **masked** (not writable by the host,
  not used for address matching). BAR0 mask **bit[0] = BAR Enable**; writing 0 disables the BAR.
  For a 64-bit BAR the **BAR1 mask holds the upper 32 bits of the BAR0 mask**.
  (DM databook §3.6.8.1.8 Fig 3-55 p.226, §3.6.8.2.3 p.230)
- Memory BAR: bits [11:0] always masked, min claim 4 KB. On a CS2 write the HW **forces the
  low 8 bits of the write data to 0xFF** (Fig 3-56 note `#`), and "when the BAR mask value is
  less than that required for the BAR type, the controller automatically uses the minimum".
- ⇒ `dm_configure_bar()` step 2 (`aux_dbi_write(..., DWC_DM_DBI_ACCESS_SHADOW, ...)`) is
  **correct**. It is the DM equivalent of the EP's `PCIE_BAR_MASK_REG` (DBI2 window) write.

### 2. CS2 (shadow) writes are NOT gated by DBI_RO_WR_EN — the BAR[3:0] CDM write IS

Figure 3-56 (DM databook p.229; identical EP databook p.215) resolves this unambiguously —
the `*` footnote ("This is gated with the DBI_RO_WR_EN register field") sits on the
**`DBI write to 0x10` + `dbi_cs`** AND-gate feeding **BAR0[3:0] `RO(cs)`**
(`{PREFETCHABLE_0_0, BAR0_TYPE_0, MEM0_SPACE_DECODER_0}`). The two `WO(cs2)` mask-register
write paths (`DBI write & dbi_cs2`) carry **no** asterisk.

Table 3-10 (p.182) agrees: the **Dbi2** view is "Normally R/W", the BAR mask being the only
W-only exception; `DBI_RO_WR_EN` (`MISC_CONTROL_1_OFF`) is described solely for the **Dbi**
(cs2=0) view, "Some read-only registers can be made temporarily R/W".

Consequences:
- **DM FW bug:** `dm_configure_bar()` step 1 writes `BAR0_TYPE` (64-bit) and `PREFETCH` into
  BAR0[3:0] via **CDM** without ever setting `MISC_CONTROL_1_OFF.DBI_RO_WR_EN` → those bits
  are **silently dropped**; the BAR keeps its coreConsultant defaults. The in-code comment
  "CDM must be written FIRST so the controller knows the BAR type before the shadow mask is
  applied" is false. (`el_se_sv.c::el_se_sv_disable_flit_mode()` in the same repo does the
  correct read/set/restore dance via `DM_DBI_RO_WR_EN_OFF`.)
- For `role == RP` it is impossible by design: "For Downstream Ports (DSP) you should set
  DBI_RO_WR_EN to '0' … register fields marked RO do not become writable from the DBI"
  (p.183). The DM code also builds the RP value from **USP TYPE0** field macros while writing
  the **DSP TYPE1** offset (bit positions happen to match).
- **EP FW over-reach (mirror image):** `dwc_ep_ctl.c::dwc_ep_ctl_bar_size_init_impl()` wraps
  the mask writes in `dbi_ro_wr_en_set(true/false)` with the comment "The mask registers alias
  the read-only BAR registers, so the DBI read-only write gate gates them too." That comment
  is **wrong** per Fig 3-56 / Table 3-10; the wrapper is harmless but unnecessary. ⇒ do **not**
  copy it into the DM driver.

### 3. The BAR mask gates DBI writes to the BAR base address too → program mask BEFORE base

Fig 3-56: BAR0[31:4] (`RW`) per-bit write enable = `bar0_maski AND (PCIe Write OR (dbi_cs AND
DBI write))`. The mask therefore restricts **local DBI** writes to the BAR, not only wire
writes. Writing the base address *before* the mask (as `dm_configure_bar()` does) applies the
**reset-time** `BARn_MASK_0`, not the intended one. It works today only because the SE base
addresses are size-aligned (masked low bits are 0). Deterministic order is:
**BAR1 mask → BAR0 mask (carries the enable bit) → base address**.

Related: when `set_bar_base_addr == false` the DM code still issues a CDM write of `0`, which
clobbers BAR0[31:4] on the externally-RP-enumerated path (`el_se_step_set_bar_aperture`). The
CDM write should simply be skipped in that case.

### 4. No size validation in the DM driver (EP has it)

EP `bar_size_cfg()` rejects `size < 4 KB`, non-power-of-2, and `size > UINT32_MAX` on a 32-bit
BAR. DM `dm_configure_bar()` validates nothing: a non-power-of-2 `bar_size_gb` from DT yields a
garbage mask, and `!is_64bit` with `bar_size_gb >= 4` silently drops the upper mask word.

### 5. AUX indirect DBI path — RTL truth (how CS2 actually reaches the controller)

`DM_CTRL_AUX` register map (`DM_CTRL_AUX.yml`): `DBI_ADDR` 0x84, `DBI_WR_DATA` 0x88,
`DBI_CTRL` 0x90 (`DBI_CS` bit0, `DBI_CS2` bit1, `DBI_WR` [5:2], `DBI_IATU_ACCESS` bit6,
`DBI_IATU_REGION_TYPE` bit7, `DBI_IATU_REGION_NUM` [15:8]), `EXT_LBC` 0x94.

From `dm_macro/rtl/dm_pcie_iip_device_wrap.v`:
```
.dbi_addr_dbi_addr_data_r (dbi_addr)   .dbi_addr_dbi_addr_wr (gw_dbi_cs)
.dbi_ctrl_dbi_cs_data_r   (dbi_cs)     .dbi_ctrl_dbi_cs_wr   (lbc_dbi_ack | gw_dbi_cs)
                                        .dbi_ctrl_dbi_cs_wr_data  (gw_dbi_cs)
.dbi_ctrl_dbi_cs2_data_r  (dbi_cs2)    .dbi_ctrl_dbi_cs2_wr  (lbc_dbi_ack)
                                        .dbi_ctrl_dbi_cs2_wr_data (1'b0)
```
- ⇒ `DBI_CTRL.DBI_CS2` (bit 1) **directly drives the controller `dbi_cs2` pin**. The SW
  sequence addr → data → `DBI_CTRL{DBI_WR=0xF, DBI_CS2, DBI_CS}` genuinely performs a shadow
  write. ✅
- ⇒ The **trigger** is writing `DBI_CTRL.DBI_CS=1`; **completion** is HW auto-clearing
  `DBI_CS`/`DBI_CS2` on `lbc_dbi_ack`. So the poll must be on **`DBI_CTRL.DBI_CS == 0`**.
- `DBI_CTRL.DBI_IATU_ACCESS` (bit **6**) is a *different* field: it belongs to the APB
  gateway `dm_dbi_handler.v`, where `dbi_cs2 = dbi_access_pls & cfg_dbi_iatu_access` and the
  address switches to `dbi_iatu_addr = {15'b0, region_num, region_type, paddr[7:1], 1'b1}`.
  `dm_pcie_iip_device_wrap.v` leaves that gateway `dbi_cs2` **unconnected**
  (`//Not using the GW cs2. instead, it is being set by CSR`). Do **not** confuse bit 6 with
  bit 1.
- **`EXT_LBC` (0x94) is the external-LBC / ELBI path, not DBI.** `ext_lbc_cs_wr` is tied to
  `1'b1` with `wr_data = lbc_ext_cs` (a controller **input** to the wrapper) → `EXT_LBC.CS` is
  a HW-driven status mirror; SW writes to it are overwritten. `ACCESS_EVENT` is HW-set on
  `lbc_ext_cs_rise_pulse`. ⇒ `aux_dbi_write()`'s "step 4 trigger via EXT_LBC" and "step 5 poll
  EXT_LBC.CS" are **wrong register**: the poll reads an unrelated, normally-0 bit, exits on the
  first iteration, and the function always reports success.

### 6. BAR readback

The mask is unreadable, so verification must use the Element wrapper's
`DM_CTRL_AUX.CFG_INFO_BAR_0_{START,LIMIT}_{0,1}` (RO status of the resolved window) — which is
what `dwc_dm_dump_bar_cfg_info()` / `dwc_dm_ctl_get_bar_info()` (`se bar` shell) do. Empirical
check for finding #2: if `se bar` reports `64bit=yes prefetchable=yes` without any
`DBI_RO_WR_EN` unlock, those bits come from the coreConsultant config, not from FW.

### Open item

`BAR0_TYPE_0` / `PREFETCHABLE_0_0` / `BARn_MASK_0` coreConsultant values for the SE DM
instance are **not** in `ip.se.rtl` (Synopsys IP delivered separately) — unverified.

## Sources

- Doc: `DWC_pcie_ctl_dm_databook.pdf` (v6.42a-lca01) pp. 182–184 (Table 3-9/3-10, §3.6.1.3),
  226–231 (§3.6.8.1.5–3.6.8.2.4, Fig 3-55 / **Fig 3-56** / Fig 3-57), 588 (iATU BAR-match
  region size = BAR mask)
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.215 — identical Fig, same `*` placement
- RTL: `Elmnt-Internal/ip.se.rtl` → `dm_macro/rtl/dm_pcie_iip_device_wrap.v`
  (`dbi_ctrl_dbi_cs2_*`, `ext_lbc_cs_*`, `lbc_dbi_ack`),
  `dm_macro/rtl/dm_dbi_handler.v` (`cfg_dbi_iatu_access`),
  `dm_macro/misc/DM_MACRO_Micro_Architecture.md`
- FW: `ip.se.sw.fw` → `drivers/snps_dm_ctl/dwc_dm_ctl.c` (`dm_configure_bar`,
  `aux_dbi_write`, `dwc_dm_dump_bar_cfg_info`), `drivers/scale_engine/el_se.c`
  (`el_se_step_set_bar_aperture`, `el_se_step_configure_bar`),
  `drivers/scale_engine/el_se_sv.c` (`el_se_sv_disable_flit_mode` — correct RO_WR_EN pattern)
- FW: `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl.c`
  (`bar_size_cfg`, `dbi_ro_wr_en_set`, `dwc_ep_ctl_bar_size_init_impl`)
- Regs: `prototype/dm_ctrl_aux_regs.h` (DM_CTRL_AUX.yml), `prototype/dm_usp_dwc_pcie_usp_regs.h`
