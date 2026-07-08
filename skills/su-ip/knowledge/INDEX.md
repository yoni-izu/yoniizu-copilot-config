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
