# SuSurpriseLinkDown — what the manageability specs define

- **Recorded:** 2026-11-04
- **Status:** Verified from spec documents (effective markup, tracked insertions included)
- **Tags:** susurpriselinkdown, surprise down, surprise link down, sulinkdown, scale-up, chip health, joint state and event, pldm state sensor, attribute 111, aer

## Findings
- **A1 Manageability Spec v0.6**, Table 54 "Chip Health, Fault and Reliability Status" (docx tbl idx 53, row 13):
  `SuSurpriseLinkDown` — "Indicates that one or more scale-up interconnect links **encountered** a surprise link down."
  Type: **Joint state and event for all ports**. That means one aggregated state sensor for all 32 ports, plus an event.
- The spec does **not** define:
  - when the state clears (latched, clear-on-read, or MC clear), or what it is relative to (since reset or now);
  - any counter. Unlike its siblings `SuUncorrectable{NonFatal,Fatal}Error`, the row does not say "generated together with increment of a numeric counter".
- The Scale-up Ports Telemetry table (tbl idx 61) has **no per-port surprise-down attribute**. Per-port items are `SuLinkStatusL<x>` (current LinkUp/LinkDown/Training state), error counters, and `SuRecovery[Failed]ErrorCountL<x>`. FW attrs 931–962 `su_surp_link_down_error_status_l_*` are therefore not an A1 spec contract.
- **Device Manageability Spec** (internal), tbl 207 row 13 has the predecessor `SuLinkDown` — "links are **down or inactive**" (current-state meaning). A1 renamed it to the surprise-event meaning. Current link state belongs to `SuLinkStatusL<x>`.
- Neither spec has a PCIe EP surprise-down attribute. Surprise Down is an AER uncorrectable error (PCIe default severity: Fatal), so it may also count toward `SuFatalErrorCountL<x>` / `SuUncorrectableFatalError`. The spec does not say this explicitly; this is an inference.
- HW side: there is no surprise-down counter in the DM controller. See su-ip `dm-surprise-down-reporting.md`.

## Sources
- Historical secondary A1 source: `A1 Manageability Specification.docx`, tbl 53 rows 0–16 and tbl 61; paragraph 4283–4284. Exact approved Git original unverified.
- Historical secondary DMS source: `Device Manageability Specification.docx`, tbl 207 row 13 and tbl 215. Exact approved Git original unverified; mapped Chip Management / Platform Manageability are not proven equivalents.
