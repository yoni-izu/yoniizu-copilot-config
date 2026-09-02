# PSOC IP Knowledge Base — Index

Expert memory for the `psoc-ip` skill. Records verified findings about the PSOC spec (uC cluster,
boot/reset, slow IOs, GPIO/eFuse, crypto, HSM/Caliptra, platform guidelines) and the PSOC RTL
delta (`ip.psoc.rtl` `iridium..main`).

## How to use this file
1. **Before researching**, scan the table and full-text search `knowledge/`.
2. **After verifying** a non-trivial finding, create/update `knowledge/<slug>.md`, add a table row
   (newest at top), and cite sources.
3. **If contradicted later**, correct the topic file.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| PSOC uC cluster (3× Cortex-M7 + TuC) — no PCIe uC | [uc-cluster.md](uc-cluster.md) | psoc, uc, cortex-m7, muc, ptuc, ruc, tuc, cluster, pcie uc, mailbox, no pcie uc | Verified from spec | 2026-08-30 |
| PSOC A2 overview + Rev2-vs-Rev1 delta | [a2-psoc-overview.md](a2-psoc-overview.md) | psoc, a2, overview, crypto, sha, ecc p-384, hsm, caliptra, boot, reset, slow io, delta | Verified from spec | 2026-08-30 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from spec | Verified from RTL | Partial / needs follow-up
- **RTL branch/tag:** main (A2) | iridium (A1 baseline) | n/a
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what this entry covers>

## Findings
<verified facts — concise, durable>

## Sources
- Doc: `PSOC Specification Rev2.0.1.docx` — para/table <n> (section "<heading>")
- RTL: `Elmnt-Internal/ip.psoc.rtl` — `compare/iridium...main` (file/commit <sha>)
```
