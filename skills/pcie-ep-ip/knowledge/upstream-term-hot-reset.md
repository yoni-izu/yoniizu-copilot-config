# Upstream (device→host) access termination on Hot Reset — HW trigger vs FW release

- **Recorded:** 2026-11-10
- **Status:** Verified from EP spec + generated CSR headers + FW source (iridium/two workspace).
  RTL semantics of the `*_MASK` polarity and the PIW reset domain are **[unverified — Neo was
  unavailable: `mctl -t vm ip` empty / VM stopped]**.
- **Tags:** upstream termination, ups_term, tlp_upstream_term, smlh_req_rst, hot reset, link down, bus master enable, cfg_bus_master_en, viral, piw_main, sei, fw release, delay_link_dn_rst, app_ltssm_enable

## Scope / Question
On a Hot Reset, is upstream (device→host) traffic terminated (by HW or FW)? If yes, does FW
release the termination after the reset?

## Findings

### Spec intent (EP Spec Rev 2.0.0 §"Upstream Access Termination", paras 948-954)
Three upstream-termination triggers, each with its own enable bit:
1. **Hot reset** (`smlh_req_rst_not` asserted) — **de-assertion must be done by FW**.
2. **Bus Master Enable cleared** (`cfg_bus_master_en`=0) — de-asserted **by HW** when BME is set again.
   An upstream transaction seen while BME=0 → status bit + interrupt.
3. **Viral**.
Termination is reported as an **SEI** ("Upstream/downstream termination (SEI)", para 1677).

Hot-reset flow (§6.9, paras 853-868): `smlh_req_rst_not` → early-warning IRQ ("stop data
transmission, expected to be handled by firmware"); HW clears `app_ltssm_enable` so the
controller keeps **accepting and terminating** upstream requests / DBI reads until FW re-asserts
`app_ltssm_enable`; then `link_req_rst_not` → PSOC reset → `core_rst_n` → FW reconfigures.

### HW register (A1 v1 `3_0_11_3p0_1389` and A2 v2 `1_0_7_1p0_503`, identical)
- `PIW_MAIN_CFG_TLP_UPSTREAM_TERM` @ PIW_MAIN + **0x148**, RW, **reset 0x3**:
  `CFG_BUS_MASTER_MASK` b0, `SMLH_REQ_RST_MASK` b1, `VIRAL_MASK` b2. Description is "TBD".
  No explicit "release/clear-termination" field exists.
- `PIW_MAIN_CFG_TLP_UPS_TERM_STATUS` @ 0xBC0: `AR_TERM_OCCURED` b0 / `AW_TERM_OCCURED` b4
  (HW-set, sticky — same pattern as axi-term-status-sticky.md) + capture regs
  `TLP_UPS_TERM_TERM_{AR,AW}{ADDR_L/H,ID,USER}`, response cfg `TLP_UPS_TERM_CFG_{RESP,RUSER}`.
- PIW SPI cause bits: `UPS_TERM_CFG_BUS_MASTER` 0x100, `UPS_TERM_SMLH_REQ_RST` 0x200,
  `UPS_TERM_VIRAL` 0x400.
- If "MASK=1 ⇒ trigger disabled" (usual naming), reset 0x3 means **hot-reset and BME triggers
  are disabled out of reset, only Viral is armed**. [unverified polarity — confirm with Neo]

### FW (iridium/two workspace)
- **Nothing writes `TLP_UPSTREAM_TERM`** (no reference outside `build_artifacts` headers), so the
  HW reset value 0x3 stays in effect. Nothing reads/clears `TLP_UPS_TERM_STATUS`.
- `UPS_TERM_*` SPI bits appear only as names in `ip/pcie/ep/drivers/el_piw/el_piw_spi_tree.c:70-78`
  (cause decoding) and in `proj_infra/soc/dts/a{1,2}/a{1,2}_irq_mon_spi.dtsi` (node
  `status="disabled"` on A1). No dedicated handler.
- Hot-reset handling in `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c`:
  `handle_early_hot_reset_warning()` (≈1681) returns unless `DELAY_LINK_DN_RST` set; otherwise
  `/* TODO: Stop outbound data transmission. */`, `set_config_ready(false)`, `ltssm_en_set(true)`.
  `handle_post_hot_reset()` (≈1704) re-enables LTSSM if `DELAY_LINK_TRAINING`, then
  `set_config_ready(true)`. **No upstream-termination enable or release anywhere.**
  `LINK_REQ_RST_N` fall: "Nothing to do".

### Conclusion
- Designed: yes, HW can terminate upstream on hot reset, and FW must release it.
- Implemented in FW: no. FW neither arms nor releases the hot-reset trigger, and the
  "stop outbound" step is a TODO. Under the likely mask polarity, the hot-reset trigger stays
  disabled. Upstream protection during hot reset then rests on the DWC controller itself
  (accept-and-terminate while `app_ltssm_enable` is held low).
- If anyone enables `SMLH_REQ_RST` termination (clears b1), FW must add the release step in
  `handle_post_hot_reset()` (or on `LINK_REQ_RST_N` rise), unless RTL shows PIW_MAIN is reset by
  the hot-reset PSOC reset. Today it is unknown whether the hot reset also resets PIW_MAIN.
  `phy-reset.md` covers only the PHY: hot reset → `phy_rst_n` lane reset.

## Open (needs Neo, `--chip a1` and `a2`)
1. `*_MASK` polarity of `TLP_UPSTREAM_TERM` (1 = trigger disabled?).
2. Whether the hot-reset termination is a sticky latch, and how FW releases it (toggle mask bit?).
3. Whether the PSOC hot-reset (`async_pcie2psoc_hot_rst_n`) resets PIW_MAIN, which would self-clear it.

## Sources
- Doc: `~/docs/PCIe EP/PCIe EP Specification Rev 2.0.0.docx` paras 848-868 (§6.9 Link Down and
  Hot Reset), 948-954 (Upstream Access Termination), 1667-1679 (interrupts).
- Headers: `build_artifacts/include/ips/pcie/v1/3_0_11_3p0_1389/prototype/piw_main_cfg_regs.h:1195-1206,2813-2821`,
  `..._regs_attr.h` (`TLP_UPSTREAM_TERM__RST 0x3`); v2 `1_0_7_1p0_503` identical.
- FW: `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c:1681-1790`, `ip/pcie/ep/drivers/el_piw/el_piw_spi_tree.c:70-78`.
- Related: `app-ctrl-crs-drs.md`, `link-down-fw-notification.md`, `axi-term-status-sticky.md`, `phy-reset.md`.
