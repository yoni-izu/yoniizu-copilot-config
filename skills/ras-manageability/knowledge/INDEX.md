# RAS & Manageability Knowledge Base — Index

This is the **expert memory** for the `ras-manageability` skill. Every time the skill
researches a topic through the RAS spec, Device Manageability spec, FW code, or related RTL
and verifies an answer, it records the findings here so the next query on the same subject is
fast and does not require re-reading the (very large) source documents.

## How to use this file

1. **Before researching** any question, scan the table below (and full-text search the
   `knowledge/` directory) for relevant entries. If a matching topic exists, read its detail
   file first — it may answer the question directly or narrow the search.
2. **After verifying** a non-trivial finding from a spec, FW code, or RTL, record it:
   - Create or update a topic file `knowledge/<short-topic-slug>.md` using the template below.
   - Add/refresh a row in the table (newest at top).
   - Always include citations (doc + paragraph/table/page, RTL/FW repo + file + symbol) so the
     finding can be re-verified and trusted.
3. **If a finding is later contradicted**, correct the topic file and note the change — keep
   the knowledge base accurate, not just append-only.

> RAS / manageability findings often cut across IPs (PCIe, SE/C2C, D2D, HBM, MMU, GPU). When a
> fact is equally relevant to one specific IP, mirror or cross-link it in that IP's knowledge
> base (`pcie-ep-ip`, `su-ip`, `d2d-ip`).

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| PIW SEI host-notify — execution context + plugin-registration liveness gate | [piw-sei-host-notify.md](piw-sei-host-notify.md) | sei, piw, host notify, error handler, el_piw_err_handler_api, register_err_handler, irq tree, irq_mon, hard isr, mailbox, eq, el_pcie_sei_op_code, el_pcie_sei_raw_capture, el_error_handler, plugin, pcie_sei | Verified from FW + design docs | 2026-06-30 |
| C2C / Scale-Up link errors → tray reset (FW vs BMC split) | [c2c-link-error-tray-reset.md](c2c-link-error-tray-reset.md) | c2c, scale-up, link error, drop mode, viral, tray reset, scale up reset, reset mask, c2c reset, bmc, fw responsibility, spec owner directive, maxim | Verified from spec-owner directive + FW PRs | 2026-06-23 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from RTL | Verified from spec | Verified from FW | Partial / needs follow-up
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- Spec: `<file>` — para/table <n>
- Doc: `<file>` p.<n>
- RTL/FW: `<repo>` → `<path>` (`<function/signal>`)
```
