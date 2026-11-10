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

Release 0.5.231 diagnostic caveats (2026-11-04): the
[LinkStatus implementation entry](su-pcie-linkstatus-attribute-impl.md) also covers
the OEM EP unsupported stub, raw PLDM booleans, the EP1 successful-zero fallback,
ownership-gated EP0 reads, a startup-sweep retry gap, a speed/generation unit
mismatch, and shipped in-band read-error masking.
The same entry assesses the open release ports EP #471 / MNG #522 and the
updated main MNG #515 at pinned heads. The release port now maps unwired
state/status to `2/2`, and updated main fixes the earlier simulator selector
failure. Startup retry and other gaps remain; author-reported A1 evidence is
not customer PLDM verification.

The [middleware enablement chronology](su-pcie-linkstatus-attribute-impl.md#middleware-enablement-and-release-chronology)
distinguishes the 2026-10-14 A1 power-on wiring from earlier published images
that had PCIe sensor descriptors but no PCIe providers, and from the separately
pinned external 0.5.17 artifact.

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| SU/PCIe LinkStatus providers are wired in A1 FW. Release 0.5.231 has PCIe EP state/status defects; open release ports address normal EP0 and unwired-port state/status, while retry and other gaps remain. | [su-pcie-linkstatus-attribute-impl.md](su-pcie-linkstatus-attribute-impl.md) | linkstatus, sulinkstatus, pcielinkstatus, telemetry attribute, middleware provider, pldm state sensor, su subsystem, el_se, dll_active, rdlh_link_up, unwired port, simulator | Release verified from FW and ELF; PR heads assessed from source and author report; customer EP0 cause unconfirmed | 2026-11-05 |
| SuSurpriseLinkDown spec semantics: one aggregated chip-health state and event, with the wording "encountered"; no clear rule and no counter defined; no per-port attribute in A1 | [su-surprise-link-down-attribute.md](su-surprise-link-down-attribute.md) | susurpriselinkdown, surprise down, scale-up, chip health, joint state and event, attribute 111 | Verified from specs | 2026-11-04 |
| OemPcieLinkControl (formerly OemLinkControl): ownership-gated non-blocking transitions, mandatory traffic-idle check, status and recovery contract | [oem-pcie-link-control.md](oem-pcie-link-control.md) | oempcielinkcontrol, scale-up, ownership, quiesce | Verified from current internal working spec | 2026-10-25 |
| Device Manageability Specification current copy — September 2026 OEM-command additions; still an internal working document with tracked changes, comments, and open items | [device-manageability-current-copy-status.md](device-manageability-current-copy-status.md) | device manageability, working spec, revision history, oem commands, tracked changes, open items, source precedence | Partial — current copy inspected; no prior copy available for a content diff | 2026-10-25 |
| LTSSM Logger: revised spec permits software consumption and suppression/positive thresholds; EP and SE count-query/read restrictions rechecked | [ltssm-logger-contract.md](ltssm-logger-contract.md) | ltssm logger, invalidation, read pointer, statefilter, threshold, efuse, middleware | EP #442 at d1e9d3ce and SE #247 at ef0d32b4; reset guarantees remain unverified | 2026-10-20 |
| Same-index inter-sled NPA/BDF contract: per-die peer BAR bases/BDFs via OEM data load; size remains DTS-controlled, not table-controlled; 256-GiB selector constraints linked to SU | [same-index-npa-bar-contract.md](same-index-npa-bar-contract.md) | same_index, npa, bdf, bar, oemdataload, moduleid | Verified from A1 spec + FW | 2026-10-15 |
| A1 Manageability v0.6 document status and source precedence — A1/customer baseline vs broader internal working spec; tracked-change extraction and known review artifacts | [a1-manageability-v0p6-document-status.md](a1-manageability-v0p6-document-status.md) | a1 manageability, version 0.6, customer baseline, source precedence, tracked changes, comments, broken cross reference, document quality | Verified from spec documents | 2026-10-15 |
| RAS DES lane classes vs manageability attributes — every RAS-DES-sourced attribute is **common-lane**; groups 0/4 (per-lane) back zero attributes; attribute set is per-link, not per-lane → counter-read API should split by lane class (with two enum types, not just two functions) | [rasdes-lane-classes-vs-attributes.md](rasdes-lane-classes-vs-attributes.md) | ras des, per lane, common lane, event counter group, lane select, telemetry attributes, pdr, per link, pciebadtlperrorcount, pcielcrcerrorcount, pcieretrytlpcount, api design, middleware, group 0, group 4 | Verified from spec + FW | 2026-08-07 |
| PCIe AER — FW's job is counting for RAS/manageability, not clearing (`PcieCorrectable/Fatal/NonFatalErrorCount` + thresholds → Critical Chip Status; persisted RAS history; RAS DES can't supply severity split; count off the SEI cause, not the sticky AER status) | [pcie-aer-fw-counters.md](pcie-aer-fw-counters.md) | aer, pcie ep, error counters, pciefatalerrorcount, pcienonfatalerrorcount, critical chip status, ras history, pdr initialization, radm_fatal_err, ras des, sei cause, fw responsibility | Verified from spec | 2026-07-27 |
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
