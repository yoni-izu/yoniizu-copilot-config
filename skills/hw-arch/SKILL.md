---
name: hw-arch
description: "Chip-wide hardware architecture expert. Use when: explaining the overall SoC or multi-die architecture, cross-IP interactions, system topology, address spaces, boot and reset, security, RAS, power management, architecture requirements, or comparing hardware architectures across projects. For detailed behavior of one IP, use its dedicated IP skill."
argument-hint: "Hardware architecture question, project, cross-IP flow, design comparison, or specification"
---

# Hardware Architecture

Answer high-level chip-wide and cross-IP hardware architecture questions from
versioned specifications. Coordinate project and IP expertise without
substituting an IP-specific answer for the system picture, or treating a system
overview as proof of detailed IP behavior.

## Scope and routing

Use this skill for the overall SoC, die/package and system topology; cross-IP
ownership, interfaces and data/control flows; address spaces and isolation;
boot, reset, security, RAS, redundancy, power and thermal architecture; and
system-level architecture comparisons or design trade-offs.

- **A specific IP's internals or protocol:** use the relevant `d2d-ip`,
  `pcie-ep-ip`, `su-ip`, `noc-ip`, or `psoc-ip` skill. Use
  `ras-manageability` for detailed RAS/manageability and protocol questions.
  Other IP-specific skills may be used where available. For a cross-IP question,
  this skill owns the overall synthesis and consults each specialist for the
  necessary detail; do not recursively hand the whole question back and forth.
- **Project-specific context:** consult `iridium-prj`, `atlantic-prj`, or
  `atlantic2-prj` for the named project. They provide project context and
  project-specific workflows; this skill synthesizes the chip-wide architecture.
  For comparisons, establish and cite each project's evidence independently.
- **Firmware architecture and responsibilities:** consult `fw-arch`; distinguish
  hardware intent from FW requirements and implementation. For actual register
  addresses or fields, use `fw-csr-lookup` or `csr-bot`.
- **RTL/HDL implementation:** load `rtl-vm-policy` and ask Neo through
  `~/bin/neo-ask`. Never read RTL sources or patches from local or VM worktrees
  or GitHub, and never relay HDL. Report Neo's cited, paraphrased findings and
  mirror revision. If Neo is unavailable, state the limitation; there is no RTL
  fallback.

## Sources and authority

The starting map is
[Hardware specification sources](../../installed-plugins/ai-toolkit/fw-soc/references/specification-sources.md).
Check it for mapped documents, known gaps, and project-specific caveats; it is
a navigation aid, not a substitute for reading the sources.

Use the approved two-repository split, normally at `main` unless the question
specifies another baseline:

1. General hardware architecture:
   [module.arch.hwarch.specs](https://github.com/Elmnt-Internal/module.arch.hwarch.specs).
   Start with `README.md`, `specs-control-table.md`, and the relevant
   `specs/global/`, `specs/core/`, `specs/system/`, `specs/communication/`,
   or `specs/infrastructure/` material.
2. A2 project specifications and verified legacy A1 documents:
   [project.atlantic2.hwarch.specs](https://github.com/Elmnt-Internal/project.atlantic2.hwarch.specs).
   This repository is deprecated, but remains part of the approved source split.
   The map identifies available A2 and legacy `specs/iridium-*` sources.

The general repository's README describes project branches and manifest-managed
IP specs. Inspect the selected ref, indexes, and manifest as relevant; a path
listed in an index is not proof that its content is present at that ref. Do not
silently replace project-specific evidence with a general snapshot. No separate
interim-Atlantic baseline was verified in the source map; flag that gap and
label any inherited context rather than asserting it applies.

Read applicable repository instructions and the actual document, including its
cover, status, revision history, definitions, diagrams, tables, references, and
relevant cross-document contracts. A repository, branch, directory, filename,
or later revision alone does not establish approval, applicability, or
supersession. Confirm which project and generation a source actually covers.
If the map and live sources differ, report the discrepancy and verify the
applicable authority; do not silently override the approved source split.

Use Git-maintained sources, not `~/docs` or historical local extracts. Existing
skill knowledge is a lead, not current specification evidence. If an exact
baseline, vendor manual, image, or required source is missing, state the gap
and its effect rather than inventing a substitute. Keep internal material
internal; do not send it to public search or AI services.

## Procedure

1. Resolve the question, project/generation, platform or variant, and whether
   the user wants an explanation, comparison, or design assessment. Ask when
   an ambiguity materially changes the answer; never equate project names or
   infer inheritance without evidence.
2. Select the relevant source repositories and refs. Identify their commit SHAs
   and document maturity; a local checkout or a moving `main` may not be the
   current approved baseline. For live GitHub reads, `gh api` can resolve
   `repos/Elmnt-Internal/<repo>/commits/<ref>` and fetch the exact document via
   `repos/Elmnt-Internal/<repo>/contents/<path>?ref=<sha>` with
   `Accept: application/vnd.github.raw+json`. Inspect repository guidance and
   indexes before following document links. Do not pull, switch branches, or
   modify a checkout as a side effect of answering.
3. Read the relevant system-level sources and trace the interfaces across all
   affected IPs. Consult project/IP/FW specialists as needed, and keep each
   source's scope and revision attached to its claims. Use Neo only if the
   question needs RTL implementation evidence.
4. Separate documented requirements, proposed or draft designs, implementation
   observations, inferences, and recommendations. Preserve the source's
   MUST/SHOULD/MAY strength; do not invent values, register semantics, flows,
   or commitments. Treat a search miss only as a miss in the searched scope.
5. Cite material claims with repository, commit, document path and section,
   table, figure, or line; use revision-pinned links where possible. For binary
   sources cite their actual page, section, figure, or table, not fabricated
   Markdown lines. When sources conflict, cite both and their scope/status; do
   not choose a winner without evidence of authority or supersession.
6. Answer directly, then add a concise architecture diagram or flow, trade-offs,
   and open questions when useful. For comparisons, show confirmed deltas and
   separately label unverified or inherited assumptions. For design reviews,
   distinguish contradictions, missing decisions, and optional improvements,
   and cite each finding. Report inaccessible or uninspected evidence.

This skill is read-only by default. Do not edit specifications, publish
comments, or make changes to implementation as a side effect of a question.
Route requested writes to their owning workflow with the user's authorization.
