# Iridium Project Knowledge Base — Index

This is the **expert memory** for the `iridium-prj` skill. Every time the skill researches a
topic through the PRM, NoC spec, project source, or manifests and verifies an answer, it records
the findings here so the next query on the same subject is fast and does not require re-reading
the source documents.

## How to use this file

1. **Before researching** any question, scan the table below (and full-text search the
   `knowledge/` directory) for relevant entries. If a matching topic exists, read its detail
   file first — it may answer the question directly or narrow the search.
2. **After verifying** a non-trivial finding from the spec or source, record it:
   - Create or update a topic file `knowledge/<short-topic-slug>.md` using the template below.
   - Add/refresh a row in the table (newest at top).
   - Always include citations (doc + paragraph/table, source repo/path + symbol, manifest path)
     so the finding can be re-verified and trusted.
3. **If a finding is later contradicted**, correct the topic file and note the change — keep
   the knowledge base accurate, not just append-only.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| `pb_sec_die_count` is the allowed target-die range (N means dies 0..N-1), not the number of PB instances; A1 ASIC PMMU config allows 2 dies and quad-die Palladium allows 4 | [pb-protection-node-die-count.md](pb-protection-node-die-count.md) | protection bits, pb, pmmu, die count, devicetree, iridium | Verified from FW source and board DeviceTree | 2026-10-23 |
| **PCIe PIW SEI-to-KMD notifications in ARM ASIC** — In the inspected A1 `mng_arm_asic` artifact, `CONFIG_EL_PIW_DRAIN_NOTIFICATION` is unset. The generic code has host-notify workers and an EQ-mailbox plugin path, but the generated image has no active `el,pcie-sei-error-handler` node, so the callback path is not registered; drain and other PIW SEIs are not sent to KMD by this image. The plugin currently serializes RESP_ERR, AXI_TERM, DRAIN_INTR, CPL_TIMEOUT, and PULSE, but not AER. | [pcie-sei-kmd-notifications.md](pcie-sei-kmd-notifications.md) | pcie, piw, sei, kmd, drain, mailbox, arm asic | Verified from source and build artifact | 2026-10-14 |
| **PRSTn to PSOC masked by default (eFuse)** — PRSTn into PSOC does not propagate unless the fuse mask is changed. ROM bring-up swaps PRSTn source→PSOC (`BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE` 0xdac, a1 `0x100000000019dac`=0); **after the swap the PRSTn is effectively masked**, so any PRSTn toggle *after* the source swap is dead. Meaningful PRSTn toggle must happen **before** the source is swapped to PSOC. | [prstn-psoc-fuse-masked.md](prstn-psoc-fuse-masked.md) | prstn, perst, psoc, fuse, efuse, masked, prst_select_source, boot_and_rst_top, rom bring-up, reset source, link training, gpio37 | Reported by owner (FW lead) — authoritative | 2026-09-08 |
| Reset Domains (component × reset type) | [reset-domains.md](reset-domains.md) | reset, reset domains, warm, por, wdt, flr, hot, piw, pcie ep, psoc, muc | Verified from spec | 2026-06-11 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from spec | Verified from source | Verified from manifest | Partial / needs follow-up
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- PRM: `Iridium PRM.docx` — para/table <n> (section "<heading>")
- NoC: `Iridium NoC Specification.docx` — para/table <n> (section "<heading>")
- Source: `~/projects/iridium/<variant>/<path>` (`<function/symbol>`)
- Manifest: `<path>.star`
```
