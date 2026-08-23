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
| AlphaWave UCIe D2D IP vendor doc set (AresCORE PHY / GammaCORE ctrl / GammaCORE32 subsystem + MMR) | `alphawave-ip-docs.md` | alphawave, alphawave semi, arescore, gammacore, gammacore32, eland, eland_s, ucie phy, d2d phy, d2d controller, subsystem, mmr, dvsec, cmn mcu, 1xs32, vendor ip, el aw d2d, uisrb, sideband mailbox | Verified from vendor docs | 2026-08-23 |
| Manageability Bridge (D2D wrapper manageability glue) | `manageability-bridge.md` | manageability bridge, management port, mtp, ucie manageability, memory access protocol, test and debug, legality checks, access control, class id, rac, wac, security clearance group, traffic class, mainband, sideband, vdm, compression engine, indirect mtp, doorbell, chiplet id, entity id, ipd2dsw-70 | Verified from spec | 2026-08-16 |
| VDM over SB support (Vendor Defined Messages over sideband) | `vdm-over-sb-support.md` | vdm, vendor defined message, sideband, sb, ucie.sb, fw download, mng fabric, mainband debug, generation fsm, reception fsm, message code 0xff, axi to vdm, credits, checksum, hardware mode, software mode | Verified from spec | 2026-07-15 |
| Pre-FIU transport reachability — SPI-GW reaches horizontal links MMIO can't | `pre-fiu-transport-reachability.md` | spi gateway, spi-gw, mmio transport, fiu, transport switch, dup broadcast, horizontal links, d2-d3, primeboot, sbinit, re-bring-up, verification reads | Verified from FW driver | 2026-06-30 |
| SBINIT-done probe (spare bit vs intr summary) | `sbinit-done-probe.md` | sbinit, sideband init, cmn_top_int2, spare_cfg0, cmn_top_int1, intr1_summary, rom marker, boot probe, link bring-up | Verified from spec + reg header | 2026-06-29 |
| D2D link health monitoring | `health-monitoring.md` | health monitor, link health, eye monitoring, running parity, runtime link testing, ber, slm, per-lane, retrain, autonomous bw, pll lock, ointerrupt_0, ointerrupt_1 | Verified from spec | 2026-06-23 |
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
