# DWC EQ preset request vector

- **Recorded:** 2026-10-11
- **Status:** Verified from the verification model and Iridium controller configuration
- **Tags:** GEN3_EQ_PSET_REQ_VEC, pset, equalization, PCIe, rate shadow

## Scope / Question

How the DWC `GEN3_EQ_PSET_REQ_VEC` value represents equalization preset requests.

## Findings

- The request vector is a bitmap, not a single encoded preset. Each set bit requests evaluation of the corresponding preset; multiple bits may be set.
- The Iridium A1 controller configuration sets the Gen3/Gen4 default vector to `0x3f0`, bits 4 through 9 (P4-P9). This is the configured baseline and does not independently validate any Gen5/Gen6 experiment-specific vector.

## Sources

- Verification model: `Elmnt-Internal/ip.se.verif` -> `dm_macro_vtb/sv/testbench/vtb/src/sv/api/vtb_link_ctx.sv` (`pset_vector` comment).
- Project configuration: `Elmnt-Internal/project.iridium.rtl.eip_pcie` -> `ep/i_DWC_pcie_ctl/element_labs_ep8sx8_gen6_axi4_20250814.tcl` (`DEFAULT_GEN3_EQ_PSET_REQ_VEC`).
