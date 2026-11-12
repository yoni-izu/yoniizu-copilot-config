# A2 PIW TuC (tiny micro-controller) in the PCIe EP

- **Recorded:** 2026-11-10
- **Status:** Verified by Neo against A2 RTL/CSR
- **Tags:** a2, tuc, tiny micro controller, piw, piw_lbw_mng, piw_mng_cfg, tuc_pm, tuc_run_mode_config_reg, warm reset assist
- **Source:** Neo (`neo-ask --chip a2`), ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, ip.infra.rtl.shared.atlantic2@9bc4f713, project.atlantic2.rtl@875d851. Raw answer: HSI A2 PCIe plan review, pcie_review/a/19_tuc.md.

## Findings
- **The TuC exists in A2 and matches A1.** An earlier Neo answer said there was no TuC in ip.pcie.rtl. That answer was wrong.
- **Instance:** module `tuc`, instance `tuc`, in `piw_macro/rtl/piw_lbw_mng.v:994-1115`.
  - Hierarchy: `pcie_ep_cls` → `piw_macro` → `piw_wrap` → `piw_core` → `piw_lbw_mng` → `tuc`.
  - The module is defined in ip.infra.rtl.shared.atlantic2 at `tuc/rtl/tuc.v:29`.
- **Fabric:** the TuC masters both fabrics.
  - **MNG:** `mng_ups_axi[1]` of arbiter `el_axi_arb_if_mng_ups_2`.
  - **CTRL:** reached through `el_axi_expander_tuc2ctrl`, then arbitrated with the FIU in `piw_lbw_ctrl.v`.
- **CSRs:** the PIW_MNG_CFG block.
  - `TUC_PM[0..63]` at 0x6CC–0x8C8.
  - `TUC_REGISTER_0..15` at 0x8CC.
  - `TUC_START_PC` at 0x910.
  - `TUC_RUN_MODE_CONFIG_REG` at 0x914 (go, halt, max AXI write outstanding).
  - `HALT_STATUS` at 0x960, `GENERAL_STATUS` at 0x964, the timer at 0x968–0x974, `INTR_SEI_*` at 0x98C–0x994.
- **Outstanding writes:** the default is 64, configured through 0x914. `PIW_TUC_RD_OUTS=4` limits only ctrl-path reads.
- **Naming:** the yml (`PIW_MNG_CFG.yml:639`) calls it a "transaction usage counter". That description is stale; the RTL header says "tiny micro controler".

## FW relevance
- Plan story B3.5 (TuC spike). It could assist warm reset if the 20 ms PCIe-spec budget is missed (R1.3).
