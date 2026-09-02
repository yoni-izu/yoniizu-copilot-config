# NoC / Fabric IP Knowledge Base — Index

Expert memory for the `noc-ip` skill. Records verified findings about the Atlantic2 NoC/fabric
spec, address map, routing, sideband fabrics, AxUSER, and FIU — plus the A2 RTL delta
(`ip.noc.rtl` `iridium..main`) — so repeat questions are fast.

## How to use this file
1. **Before researching**, scan the table and full-text search `knowledge/` for relevant entries.
2. **After verifying** a non-trivial finding, create/update `knowledge/<slug>.md` (template
   below), add/refresh a table row (newest at top), and cite sources.
3. **If contradicted later**, correct the topic file — keep the KB accurate.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| A2 NoC overview + address map (INC/GPU) | [a2-noc-overview.md](a2-noc-overview.md) | noc, fabric, address map, npa, fiu, inc, gpu, mem fabric, mng, ctrl, dbg, trc, ect, ts, routing, tp8, tp16 | Verified from spec | 2026-08-30 |

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
- Doc: `Atlantic2 NoC Specification Rev 1.4.0.docx` — para/table <n> (section "<heading>")
- RTL: `Elmnt-Internal/ip.noc.rtl` — `compare/iridium...main` (file/commit <sha>)
```
