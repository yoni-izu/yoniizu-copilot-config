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
| SE `REMOTE_REQ_ID` is written only after C2C link training because the C2C core register access bus-faults pre-link on Palladium; current runtime gate is all-links/all-dies | [se-remote-req-id-link-gate.md](se-remote-req-id-link-gate.md) | se, c2c, remote_req_id, link training, bus fault | Verified from Iridium SE firmware source | 2026-10-25 |
| OEM PCIe Link Control and native auto-flush ACK integration; owned-port recovery and RTL-version caveats | [oem-pcie-link-control.md](oem-pcie-link-control.md) | link control, root port, link disable, ownership, quiesce | Verified from SNPS docs + FW; lab failure reported, not independently reproduced | 2026-10-26 |
| Iridium C2C traffic-generator shell: positive-duration runs poll/re-arm idle directions and stop at a deadline; zero duration starts once without firmware re-arm | [c2c-traffic-generator-shell-semantics.md](c2c-traffic-generator-shell-semantics.md) | c2c, traffic generator, shell, tgen, timed run, idle | Verified from Iridium FW source; hardware semantics not independently checked | 2026-10-25 |
| Preserved DM ports reload the cached EP/RP role from `DEVICE_TYPE` because normal role programming is skipped; role enum values are EP=0, RP=4 | [dm-preserved-role-cache.md](dm-preserved-role-cache.md) | dm, role, preservation, device type, root port, endpoint, flr | Verified from FW source | 2026-10-23 |
| DWC DM bus/device: DBI+0xB10 has Downstream Port mode caveat; AUX CFG_INFO_1 at +0x14 continuously mirrors DWC bus/device and BME/MSE outputs, providing endpoint readback; zero status does not prove never enumerated | [dm-assigned-bdf-status.md](dm-assigned-bdf-status.md) | dm, pcie, bdf, bus number, device number, requester id, enumeration, cfg_info_1 | Verified from DM databook + Iridium RTL + generated headers | 2026-10-20 |
| Asgard S1 nLUT 16-JBOG mapping plus scheme-selected defaults: O/R/ELS1 use restored Ocean NPA/CAM, A uses Asgard per-chip NPA and parity CAM, TEST uses topology fallback; middleware data can override DTS defaults. History: pre-Aqua used `0x80000+(J<<3)` / `BDF(J+1,0,0)` | [asgard-s1-nlut-16jbog-mapping.md](asgard-s1-nlut-16jbog-mapping.md) | nlut, npa, cam, bdf, req-id, same_index, aqua, asgard, 16 jbog, ocean, fallback, git history, strap | verified (sheet+FW+DT build; hardware validation pending) | 2026-10-21 |
| Same-index 256-GiB BAR propagation: size reaches DM shadow mask only on switched SU; TX/RX chip-bit OR selectors require BAR[40:38] == rail index unless SU folding is changed; RX DPA[63:41] comes from local NPA_TO_DPA_CFG, not incoming NPA[63:41]; BMC supplies bases/BDFs, not size | [same-index-256gb-bar-propagation.md](same-index-256gb-bar-propagation.md) | same_index, bar-size-gb, 256gb, 2048gb, selectors, npa, npa-to-dpa, bmc, pldm | Verified from FW + Iridium RTL; no HW test | 2026-10-19 |
| SE link-init has **two independent "external config" waits** that a single Kconfig used to gate: (1) the **conn-scheme** wait (`el_se_scheme_sem`, before deferred `device_init`) whose only producers are the board **strap** (`el_se_strap.c`) and the SE **shell** — **no middleware/BMC path exists**; (2) the **MW LUT** wait (`mw_lut_ready`, before `el_se_lut_program()`/LTSSM) fed by the **BMC over PLDM** via `ip/mng soc_providers.c` → `el_se_lut_commit()` + `el_se_lut_signal_mw_ready()` (or the sample filler under `EL_OOB_STANDALONE`). `el_se.c` force-overrides `CONFIG_EL_SE_SKIP_EXTERNAL_CONFIG_WAIT=1` (IPSESW-379), which made `C2C_LUT_MW_TIMEOUT_MS` **dead** (timeout always 0 → DTS fallback → later BMC `el_se_lut_commit()` returns `-EACCES` once `hw_programmed`). Fix: new `CONFIG_EL_SE_SKIP_LUT_MW_WAIT` gate (default n on ASIC, excluding `ARCH_POSIX` host builds that also set `EL_ASIC`), `C2C_LUT_MW_TIMEOUT_MS` default **600000 on ASIC** (10 Palladium / 1000 else); conn-scheme wait stays skipped. Cost: no producer ⇒ link-init blocks up to 10 min before LTSSM enable / `se_ready`. Also recorded: `el_mng_get_tray_id()` (currently a stub, no semaphore) is **not uniformly ordered** against `mw_lut_ready` if it were ever wired async like the BMC LUT tables — the FIU chip-id path (`el_se.c:1059→1065→1443`, via `device_init()` at 1863) reads it **before** `el_se_lut_wait_for_mw()` (1897), while the REMOTE_REQ_ID path (`el_se_lut.c:170,494`, via `el_se.c:1956`) reads it **after**; the mailbox RPC path (`el_mng_comms.c:179`) has no ordering relationship at all. | [se-lut-mw-wait-vs-conn-scheme-wait.md](se-lut-mw-wait-vs-conn-scheme-wait.md) | se, c2c, lut, mw, middleware, conn-scheme, strap, ipsesw-379, skip_external_config_wait, el_se_skip_lut_mw_wait, c2c_lut_mw_timeout_ms, c2c_lut_dts_fallback, el_se_lut_commit, el_se_lut_signal_mw_ready, mw_lut_ready, eacces, hw_programmed, bmc, pldm, soc_providers, link-init, se_ready, el_mng_get_tray_id, tray_id, chip_id, race, fiu, remote_req_id | Verified from FW source | 2026-10-15 |
| SE/C2C **shell command map + `diag se`** — full inventory of the production SE / LTSSM-logger / C2C shells with the include-or-exclude call per command (read-only + unattended-safe only; mutating `set …`, interactive monitors, `phyfw_verify`, arg-requiring `fiu_oam_table`, and all SV shells excluded); the `EL_DIAG_SHELL_REGISTER` contract (**`argc == 1`, `argv` passed as NULL** ⇒ every selected handler must be `argv`-free) and the `diag pcie_ep` wrapper/section-table/Kconfig pattern it copies; plus the A1 build gotchas (`octl ws build ip/se` SKIPs the compile and its sanity config omits `CONFIG_EL_SE`, so verify via `octl ws build zephyr_apps/mng --args "platform=host variant=asic"`; `board=` filter does not match — use `platform=`+`variant=`; `octl ws lint` only checks the last commit; run `octl` from `…/fw`) | [se-diag-shell-command.md](se-diag-shell-command.md) | se, c2c, scale engine, shell, diag, diag se, diag pcie_ep, el_diag_shell, el_diag_shell_register, el_se_diag, el_se_shell, el_c2c_shell, el_se_sv, el_c2c_sv, ltssm logger, counters, ebr, traffic_gen, bug report, snapshot, dump wrapper, octl build, sanity, clang-format | Verified from FW source + build + lint | 2026-10-13 |
| DM BAR sizing: mask is a **shadow/CS2 write-only** reg at the BAR address (mask=size-1, BAR0 bit[0]=enable, BAR1 mask = upper 32b); CS2 writes are **not** gated by DBI_RO_WR_EN but the BAR[3:0] TYPE/PREFETCH CDM write **is** (Fig 3-56 asterisk) → DM FW silently drops is_64bit/prefetchable; mask also gates DBI writes to BAR[31:4] ⇒ program mask before base; AUX path truth: `DBI_CTRL.DBI_CS2` bit1 drives the pin, `DBI_IATU_ACCESS` bit6 is the APB-gateway field, `EXT_LBC` is ELBI status (wrong poll register — completion is `DBI_CTRL.DBI_CS` cleared on `lbc_dbi_ack`) | [dm-bar-sizing-shadow-mask.md](dm-bar-sizing-shadow-mask.md) | bar, bar mask, bar sizing, shadow register, dbi_cs2, cs2, dbi2, dbi_ro_wr_en, misc_control_1_off, bar0_type, prefetchable, aux dbi, dbi_ctrl, ext_lbc, lbc_dbi_ack, dm controller, dm_configure_bar, dwc_dm_ctl, ep controller, bar_size_cfg, iatu bar match | Verified from doc (DM+EP databooks) + RTL + FW | 2026-10-04 |
| C2C FIU SEI/SPI interrupt tree — CAUSE/MASK/MASKED_INTR triplet (SEI 0x334/8/0x340, SPI 0x324/8/0x330; FIU_MEM bit0, FIU_MNG bit1); masking gates the CPU line via MASKED_INTR but CAUSE still latches for the poll monitor; SEI-owned drain (one SEI call W1C-clears both FIU leaves); el_irq_tree dispatch-then-clear ordering makes the dual SEI[0]+SPI[0] binding idempotent; accum_rc sticky-negative poisons the count only (handlers still run → return 0 for not-my-config) | [c2c-fiu-interrupt-tree.md](c2c-fiu-interrupt-tree.md) | c2c, se, scale up, fiu, sei, spi, interrupt, cause, mask, masked_intr, w1c, el_irq_tree, fiu_mem, fiu_mng, isolation_drain, error handling, ipsesw-139 | Verified from regs + FIU driver + walker | 2026-08-17 |
| DM link width config: LINK_CAPABLE (PORT_LINK_CTRL_OFF) + NUM_OF_LANES (GEN2_CTRL_OFF) both required per databook §3.2.1; gate different LTSSM phases (Detect vs L2.Idle/Polling.Active); x4-core reset defaults already x4; PCIE_CAP_MAX_LINK_WIDTH also needed only when capping below HW max; LINK_CAPABLE=2*width-1 | [link-width-configuration.md](link-width-configuration.md) | link width, lanes, link_capable, num_of_lanes, port_link_ctrl_off, gen2_ctrl_off, max_link_width, lane resizing, x4, dm controller, dwc, ltssm, se, scale engine | Verified from doc (DM databook v6.42a) | 2026-08-05 |
| C2C `same_index` topology (8-rail, 2048 GB) — architect decision and selector bit math; A1 FW strap selects physical connection map, while topology selects DTS NPA/CAM tables by local device index (scheme may add CAM overrides); middleware LUT data can supersede DTS fallback | [c2c-same-index-topology.md](c2c-same-index-topology.md) | topology, same_index, c2c, se, strap, connection scheme, npa, cam, reqid, 8 rail, 2048gb, nlut_select_bits, selector_cfg_addr_bit, rail_device_list, sled, tray, rack, ipsesw-295, maxim leshin, architect decision, address translation | Verified from A1 FW + DT and architect directive; emulation verification pending | 2026-10-21 |
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
