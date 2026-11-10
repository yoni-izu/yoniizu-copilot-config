# D2D IP Knowledge Base — Index

This is the **expert memory** for the `d2d-ip` skill. Every time the skill researches a
topic through the spec or RTL and verifies an answer, it records the findings here so the
next query on the same subject is fast and does not require re-reading the source documents.

## How to use this file

1. **Before researching** any question, scan the table below (and full-text search the
   `knowledge/` directory) for relevant entries. If a matching topic exists, read its detail
   file first — it may answer the question directly or narrow the search.
2. **After verifying** a non-trivial finding from the spec or RTL, record it:
   - Create or update a topic file `knowledge/<short-topic-slug>.md` using the template below.
   - Add/refresh a row in the table (newest at top).
   - Always include citations (doc + paragraph/table/page, RTL repo + file + symbol) so the
     finding can be re-verified and trusted.
3. **If a finding is later contradicted**, correct the topic file and note the change — keep
   the knowledge base accurate, not just append-only.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| Lane repair vs width degrade: Advanced-Package-only repair, Standard-Package x16→x8 degrade; AW RUNTIME_LINK_TEST_CNTRL/MB_DATA_REPAIR/width_deg regs; FW skips REPAIR* states (mask 0x1CDFF07) and sets NO_DEGRADE=1 — no mechanism in place. A2: same (N3 `aresSN36n3`/`gamma…Tn3` bump map = 2×x16, no redundant lanes; v2 headers identical; no a2 D2D DTS yet) | `lane-repair-and-width-degrade.md` | lane repair, redundant lane, width degrade, x8, standard package, advanced package, mbinit.repairmb, mbtrain.repair, phyretrain, runtime link test control, mb_data_repair, width_deg, err_log0, mmpl_cfg, no_degrade, ltsm-state-mask, scratch_reg5, speed degrade, trainerror, a2, atlantic2, n3, aressn36n3, eland_gamman64b1tn3 | Verified from UCIe + AW MMR + spec + FW + A1/A2 RTL | 2026-11-04 |
| Common SRAM uncorrectable interrupt source: cmn_ram.e_data_error (N3 main checkout; Iridium applicability unverified) | `common-sram-uncorrectable-source.md` | sram, uncorrectable, cmn_top, e_data_error, interrupt, ecc | Verified RTL wiring; external decoder unavailable | 2026-10-28 |
| A1/Iridium ASIC indirect transport (spi_gw0) & per-app D2D SB init (primeboot only) | `asic-transport-and-sb-init-per-app.md` | iridium, a1, asic, indirect transport, spi-gw, spi_gw0, el,die-indirect-transport, chosen node, sbinit, sideband bringup, el_d2d_manager_sideband_bringup, per-app d2d, primeboot, mng, fla runtime, aw_d2d depends el_spi_gw, transport selection | Verified from FW | 2026-09-25 |
| AlphaWave UCIe D2D IP vendor doc set (AresCORE PHY / GammaCORE ctrl / GammaCORE32 subsystem + MMR) | `alphawave-ip-docs.md` | alphawave, alphawave semi, arescore, gammacore, gammacore32, eland, eland_s, ucie phy, d2d phy, d2d controller, subsystem, mmr, dvsec, cmn mcu, 1xs32, vendor ip, el aw d2d, uisrb, sideband mailbox | Verified from vendor docs | 2026-08-23 |
| Manageability Bridge (D2D wrapper manageability glue) | `manageability-bridge.md` | manageability bridge, management port, mtp, ucie manageability, memory access protocol, test and debug, legality checks, access control, class id, rac, wac, security clearance group, traffic class, mainband, sideband, vdm, compression engine, indirect mtp, doorbell, chiplet id, entity id, ipd2dsw-70 | Verified from spec | 2026-08-16 |
| VDM over SB support (Vendor Defined Messages over sideband) | `vdm-over-sb-support.md` | vdm, vendor defined message, sideband, sb, ucie.sb, fw download, mng fabric, mainband debug, generation fsm, reception fsm, message code 0xff, axi to vdm, credits, checksum, hardware mode, software mode | Verified from spec | 2026-07-15 |
| Pre-FIU transport reachability — SPI-GW reaches horizontal links MMIO can't | `pre-fiu-transport-reachability.md` | spi gateway, spi-gw, mmio transport, fiu, transport switch, dup broadcast, horizontal links, d2-d3, primeboot, sbinit, re-bring-up, verification reads | Verified from FW driver | 2026-06-30 |
| SBINIT-done probe (spare bit vs intr summary) | `sbinit-done-probe.md` | sbinit, sideband init, cmn_top_int2, spare_cfg0, cmn_top_int1, intr1_summary, rom marker, boot probe, link bring-up | Verified from spec + reg header | 2026-06-29 |
| D2D link health monitoring; AW adapter 0xDC combines correctable/uncorrectable fields and requires severity masking | `health-monitoring.md` | health monitor, link health, eye monitoring, running parity, runtime link testing, ber, slm, per-lane, retrain, autonomous bw, pll lock, ointerrupt_0, ointerrupt_1, uncorr_err_stat_w | Verified from spec + vendor MMR | 2026-10-20 |
| D2D / UCIe error injection mechanisms | `error-injection.md` | error injection, inject, crc, ecc, parity, prbs, link test, d2d_err_link_test_ctl, imp_mb_config6, imp_mb_config7, ber, runtime link testing, testability | Verified from spec | 2026-06-23 |
| UCIe error notification / reporting mechanism | `ucie-error-notification.md` | ucie, error, notification, reporting, escalation, errmsg, sideband, linkerror, rdi, fdi, viral, dvsec, correctable, non-fatal, fatal, uie | Verified from spec | 2026-06-16 |
| Iridium per-die D2D instance counts (hD2D vs vD2D) | `iridium-d2d-instance-counts.md` | iridium, hd2d, vd2d, horizontal, vertical, instance count, topology, lanes, bandwidth, routing, cce | Verified from RTL/address-map + spec | 2026-06-12 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from RTL | Verified from spec | Partial / needs follow-up
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- Spec: `<file>` — para/table <n>
- RTL: `<repo>` → `<path>` (`<function/signal>`)
```
