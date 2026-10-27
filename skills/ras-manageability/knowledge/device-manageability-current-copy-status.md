# Device Manageability Specification — Current Copy Status

- **Recorded:** 2026-10-25
- **Status:** Partial — current copy inspected; no prior copy available for a content diff
- **Tags:** device manageability, working spec, revision history, OEM commands, tracked changes, open items, source precedence

## Scope / Question

Check the newly copied `Device Manageability Specification.docx` and establish what it is,
what its latest recorded updates cover, and whether it is suitable as an A1/customer baseline.

## Findings

- The only matching file found under `/home/yoniizu` is
  `docs/RAS/Device Manageability Specification.docx`. Its core properties say author
  `<author>`, last modified by `<modifier>`, modified **2026-10-25 16:06 UTC**,
  revision 2. There was no earlier matching copy available to compute a before/after diff.
- This remains the broader internal working specification, not a replacement for the
  A1/customer-facing specification. Its revision history labels Version 0.5 for Aqua as the
  baseline through its preceding entries, then records additional changes through
  **15 September 2026** (revision-history table 0, rows 91–103).
- The latest history entries describe PLDM OEM DMA commands; DMA and memory ownership
  instances for `OemMcOwnership`; `OemLtssmLogger (0x50)`; `OemWriteMem (0x03)` /
  `OemReadMem (0x04)`; `OemLinkControl`; consolidated OEM completion-code definitions; and
  eFuse `FEATURE_DISABLE` mappings (table 0, rows 95–103).
- The file still contains substantial review markup: 7,534 `w:ins` elements, 2,653 `w:del`
  elements, and 68 comment records. The revision history explicitly says turquoise-titled
  sections are internal and must not be shared with customers, and that comments must be
  removed from a customer version (table 0, row 38). Do not treat this copy as customer-ready.
- Open or incomplete material remains in the body, including FW-update questions (paragraphs
  1295, 1299), crash-dump flow marked WIP/TBD (1674–1675), reset behavior marked “Needs
  update” (2102–2103), RAS SRAM errors marked TBD (2415), and power/thermal algorithm
  questions (4160, 4223). There are additional TBD markers in paragraphs 2472, 3598, and 4595.
- Keep the source-precedence rule in
  [`a1-manageability-v0p6-document-status.md`](a1-manageability-v0p6-document-status.md):
  use the A1 Manageability Specification for A1/customer contracts; use this broader
  document for generic/internal/future detail only when A1-specific material does not settle
  the question.

## Sources

- Spec: `Device Manageability Specification.docx` — core properties; revision-history table 0,
  rows 38 and 91–103.
- Spec: `Device Manageability Specification.docx` — paragraphs 1295, 1299, 1674–1675,
  2102–2103, 2415, 2472, 3598, 4160, 4223, and 4595.
- Spec: `Device Manageability Specification.docx` — `word/document.xml` counts for `w:ins`
  and `w:del`; `word/comments.xml` contains 68 comment records.
