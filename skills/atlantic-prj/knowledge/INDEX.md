# Atlantic Project Knowledge Base — Index

This is the **expert memory** for the `atlantic-prj` skill. Every time the skill researches a
topic through the PRM, NoC spec, project source, or manifests and verifies an answer, it records
the findings here so the next query on the same subject is fast and does not require re-reading
the source documents.

> **Atlantic is the current, most-recent project.** Its RTL lives on the **`main`** branch of
> each IP RTL repo. Previous projects (e.g. Iridium) are frozen on their own **freezed rtl**
> branch — record which branch a finding was verified against when it matters.

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
| _(none yet)_ | | | | |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from spec | Verified from source | Verified from manifest | Partial / needs follow-up
- **RTL branch:** main (Atlantic) | <freezed-rtl branch> (inherited) | n/a
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- PRM: `Atlantic PRM.docx` — para/table <n> (section "<heading>")
- NoC: `Atlantic NoC Specification.docx` — para/table <n> (section "<heading>")
- Source: `~/projects/atlantic/<variant>/<path>` (`<function/symbol>`)
- Manifest: `<path>.star`
```
