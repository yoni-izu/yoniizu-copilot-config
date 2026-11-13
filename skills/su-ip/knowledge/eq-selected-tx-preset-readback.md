# Reading back which TX EQ preset equalization selected (SD_EQ_STATUS viewport)

- **Recorded:** 2026-11-08
- **Status:** Verified from DM reference + DM databook + PCS wrapper databook + A1 CSR model + A1 FW/DT
- **Tags:** equalization, preset, tx preset, selected preset, winning preset, sd_eq_status,
  sd_eq_control1, ras des, silicon debug, fom, dir, gen3_eq_control_off, pset_req_vec,
  fom_inc_initial_eval, pset_coef_map, fs, lf, coefficients, cursor, gen5, readback, shell, a1

## Scope / Question

"Is there a register that holds the TX preset equalization selected? If not, how can FW tell
which preset won?" Also: a review of a colleague's doc (`c2c_gen5_preset_config.md` §3).

## Findings

1. **A direct, role-specific preset capture exists; it is not a universal final-result field.**
   Most DM_DSP/DM_USP `PRESET` fields are an *input* or a *force* knob:
   - The `DSP_*_TX_PRESET` subfields in `PL16G_CAP_OFF_20H` (0x188),
     `PL32G_CAP_OFF_20H` (0x1C4), and `PL64G_LANE_EQ_*` (0x1D8..) configure the DSP-side
     starting TX presets advertised in EQ TS (Lane EQ Control).
   - `LINK_CONTROL2.COMPLIANCE_PRESET` → compliance mode only.
   - `SD_EQ_CONTROL2.FORCE_LOCAL_TX_PRESET` → debug force.
   - **PCIe Base Spec 6.3 defines a USP-side debug capture for each lane.** At 32.0 GT/s
     (Gen5), the `Upstream Port 32.0 GT/s Transmitter Preset` nibble in the 32.0 GT/s Lane
     Equalization Control entry is RO when the port operates as USP and Crosslink Supported=0.
     Capture from 128b/130b EQ TS2 or received equalization requests with `Use_Preset` set is
     **MUST@FLIT**: mandatory when the component's Flit Mode Supported bit is set and strongly
     recommended otherwise. This depends on the advertised capability, not the current Link mode.
     Requests with `Use_Preset` clear **should not** affect the capture.
     This is not just the initial EQ TS2 preset. It provides the best direct register indication
     of the USP TX's preset-mode request during equalization. In successful preset-only FOM
     equalization, where the final best-preset request is accepted, this should identify the
     selected preset.
     Treat it as the captured/requested preset, not unconditional proof of the final applied TX
     state: the field does not itself report acceptance, and coefficient-only requests should leave it
     unchanged. On the local DSP, it does not report the DSP TX that the USP tunes in Phase 2.
     Crosslink Supported=1 also makes the capture unavailable to software.
   - Therefore the direct field is preferred when reading the local USP TX after successful
     preset-only FOM equalization; `SD_EQ_STATUS2/3` coefficient decoding remains the fallback
     for other roles, with the same validity and table-match checks below. Verify actual capture
     behavior on hardware.
   - The inspected A1 USP CSR model resets `FLIT_MODE_SUPP` to 1 and `CROSS_LINK_SUPPORT` to 0.
     These are not live readings; `CONFIG_EL_SE_PRE_LTSSM_NONFLIT` can clear `FLIT_MODE_SUPP`.

2. **RAS-DES EQ viewport (A1 DM_DSP/DM_USP offsets; ref "B+0xd0.."):**
   | Off | Reg | Fields |
   |---|---|---|
   | 0x2BC | SD_EQ_CONTROL1 (R/W, sticky, RST 0) | EQ_LANE_SEL[3:0], EQ_RATE_SEL[6:4] (0=8G,1=16G,2=32G,3=64G,4=128G), EXT_EQ_TIMEOUT[9:8], EVAL_INTERVAL_TIME[17:16], FOM_TARGET_ENABLE[23], FOM_TARGET[31:24] → **RMW lane/rate only** |
   | 0x2CC | SD_EQ_STATUS1 | EQ_SEQUENCE[0], **EQ_CONVERGENCE_INFO[2:1]** (0 not attempted, 1 success, 2 fail; cleared when this port *starts EQ Lead again*), rule A..D violations [6:3], REJECT[7] (valid when =2), REMOTE_2ND_PRE[31:26] (Gen6) |
   | 0x2D0 | SD_EQ_STATUS2 | LOCAL PRE[5:0], CURSOR[11:6], POST[17:12], RX_HINT/2ND_PRE[23:18], LOCAL_FOM[31:24] (max FOM) |
   | 0x2D4 | SD_EQ_STATUS3 | REMOTE PRE[5:0], CURSOR[11:6], POST[17:12], REMOTE_LF[23:18], REMOTE_FS[29:24] |
   **STATUS2/3 are valid only when EQ_CONVERGENCE_INFO == 1.** All fields are sticky, per lane and per rate.
   With EQ bypass to the highest rate (see `eq-bypass-highest-rate.md`), only the top rate (Gen5 on A1 ASIC)
   has data. Gen3/Gen4 read as 0 (not attempted), and their zero coefficients would falsely decode as P4.

3. **Why inversion works (preset-only FOM mode):** DM databook p.839: in FOM mode the lead evaluates the
   initial TX settings and then each preset in `PSET_REQ_VEC`. The **final request is the preset with
   the highest FOM, requested again**, so the partner TX ends on exactly that preset's
   coefficients. In **DIR mode** (FB_MODE=0) the lead then nudges the coefficients ±1, so the end state is
   generally **not** a preset. For the USP TX, check the DSP lead's rate-shadowed feedback mode.
   - `FOM_INC_INITIAL_EVAL`=1 lets the *initial* TX setting win, which can be a preset outside the vector.
   - If the optional `CX_EQ_MASTER_COEF_REQ_FOR_FOM_ENABLE` feature is enabled and
     `GEN3_FCOEF_MAX_VEC_DEPTH` is nonzero, the controller's final FOM request uses the
     highest-scoring **coefficients**, not a preset (databook p.840-842). The preset capture can
     then be stale; FOM mode alone does not prove that the final TX setting is a preset.
     A1's FCOEF availability and active depth have not been verified.
   - `GEN3_EQ_PSET_REQ_AS_COEF` (bit 25) is internally reserved; leave 0.

4. **Preset→coefficient table:** `GEN3_RELATED_OFF` resets to 0x402000, so
   `GEN3_EQ_PSET_COEF_MAP_MODE_PROG` = 0 and FW never sets it. The controller then takes the table from the **PHY/PCS**,
   not the 0x898/0x89C programmable map. PCS wrapper databook p.179-181, Table 5-8 (Gen3/4/5 map =
   Gen6 100%-swing table, **FS=48, LF=16**), as (C-1 pre, C+1 post) magnitudes:
   P4 (0,0) · P0 (0,12) · P1 (0,8) · P2 (0,10) · P3 (0,6) · P5 (5,0) · P6 (6,0) ·
   P7 (5,9) · P8 (6,6) · P9 (8,0) · P10 (0,16). C0 = 48 − pre − post. **All 11 are unique**, so an exact lookup works.
   Spec-ratio rounding with FS=48 gives P7 post = 10, but the PCS uses **9**. Prefer the exact table and fall back
   to nearest-ratio using EQ_REMOTE_FS/LF only for a non-Synopsys partner (A2 PCIe-compliant GPU).
   Tables 5-9..5-11 cover reduced Tx-margin swing (not used during EQ).

5. **Who tunes whom:** EQ lead = **USP in phase 2** (tunes the DSP TX), **DSP in phase 3**
   (tunes the USP TX). Reference SD_EQ_CONTROL1 EXT_EQ_TIMEOUT text, databook p.839.
   Read at the lead (STATUS3, which carries the partner's FS/LF in the same snapshot) for the
   partner TX. STATUS2 "local" = this port's own TX; databook wording is terse, so cross-check both ends once on silicon.

6. **A1 config (ip.se.sw.fw 7153358, proj_infra `soc/dts/a1/a1_hsi.dtsi`):** `eq-presets-gen5 = <9 1 3>`,
   `eq-fom-inc-initial-eval-gen5 = <0>` (needs `CONFIG_EL_SE_PCIE_EQ_RATE_CFG`). FB_MODE stays at reset
   (GEN3_EQ_CONTROL_OFF RST 0x0D03F071 → FOM, vector P4..P9, INC_INITIAL=1), so the effective Gen5 shadow is
   0x0402_0A31; in preset-only FOM with those settings active, **the winner ∈ {P1 (0,8),
   P3 (0,6), P9 (8,0)}**. Gen6: `<2 1>`.
   FW never reads SD_EQ_STATUS. `show se eq_cfg` / `el_se_sv eq_cfg_dump` show configuration only.

## Sources

- Historical secondary vendor source (unavailable in verified Git catalog): `DWC_pcie_ctl_reference.pdf` (6.42a-lca01) p.2727-2743: SD_EQ_CONTROL1/2, SD_EQ_STATUS1/2/3;
  p.3251-3281: GEN3_RELATED_OFF, GEN3_EQ_LOCAL_FS_LF, GEN3_EQ_PSET_INDEX, GEN3_EQ_CONTROL_OFF (FB_MODE, PSET_REQ_AS_COEF).
- Historical secondary vendor source (unavailable in verified Git catalog): `DWC_pcie_ctl_dm_databook.pdf` p.837-840 (FOM vs DIR, lead flow), p.840-842
  (optional FOM coefficient requests), p.851 (final-request acceptance), p.843-844 (Table A-3
  default map), p.855-856 (worked example).
- Historical secondary vendor source (unavailable in verified Git catalog): `dwc_pcie6_phy_pcs_wrapper_databook.pdf` p.179-181 (Table 5-8, Gen3/4/5 mapping note);
  `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` p.53 (coefficient mapping figure).
- PCI-SIG, `NCB-PCI_Express_Base_6.3.pdf`, p.118 (`MUST@FLIT` definition); §7.7.6.9,
  Table 7-83, p.1160 (32.0 GT/s USP Tx Preset debug-capture field); p.467
  (Phase 2/3 responsibilities and required TS1 reflection of
  accepted preset/coefficient requests). Local copy:
  `/mnt/c/Users/YoniIzutskiver/OneDrive - Element labs/Downloads - BKP/NCB-PCI_Express_Base_6.3.pdf`.
- A1 CSR `build_artifacts/include/ips/se/v1/3_0_11_3p0_1389/prototype/dm_dsp_dwc_pcie_dsp_regs.h` + `_attr.h` (offsets, resets).
- A1 USP CSR model `dm_usp_dwc_pcie_usp_regs.h:436,745` and `_regs_attr.h:207,336`
  (Flit Mode Supported and Crosslink Supported masks and reset values); FW
  `ip/se/drivers/scale_engine/el_se_sv.c:884` (optional pre-LTSSM Flit capability override).
- FW `ip.se.sw.fw` `drivers/snps_dm_ctl/dwc_dm_ctl.c` (dm_set_eq_rate_cfg), `drivers/scale_engine/el_se_shell*.c`; `proj_infra/soc/dts/a1/a1_hsi.dtsi:1138`.
