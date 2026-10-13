# Scale Up (SU) IP Knowledge Base — Index

This is the **expert memory** for the `su-ip` skill. Every time the skill researches a topic
through the DM controller docs, PHY docs, C2C spec, or RTL and verifies an answer, it records
the findings here so the next query on the same subject is fast and does not require
re-reading the (very large) source documents.

## How to use this file

1. **Before researching** any question, scan the table below (and full-text search the
   `knowledge/` directory) for relevant entries. If a matching topic exists, read its detail
   file first — it may answer the question directly or narrow the search.
2. **After verifying** a non-trivial finding from a doc or RTL, record it:
   - Create or update a topic file `knowledge/<short-topic-slug>.md` using the template below.
   - Add/refresh a row in the table (newest at top).
   - Always include citations (doc + page, RTL repo + file + symbol) so the finding can be
     re-verified and trusted.
3. **If a finding is later contradicted**, correct the topic file and note the change — keep
   the knowledge base accurate, not just append-only.

> Note: PHY findings here apply to the shared PCIe6 PHY. When a PHY fact is equally relevant
> to the EP, consider also recording it in the `pcie-ep-ip` knowledge base (the PHY is identical).

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| SE/C2C **shell command map + `diag se`** — full inventory of the production SE / LTSSM-logger / C2C shells with the include-or-exclude call per command (read-only + unattended-safe only; mutating `set …`, interactive monitors, `phyfw_verify`, arg-requiring `fiu_oam_table`, and all SV shells excluded); the `EL_DIAG_SHELL_REGISTER` contract (**`argc == 1`, `argv` passed as NULL** ⇒ every selected handler must be `argv`-free) and the `diag pcie_ep` wrapper/section-table/Kconfig pattern it copies; plus the A1 build gotchas (`octl ws build ip/se` SKIPs the compile and its sanity config omits `CONFIG_EL_SE`, so verify via `octl ws build zephyr_apps/mng --args "platform=host variant=asic"`; `board=` filter does not match — use `platform=`+`variant=`; `octl ws lint` only checks the last commit; run `octl` from `…/fw`) | [se-diag-shell-command.md](se-diag-shell-command.md) | se, c2c, scale engine, shell, diag, diag se, diag pcie_ep, el_diag_shell, el_diag_shell_register, el_se_diag, el_se_shell, el_c2c_shell, el_se_sv, el_c2c_sv, ltssm logger, counters, ebr, traffic_gen, bug report, snapshot, dump wrapper, octl build, sanity, clang-format | Verified from FW source + build + lint | 2026-10-13 |
| DM BAR sizing: mask is a **shadow/CS2 write-only** reg at the BAR address (mask=size-1, BAR0 bit[0]=enable, BAR1 mask = upper 32b); CS2 writes are **not** gated by DBI_RO_WR_EN but the BAR[3:0] TYPE/PREFETCH CDM write **is** (Fig 3-56 asterisk) → DM FW silently drops is_64bit/prefetchable; mask also gates DBI writes to BAR[31:4] ⇒ program mask before base; AUX path truth: `DBI_CTRL.DBI_CS2` bit1 drives the pin, `DBI_IATU_ACCESS` bit6 is the APB-gateway field, `EXT_LBC` is ELBI status (wrong poll register — completion is `DBI_CTRL.DBI_CS` cleared on `lbc_dbi_ack`) | [dm-bar-sizing-shadow-mask.md](dm-bar-sizing-shadow-mask.md) | bar, bar mask, bar sizing, shadow register, dbi_cs2, cs2, dbi2, dbi_ro_wr_en, misc_control_1_off, bar0_type, prefetchable, aux dbi, dbi_ctrl, ext_lbc, lbc_dbi_ack, dm controller, dm_configure_bar, dwc_dm_ctl, ep controller, bar_size_cfg, iatu bar match | Verified from doc (DM+EP databooks) + RTL + FW | 2026-10-04 |
| C2C FIU SEI/SPI interrupt tree — CAUSE/MASK/MASKED_INTR triplet (SEI 0x334/8/0x340, SPI 0x324/8/0x330; FIU_MEM bit0, FIU_MNG bit1); masking gates the CPU line via MASKED_INTR but CAUSE still latches for the poll monitor; SEI-owned drain (one SEI call W1C-clears both FIU leaves); el_irq_tree dispatch-then-clear ordering makes the dual SEI[0]+SPI[0] binding idempotent; accum_rc sticky-negative poisons the count only (handlers still run → return 0 for not-my-config) | [c2c-fiu-interrupt-tree.md](c2c-fiu-interrupt-tree.md) | c2c, se, scale up, fiu, sei, spi, interrupt, cause, mask, masked_intr, w1c, el_irq_tree, fiu_mem, fiu_mng, isolation_drain, error handling, ipsesw-139 | Verified from regs + FIU driver + walker | 2026-08-17 |
| DM link width config: LINK_CAPABLE (PORT_LINK_CTRL_OFF) + NUM_OF_LANES (GEN2_CTRL_OFF) both required per databook §3.2.1; gate different LTSSM phases (Detect vs L2.Idle/Polling.Active); x4-core reset defaults already x4; PCIE_CAP_MAX_LINK_WIDTH also needed only when capping below HW max; LINK_CAPABLE=2*width-1 | [link-width-configuration.md](link-width-configuration.md) | link width, lanes, link_capable, num_of_lanes, port_link_ctrl_off, gen2_ctrl_off, max_link_width, lane resizing, x4, dm controller, dwc, ltssm, se, scale engine | Verified from doc (DM databook v6.42a) | 2026-08-05 |
| C2C `same_index` topology (8-rail, 2048 GB) — architect decision (Maxim Leshin: same-index chip connectivity only, no cross-index/in-tray links), preset bit math (fold device bits 40:38 → window 2048 GB; nLUT index bits 46:41; bit 46 = max offset 10), + author⇄architect conversation, IPSESW-295 impl state (binding+DTS landed, driver wiring pending) | [c2c-same-index-topology.md](c2c-same-index-topology.md) | topology, same_index, c2c, se, 8 rail, 2048gb, nlut_select_bits, selector_cfg_addr_bit, rail_device_list, sled, tray, rack, ipsesw-295, maxim leshin, architect decision, address translation | Verified from FW + DT + architect directive; emulation verification pending | 2026-07-22 |
| C2C board topology → SE LUT driver config (6 presets: how nlut_select_bits/selector_cfg_addr_bit/num_rails/rail_device_list/bar_size_gb change nLUT indexing, rail grouping, NPA/CAM table slots, BAR size, tray-count validation) | [topology-driver-configuration.md](topology-driver-configuration.md) | topology, c2c, se, scale engine, lut, nlut, npa, cam, rail, rail_device_list, nlut_select_bits, selector_cfg_addr_bit, num_rails, bar_size_gb, all_to_all, pairs, halves, quads, address translation, preset, driver configuration | Verified from FW driver + DeviceTree | 2026-07-09 |
| Scale-up tray/rack interconnect topology + switch bottleneck (intra-tray P2P mesh vs inter-tray PCIe switch; TX≫RX = backpressure not drop) | [scale-up-tray-rack-topology.md](scale-up-tray-rack-topology.md) | tray, rack, scale-up, c2c, topology, point-to-point, p2p, all-to-all, switch, rail, oversubscription, bottleneck, backpressure, flow control, redundancy, npa, gen6 x4 | Verified from doc (NoC spec) | 2026-07-06 |
| DM controller RADM clock-gating un-gate (DBI 0x88c bit 0; USP/DSP share register) | [dm-radm-clock-gating.md](dm-radm-clock-gating.md) | dm controller, dwc, clock_gating_ctrl_off, radm_clk_gating_en, radm_clk, async csr2mem, mem2csr, ecc, error reporting, usp, dsp, role-symmetry, init sequence | Verified from RTL + FW driver | 2026-06-30 |
| C2C Compression register (EN gate, reset 0x6=disabled, field map) | `c2c-compression-register.md` | compression, c2c_compression, en bit, alg_disable, core_dis, cp bit mask, reset default, scale up, se driver | Verified from RTL + regs | 2026-06-29 |
| C2C Drop Mode & Isolation Mode (error containment, FW responsibility) | `drop-mode-error-containment.md` | drop mode, isolation mode, viral, error containment, ras, link down, watchdog, completion timeout, fw responsibility, tray reset, scale up reset, reset mask, c2c reset, bmc, isolation drain, isolation drop, drain enable, drain arming, payload vs enable | Verified from doc + spec-owner directive + RTL + FW driver | 2026-06-30 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from RTL | Verified from doc | Partial / needs follow-up
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- Doc: `<file>` p.<n>
- RTL: `<repo>` → `<path>` (`<function/signal>`)
```
