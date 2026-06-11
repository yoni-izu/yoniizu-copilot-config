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
