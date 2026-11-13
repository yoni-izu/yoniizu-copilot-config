# A2 C2C spec 2.1.1 vs RTL gaps and HW not covered by FW plan

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, c2c spec, gaps, security ranges, sw-rx bar, message capture, ecam, cic, checksum, encryption, traffic gen, termination, ect, uncovered hw

## Scope / Question

Which C2C 2.1.1 spec features are absent from the A2 RTL, and which RTL features have no FW owner?

## Findings

- Spec features **absent or stubbed** in the RTL (ip.se.rtl 33cbdc1): security ranges, SW-Rx BAR, message capture (stub), SBSA ECAM (param 0), indirect TLP gateway, CIC, E2E checksum, link encryption (key DB stub), and OOB ownership.
- RTL features with **no FW story** in the original A2 SE plan: LTSSM logger, debug funnel (DBG view), traffic gen, memory ECC/injection gateway, AXI termination blocks, DMP EQ/termination tables/retimers/rescal, ECT, clock gating, early-reset notify, fork/arb weights, flag-aggregation interrupt, whoami pins, PLL-lock/refclk-detect status, p2p_mode-driven defaults, and dpa_to_npa masks.
- Note: the CSR mirror (generated se.csr) is missing on Neo, and the C2C spec and Synopsys docs are not on Neo. Offsets cited from the uarch doc must be re-checked against the generated CSR.

## Specification reconciliation update (2026-11-13)

The latest C2C DOCX candidate is named Rev2.2.0 but its cover says Rev2.1.1 and it contains
tracked changes. The NoC Rev1.7 DOCX has a malformed cover and tracked topology/channel-selection
insertions; the architecture-enhancements filename says v1.3 while its cover says 0.1 (June
2026) and its TP16 topology conflicts with the NoC candidate. Treat these as candidate text,
not approved requirements, until their owners confirm authority.

Additional high-impact items from the spec/RTL reconciliation:

- The canonical C2C MNG path appears not to reach the NPA table at 0x8000–0x9fff because the
  per-C2C management window ends at 0x8000 exclusive. Top-level NoC/FIU routing and DM-side
  response remain untraced; verify an actual FW programming route.
- Candidate A2/GPU tables are described as separate, but the reviewed RTL uses one shared
  logical 2048-entry table. Candidate INC selector bits [53:51] differ from RTL's three
  programmable selectors sourced from [47:40].
- Candidate AG-MC descriptor layout differs from the RTL/verification layout; possible loss
  of the top two multicast bits at a hand-off is a static finding, not reproduced. Agree the
  wire format and test high bits, DIST/VCM, segmentation and 4-KB crossings.
- The 1024-B datapath constant is not negotiated MPS; runtime MPS does not automatically drive
  the separate C2C posted-TLP size limit. The observed 38-bit BAR mask is a programmable reset
  default, not a proven fixed limit. Candidate 48-bit operation remains unverified at DWC DBI.
- The 11-bit local-sled sentinel 0x7ff meets an 8-bit DMT-allocation check; other indices with
  low eight bits 0xff may also match. Reset idle bits/early-reset acknowledgement do not prove
  full quiescence. Rx aggregation is not automatically gated by compliant/optimized mode, and
  >255 Requester-ID overflow behavior is not established.
- Independent INC_RS/INC_AR controls are upstream per ComGroup in the aligner. Non-INC RS/AR
  uses explicit ACK tunneling; INC RS/AR uses early responses, DMT and EOC/EOB. CDUP role and
  channel choice are software controlled and depend on the approved topology.

See `se-a2-spec-rtl-reconciliation-2026-11-13.md` for affected stories, provenance and detailed
uncertainties. Do not silently add FW scope, increase estimates, or treat a spec/RTL gap as an
approved RTL change.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
