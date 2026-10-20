# A1 Manageability v0.6 Document Status and Source Precedence

- **Recorded:** 2026-10-15
- **Status:** Verified from spec documents
- **Tags:** a1 manageability, version 0.6, customer baseline, source precedence, tracked changes, comments, broken cross reference, document quality

## Scope / Question

Classify the newly added `A1 Manageability Specification.docx`, determine its relationship to
`Device Manageability Specification.docx`, and record any caveats that affect future research.

## Findings

- `A1 Manageability Specification.docx` identifies itself as **Ember - A1 Management
  Specification, Version 0.6, September 2026**. It is the preferred source for A1/customer
  contracts.
- The A1 document is a curated, A1-specific derivative rather than a complete replacement for
  the broader internal working document. It retains A1 platform/reset/FW-update flows,
  PLDM/MCTP over SPI, streaming boot, initialization, flashless mode, power/thermal, telemetry,
  and A1-supported OEM commands.
- `Device Manageability Specification.docx` remains useful for generic protocol background and
  internal/future material absent from the A1 document, including SPDM, DSP0267 FW update,
  extended OEM memory/DMA/PCIe commands, diagnostics, and explicitly next-project features.
  Those additions must not be treated as A1 requirements without another A1-specific source.
- The A1 file is still a review artifact:
  - 11 unresolved comments.
  - Unaccepted tracked insertions and deletions.
  - Three `TBD` occurrences, including `RAS SRAM Errors table: TBD`.
  - One broken cross-reference in the Vendor IANA paragraph.
  - A stray `f` in the cover/front matter.
  - The cover says version 0.6 / September 2026, while revision-history entries stop at version
    0.5 / August 31, 2026 and include a blank trailing row.
- `python-docx` paragraph text does not include runs nested in tracked insertions. For reliable
  searches, inspect `word/document.xml` in effective final-markup mode: include `w:ins` content
  and exclude `w:del` content.

## Sources

- Spec: `A1 Manageability Specification.docx` — cover; Revision History table 1, rows 1-13;
  headings `Platform Manageability`, `Management Communication Protocol over SPI`, `Chip Bring
  Up Flow`, `Flashless Mode`, `Power/Thermal Loop`, and `Manageability Data`.
- Spec: `A1 Manageability Specification.docx` — Word package
  `word/document.xml` and `word/comments.xml` (tracked changes, comments, placeholders, and
  broken cross-reference).
- Spec: `Device Manageability Specification.docx` — Revision History table 1, especially the
  internal/customer split note and the entries adding next-project SPI features and extended
  OEM commands; headings for SPDM, PLDM FW Update, OEM memory/DMA/PCIe commands, and diagnostics.
