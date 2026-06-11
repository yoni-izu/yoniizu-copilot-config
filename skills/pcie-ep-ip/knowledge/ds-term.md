# Downstream Termination (DS_TERM) Datapath

- **Recorded:** 2026-06-11 (migrated from SKILL.md, originally verified earlier)
- **Status:** Verified from RTL
- **Tags:** ds_term, pep_ctrl_aux, elbi, mstr, rasdp, termination, host trapdoor, sei

## Scope / Question

How `PEP_CTRL_AUX_DS_TERM_CTRL` works: what each bit gates in the host → device direction,
how termination is triggered (SW vs RASDP), notification, and the host-side one-way trapdoor
behavior.

## Register

| Register | Offset | Doc | Purpose |
|----------|--------|-----|---------|
| `PEP_CTRL_AUX_DS_TERM_CTRL` | PEP_AUX + 0x34 (PF), VF mirrors at 0x10..0x80 | Element EP | Downstream termination control. Reset=0x55. Bits: PF_UNCERR_MSTR_TERM_MASK(0x1), PF_SW_MSTR_TERM(0x2), PF_UNCERR_ELBI_TERM_MASK(0x4), PF_SW_ELBI_TERM(0x8). |

## Findings

`PEP_CTRL_AUX_DS_TERM_CTRL` controls two independent gates inside the DWC IIP at the **AXI
master output stage** — i.e. the controller boundary toward SoC LBW fabric. Source:
`Elmnt-Internal/ip.pcie.rtl` → `pep_macro/rtl/pep_axi_mstr.v`:

```verilog
assign pcil_pf_mstr_ds_term[i] = pcil_pf_mstr_req_vld &
    ((pcil_cfg_pf_sw_mstr_term[i]) | (pcil_rasdp_err_mode & !pcil_cfg_pf_uncerr_mstr_term_mask[i]));
assign muxd_aux_g_pf_elbi_ds_term[i] = muxd_aux_g_pf_elbi_req_vld[i] &
    ((muxd_aux_g_cfg_pf_sw_elbi_term[i]) | (muxd_aux_g_rasdp_err_mode & !muxd_aux_g_cfg_pf_uncerr_elbi_term_mask[i]));
```

Two trigger sources per gate:
- `PF_SW_*_TERM` — software/CSR force
- `rasdp_err_mode & !PF_UNCERR_*_TERM_MASK` — automatic on RASDP uncorrectable error (mask=1
  disables this; reset state 0x55 has both masks set, so auto-termination is disabled by default)

### What gets gated by each bit (host → device direction)

| Bit | Gate location | Blocks (host‑initiated) | Survives |
|-----|--------------|-------------------------|----------|
| `PF_SW_MSTR_TERM` | DWC IIP MSTR master output | BAR / iATU‑routed MEM TLPs to SoC | Config TLPs (handled inside controller); ELBI path; embedded EP CPU |
| `PF_SW_ELBI_TERM` | DWC IIP ELBI master output | **All host CSR access via ELBI** — PIW regs, PEP_CTRL_AUX (incl. `DS_TERM_CTRL` itself), PEP_CTRL_CORE, rest of SoC | Config TLPs; MSTR path; embedded EP CPU; sideband (JTAG/BMC) |

### Notification
- ELBI gate firing raises `intr_sei_ds_term_elbi_pulse_intr` (signal `muxd_aux_g_elbi_term`
  in `pep_macro/rtl/pcie_iip_device_wrap.v`)
- FW SEI bit: `IIP_AUX_SEI_BIT_DS_TERM_ELBI` (`fw/ip/pcie/include/el_piw_sei_bits.h`); handler
  bound in `fw/ip/pcie/drivers/el_piw/el_piw_sei_tree.c`

### Host‑side one‑way trapdoor
- `el_pcie_safe_write32` traverses ELBI when `CONFIG_EL_PCIE_IF_ELBI=y` (`fw/ip/pcie/include/el_pcie.h`)
- "Safe" in cluster architecture (`fw/ip/pcie/docs/pcie-cluster-architecture.md`) means
  resilient to transaction‑level disruption (FLR, BAR remap) — **NOT** ELBI termination
- Once host sets `PF_SW_ELBI_TERM=1` via ELBI, the clear write cannot land — the gate sits in
  front of `PEP_CTRL_AUX`. Recovery requires the embedded EP CPU (local LBW access, unaffected)
  or a sideband path
- Host self‑test (`drivers/snps_ep_ctl/tests/host_tests_sv.c`) uses
  `wait_for_user_to_disable_elbi_termination(...)` as the manual recovery workaround
- API: `dwc_ep_ctl_set_downstream_termination(dev, enable)` toggles both `PF_SW_MSTR_TERM` and
  `PF_SW_ELBI_TERM` together

### Unrelated mechanism — don't confuse
- `PIW_RR_MAIN_CFG_ELBI_MNG_SEC_TERM_*`, `PIW_*_CFG_TEE_TERM_*`,
  `PIW_MNG_CFG_{SEC,EMPTY_STRB,NARROW_BURST,MSI_WR,MSIX_WR}_TERM_*` are **separate AXI
  security/range filters in PIW**, downstream of the DWC DS_TERM gate. Different blocks,
  different schema (`AXI_TERMINATION{,_CFG}.yml`).

## Sources
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `pep_macro/rtl/pep_axi_mstr.v`, `pep_macro/rtl/pcie_iip_device_wrap.v`
- FW: `fw/ip/pcie/include/el_piw_sei_bits.h`, `fw/ip/pcie/drivers/el_piw/el_piw_sei_tree.c`,
  `fw/ip/pcie/include/el_pcie.h`, `drivers/snps_ep_ctl/tests/host_tests_sv.c`
- Doc: `fw/ip/pcie/docs/pcie-cluster-architecture.md`
