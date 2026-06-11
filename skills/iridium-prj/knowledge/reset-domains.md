# Reset Domains (which components reset on each reset type)

- **Recorded:** 2026-06-11
- **Status:** Verified from spec
- **Tags:** reset, reset domains, reset types, warm reset, por, wdt, flr, hot, hard, soft, debug, platform, piw, pcie ep, pcie wrapper, psoc, muc, puc, boot flow

## Scope / Question
Which hardware components are reset under each reset type in Iridium. Source of truth for FW
reset-handling design (e.g. PCIe EP warm-reset callbacks, which reset paths keep the MuC FW
alive vs. reboot it, and whether PIW/PEP are in a given reset's domain).

## Findings

Reset domains per **`Iridium SoC spec.docx`** Table 4-2 "Reset Domains" (section
"Architectural Design → Reset Domains"). `v` = component is reset by that reset type. Verified
2026-06-11 against the SoC spec table — every cell matches. (The doc labels the component
"PCIe Wrapper"; the **(PIW)** annotation below is the FW name for it.)

| Domain | Component | PoR | Warm | Platform | WD | FLR | Hot | Hard | Soft | Debug |
|--------|-----------|:---:|:----:|:--------:|:--:|:---:|:---:|:----:|:----:|:-----:|
| Security | HSM (Caliptra) | v | | | | | | | | |
| Global | PLL | v | | | | | | | | |
| Global | PVT | v | | | | | | | | |
| PCIe | **PCIe EP** | v | v | | | | | | | |
| PCIe | **PCIe Wrapper (PIW)** | v | v | | v | | | | | |
| PSOC | PSOC | v | | v | v | v | v | | | |
| PSOC | **PSOC (MuC, PuC)** | v | | v | v | v | v | | | |
| NoC | NoC (VSP, VME, PBox) | v | v | v | v | v | v | v | | |
| NoC | D2D | v | v | v | v | v | v | v | | |
| NoC | PMMU | v | v | v | v | v | v | v | | |
| C2C | C2C | v | | | | | | | | |
| Compute | Scheduler | v | v | v | v | v | v | v | v | v |
| Compute | PDMA | v | v | v | v | v | v | v | v | v |
| Compute | VCore | v | v | v | v | v | v | v | v | v |
| Compute | CCE | v | v | v | v | v | v | v | v | v |

### Key consequences for FW reset handling
- **PCIe EP (PEP)** is reset only by **PoR** and **Warm**. Notably **not** by WD/FLR/Hot.
- **PCIe Wrapper (PIW)** is reset by **PoR, Warm, and WD**.
- **PSOC (MuC, PuC)** — the CPU the management/PCIe FW runs on — is reset by **PoR, Platform,
  WD, FLR, Hot**, but **not Warm**. So under a **warm reset the MuC FW keeps running**, which is
  exactly what lets FW handle warm reset via reset-manager PRE/POST callbacks instead of a reboot.
- **WD reset asymmetry:** WD resets **PIW** and **the MuC** but **not PEP**. So after a watchdog
  reset the FW reboots (boot path, not warm callback) and re-inits a PIW that was reset while the
  **PEP controller was not** — a stale-but-live controller case FW reset logic must account for.
- Warm reset domain membership is fixed by this table, but FW can further **mask** components out
  of a reset type via `el_reset_config_comp_reset_mask(type, comp, enable)` (e.g. PCIe EP FW masks
  `EL_RESET_COMP_PSOC` out of warm). Do **not** mask PIW out of warm — the warm-reset PIW-deassert
  inference depends on it.

## Sources
- Spec: `~/docs/Iridium/Iridium SoC spec.docx` — Table 4-2 "Reset Domains"
  (section "Architectural Design → Reset Domains", table object index 3). **Authority for the
  reset-domain matrix** (the PRM does not contain this table). Verified 2026-06-11.
- Source: `~/projects/iridium/three/fw/ip/psoc/drivers/el_reset/el_reset.c`
  (`el_configure_component_reset_mask` → per-type `RST_TYPE_*_UNIT_RST_EN` masks;
  `el_reset_warm_reset_isr` → `el_pre_reset`/`el_post_reset` notify callbacks)
- Source: `~/projects/iridium/three/fw/infra/include/el_reset_common.h`
  (`enum el_reset_type`, `enum el_reset_components`, `struct el_reset_cb_data`)
