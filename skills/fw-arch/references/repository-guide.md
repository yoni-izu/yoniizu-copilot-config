# Architecture repository guide

Repository: `Elmnt-Internal/module.arch.sw.specs`

Default checkout: `~/fw-arch`

This is a navigation aid, not a cached architecture specification. Re-read the
live checkout's instructions/indexes and verify paths before relying on them.
The repository includes broader software architecture, not only firmware.
Find the actual FW responsibility/contract sections for each question rather
than treating every software initiative as a FW requirement.

## Starting points

| Path relative to checkout | Purpose |
|---|---|
| `AGENTS.md` | Repository rules, architecture taxonomy, project terminology, authoring dependencies |
| `README.md`, `docs/index.md` | Repository/site orientation |
| `docs/internal_index.md` | Internal specification navigation |
| `docs/external_index.md` | Customer-facing specification navigation |
| `docs/workspace_index.md` | Workspace and planning material |
| `docs/change_requests_index.md` | Change-request navigation |
| `mkdocs.yml` | Published navigation and site configuration |
| `internal/specs/` | Internal specification sources |
| `internal/workspace/` | Project initiatives and working material; verify maturity |
| `internal/change-requests/README.md` | Change-request lifecycle and status conventions |
| `internal/templates/specification.md` | Specification structure, glossary, revision history, traceability |
| `internal/templates/change-request.md` | Change-request structure |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR expectations, discussion links, review/CI workflow |
| `internal/guides/external-spec-generation.md` | Internal-to-external generation rules |
| `internal/guides/import-specification.md` | Import phases and source-conversion workflow |
| `documentation/external/` | External documentation; check generation metadata before editing |

Examples useful for understanding document shape, not blanket authority:

- `internal/specs/capabilities/a2-host-connectivity/a2-host-connectivity.md`
  illustrates a capability specification with explicit draft status.
- `internal/workspace/A2/SW_initiatives/a2-sw-stream-dma-initiatives.md`
  illustrates project initiative/planning material.
- `internal/specs/inference-serving/vllm/vllm-config/vllm_config.external.yaml`
  illustrates metadata controlling external generation.

Each document's current content and explicit approval/supersession evidence
determine its authority; these examples and directory labels do not.

## Bounded discovery

Use built-in glob/search/view tools when available. Start with indexes and narrow
searches by topic and Markdown path. Examples for terminal-only environments:

```bash
rtk proxy git -C /home/yoniizu/fw-arch remote get-url origin
rtk proxy git -C /home/yoniizu/fw-arch status --short --branch
rtk proxy git -C /home/yoniizu/fw-arch rev-parse HEAD
rtk proxy rg -n -i --glob '*.md' 'firmware|boot|reset|manageability|mailbox' /home/yoniizu/fw-arch/docs
rtk proxy rg -n -i --glob '*.md' 'firmware|host.fw|boot flow' /home/yoniizu/fw-arch/internal/specs
rtk proxy git -C /home/yoniizu/fw-arch log -5 --format=fuller -- internal/specs/path/to/document.md
```

Replace example document paths and topics with discovered ones. Do not dump the
whole repository or scan binary assets as text. Use exact JSON via
`rtk proxy gh ...` for live GitHub metadata; inspect the PR base/head and changed
paths before fetching content, especially for cross-repository links.

## Non-Markdown sources

- Prefer the authoritative editable source when an index links both a source
  and a rendered/generated copy. Check generation metadata before interpreting
  differences as competing designs.
- For PDF, DOCX, XLSX, or diagram evidence, use available format-aware tools.
  Extract to session-private scratch space without modifying the source.
  Do not install tools speculatively; report missing tooling or unreadable
  content and use another authoritative source where possible.
- Preserve headings, table structure, page/sheet identifiers, and provenance.
  Text extraction can lose arrows, merged cells, footnotes, and figures.
  Inspect diagrams/tables visually when interpretation depends on layout.
- Cite the document revision plus page, section, sheet/cell, or figure.
  Do not invent Markdown line numbers from a temporary conversion.
- Exclude uninspected attachments from completeness claims and flag any answer
  that depends on their unavailable evidence.
