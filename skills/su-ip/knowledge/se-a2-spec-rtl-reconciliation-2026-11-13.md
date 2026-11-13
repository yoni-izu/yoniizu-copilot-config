# A2 SE/C2C specification and RTL reconciliation

- **Recorded:** 2026-11-13
- **Status:** Partial / needs Architecture, RTL and vendor follow-up; A2 RTL evidence is from Neo's 2026-11-09 mirror snapshot
- **Tags:** a2, c2c, cce, noc, specification, rtl discrepancy, npa, mps, bar, descriptor, reset, aggregation, topology, manageability

## Scope / Question

Reconcile the A2 Scale-Up firmware plan against the latest relevant Git specifications and
Neo's A2 RTL findings; preserve distinctions between approved requirements, candidate text,
RTL observations and unverified vendor behavior.

## Findings

1. **Document authority is unresolved for some candidates.** The project C2C DOCX is named
   Rev2.2.0 but its cover says Rev2.1.1 and it contains tracked changes. The NoC 1.7 DOCX has
   a malformed cover and tracked topology/channel-selection insertions. The architecture
   enhancements filename says v1.3 while its cover says 0.1 (June 2026); it conflicts with
   the NoC candidate on TP16 topology. Treat those deltas as candidates until the document
   owners confirm authority.
2. **NPA management reachability:** the C2C management macro window ends at 0x8000 exclusive;
   the C2C-local decoder lists the NPA table at 0x8000–0x9fff. Neo traced the canonical path
   sending that range to the DM/DMP side before the local NPA decoder. The full top-level
   NoC/FIU route and DM-side response were not traced. Confirm the real programming path before
   firmware depends on the table.
3. **A2/GPU NPA layout:** candidate C2C describes separate 2048-entry A2 and 1024-entry GPU
   tables. Neo observed one shared logical 2048-entry A2/GPU table; same-index mappings share
   an entry. Resolve table allocation and the physical/CSR contract before defining population.
4. **INC selector:** candidate C2C describes selector bits [53:51]; Neo traced three
   programmable selectors sourcing address bits [47:40]. Confirm the address format at the
   integration boundary; do not program based on either interpretation alone.
5. **Local-sled sentinel:** aligner/CCE generate 11-bit 0x7ff; the traced SE RS DMT-allocation
   check examines only eight bits at a configured offset. Other indices ending in 0xff may
   match. The offset and full fabric path remain unverified; test local and remote cases.
6. **AG-MC descriptor:** candidate C2C specifies an 80-bit descriptor with multicast, VCM and
   DIST fields at [79:64], bit 63 and bit 62. Neo reported an RTL/verification layout mismatch,
   including possible loss of the upper two multicast bits at a hand-off. This is a static
   finding, not reproduced. Agree the wire layout and test high bits, DIST/VCM, segmentation
   and 4-KB crossings.
7. **MPS and AG split:** 1024 B is a build-time datapath/accumulator constant, not negotiated
   MPS. Runtime MPS is exposed by the DM wrapper but does not automatically drive the separate
   C2C posted-TLP size limit. Firmware policy must coordinate MPS, that limit and AG split.
   Candidate minimum MPS values for 128/256/512-B payload chunks are 256/512/1024 B,
   respectively. Mode 11 terminates; exact response/capture behavior is unverified.
8. **BAR aperture:** the observed 38-bit DM BAR0/BAR2 masks are programmable reset defaults,
   not proof of a fixed limit. Candidate C2C describes a 48-bit window; integrated DWC DBI
   support, FW programming and read-back remain unverified. Confirm and exercise the aperture.
9. **Aggregation and Requester IDs:** candidate C2C specifies aggregation behavior by compliant
   versus optimized mode and a >255-requester policy. Neo found separate Rx read/write enables,
   no mode gate, no instantiated legacy default-peer CAM in the reviewed RTL, and no proven
   overflow path for indices 256+. Explicitly configure/test modes; establish an owner-approved
   supported limit or RTL/waiver decision.
10. **CCE / INC ownership:** independent INC_RS and INC_AR controls are in the aligner's
    per-ComGroup management-writable table. C2C and CCE also have global gates. Non-INC RS/AR
    uses explicit write-response tunneling; INC RS/AR uses early responses, DMT and EOC/EOB.
    Allocate ContextIDs with the CCE/aligner/orchestration owners; avoid duplicating effort
    between C1.2 and C2.1.
11. **Reset quiescence:** the three C2C idle indications do not prove all work/responses drained;
    an early-reset acknowledgement is not drain completion and may indicate reset notification,
    isolation or timeout. Define quiesce/isolation, timeout and forced-isolation behavior with
    reset owners.
12. **Topology, security and manageability:** the candidate topology documents conflict.
    CDUP channel/role selection is software controlled and requires consistent programming.
    FIU controls are not proven equivalent to candidate C2C range enforcement; preserve the
    approved FIU-only S1.1 and closed S1.2, escalating equivalence/waiver. A1 manageability
    tables do not establish A2 port numbering or the A2 NPA/INC load format.

## Plan impact

Update acceptance criteria and Jira comments for F1.3/F1.6, F2.4/F2.5/F3.2, N2.1, B1.3/B1.4,
R1.4, A1.1/A1.3, A2.1/A2.2, D1.2/D2.1, C1.2/C2.1/C2.2, S1.1, X1.1 and M2.2/M2.3.
Do not increase estimates or create stories until the external contracts and resulting scope
are confirmed. Keep the existing 193.0 pd / 216.5 pd planning baseline explicitly historical.

## Sources

- Project C2C candidate: `Elmnt-Internal/project.atlantic2.hwarch.specs@15bd28840a84cc265db7b8c3de745775be800b2d`,
  `raw-specs/PCIe_C2C_Rev_2.2.0.docx`, §7.3.2, Tables 71, 76–77 and §7.3.7; cover says
  Rev2.1.1, tracked changes present.
- Project CCE: same pinned repository commit, `raw-specs/CCE_Specification_Rev_2.2.0.docx`.
- Project NoC 1.7 candidate and architecture enhancements: same pinned repository commit,
  `raw-specs/Atlantic2_NoC_Specification_Rev_1_7_0.docx` and
  `raw-specs/A2_Arch_Enhancments_v1.3.docx`; candidate status and cover inconsistencies above.
- Neo A2 reports, mirror snapshot 2026-11-09: `ip.se.rtl@33cbdc1`, `ip.cce.rtl@9bc720d`,
  `ip.noc.rtl@201b377`, `ip.infra.rtl.shared.atlantic2@9bc4f71`; paraphrased findings only.
- Current story mapping: [A2_SE_WORKPLAN.md](/home/yoniizu/A2_SE_WORKPLAN.md).

## Limitations

Generated C2C CSR definitions, top-level MNG routing, some fabric paths, exact DWC DBI
semantics and the current live RTL were not verified. The exact A2 RAS specification and
Synopsys DM/PHY manuals were unavailable in the mapped Git catalog. DOCX extracts include
tracked insertions and do not establish approval.
