---
name: d2d-ip
description: 'Answer D2D IP design, usage, and integration questions using the internal D2D specification, with RTL questions answered by Neo (neo-ask). Use when: die-to-die, D2D interface, D2D link, D2D protocol, D2D PHY, multi-die, chiplet, inter-die communication, D2D registers, D2D specification.'
argument-hint: 'Ask a question about D2D IP design, protocol, or integration'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# D2D IP Documentation Q&A

## When to Use

- Questions about die-to-die (D2D) interface design and protocol
- D2D link bring-up, training, and state machine behavior
- D2D register configuration
- Multi-die / chiplet interconnect architecture
- D2D PHY and signaling
- Internal D2D specification questions
- RTL implementation questions

## Architecture Context

- **D2D Interface**: Element's proprietary die-to-die interconnect for multi-die (chiplet) configurations
- **UCIe**: Universal Chiplet Interconnect Express — open industry standard for die-to-die and chiplet interconnect, reference for protocol, PHY, and interoperability
- **Purpose**: High-bandwidth, low-latency communication between dies within the same package; UCIe context for cross-vendor compatibility and industry alignment

## Boot Flow Responsibilities

Reference: [Boot flow](https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow) (SWENG space).

FW boots through staged, chained-authenticated images (root of trust starts at FLA.ROM). The MuC-resident runtime stages are **FLA.RT → PRIMEBOOT → MANAGEMENT**; **POWER & THERMAL** runs on the PTuC. Drivers are split into **INIT** (one-time bring-up) and **RUNTIME** (interrupt/operational) components so they can work seamlessly across stages.

D2D responsibilities by stage (per the proposed driver-functionality split):

| Driver / Module | FLA.RT | PRIMEBOOT | MANAGEMENT | POWER & THERMAL |
|-----------------|--------|-----------|------------|-----------------|
| EL D2D          | —      | FULL      | FULL       | —               |
| EL D2D MGR      | —      | FULL      | FULL       | —               |
| EL D2D WRAPPER  | —      | FULL      | FULL       | —               |
| EL AW D2D       | —      | FULL      | FULL       | —               |

- **PRIMEBOOT** is the primary stage for D2D: it initializes the D2D HW IP and **brings up the D2D link**, and loads/authenticates the **UCIe PHY FW** image into the PHY memories (cold reset only). Per the page, "the D2D driver will bring up the D2D link as part of PRIMEBOOT."
- **MANAGEMENT** handles D2D **runtime/interrupts** (D2D interrupts are serviced in both PRIMEBOOT & MANAGEMENT). D2D init code may **also be required in MANAGEMENT** if D2D is reset in a reset where the FW boot flow does not restart.
- D2D is **not** active in FLA.RT or POWER & THERMAL.

> The Init-vs-Runtime split is still evolving: after tape-out (TO) some HW init currently done in PRIMEBOOT may move to MANAGEMENT or POWER & THERMAL. Treat this as the current plan, not a frozen contract.

## Document Catalog

### Git specification sources

Use the [D2D two-repository map and pinned lookup](../../installed-plugins/ai-toolkit/fw-soc/references/specification-sources.md#d2d):
general architecture in `Elmnt-Internal/module.arch.hwarch.specs`, A2 and
verified legacy A1 context in `Elmnt-Internal/project.atlantic2.hwarch.specs`
(`main` for both). The deprecated project repo is retained by explicit approval.
The exact A1 Rev1 original is unverified; historical identity/delta claims below
are secondary, not evidence that the general snapshot is that baseline.
UCIe/Alphawave manuals listed below are historical titles, **unavailable in the
verified Git catalog**. Internal integration specs do not replace vendor data.
Report missing manuals; do not discover or fall back to local document folders.

> **Project note (A2 / Atlantic2 vs A1 / Iridium):** The A2 project repository contains a
> `Rev2.0.0`-named D2D document, but its Word and Markdown covers say `1.0.0` and its
> revision history ends at `1.1.0`. The newer general D2D document's history adds `2.1.0`;
> whether its added idle-traffic requirements apply to A2 is unconfirmed. Do not infer the
> document's revision from its path. `D2D Specification Rev1.docx` was the historical
> A1/Iridium title; a prior comparison reported textual identity apart from captions, but
> its exact A1 original is not verified in the current Git catalog.
> The architect (Maxim Leshin, 2026-08-30) advised keeping the core D2D design as-is and updating
> FW where the new AW IP needs different configuration. That guidance does not rule out changes in
> the wrapper CSR, topology or PHY variants: Neo (2026-11-11) confirmed the A2 D2D PHY is
> N3P only (Alphawave), in EW (side) and NS (mid) orientation variants — no 5nm/2nm D2D PHY; Tn5
> files are unused leftovers (supersedes the earlier "5nm + N3" framing). The user also confirmed D2D owns the D2D sequence in ROM, the D2D
> wrapper CDUP is used, and the D2D driver provides its APIs. The checked A2 CSR contains this
> wrapper block; A1 D2D wrapper CSR does not. The exact PHY/ROM deltas, HSM integration and CDUP
> API consumer remain to be confirmed. For current source reconciliation, including the
> fatal-error, mailbox, gateway and idle-traffic qualifications, see
> [A2 Git specification reconciliation](knowledge/a2-current-spec-contracts.md); it supersedes
> conflicting broad claims in older knowledge notes. For physical-variant evidence see
> [A2 D2D physical variants and wrapper CDUP API](knowledge/a2-physical-variants-and-cdup.md)
> and the `atlantic2-prj` skill. A2 NoC Rev 1.7 describes one horizontal and two vertical
> x32 connections per die, consistent with **12 connected D2D endpoints** across four dies;
> the precise wrapper-to-package mapping still needs confirmation.

### Historical D2D & UCIe document titles

| File | Size | Content |
|------|------|---------|
| `D2D Specification Rev 2.0.0.docx` | ~5.5MB | A2 project-repository document; the filename is not its verified internal revision — protocol, PHY, registers, integration |
| `D2D Specification Rev1.docx` | ~5.5MB | Historical **A1/Iridium** title; exact Git original unverified, not available for a verified revision diff |
| `ucie_1p1_with_legal_disclaimer_july_10th_2023.pdf` | ~7MB | UCIe 1.1 specification — industry standard for Universal Chiplet Interconnect Express (UCIe), relevant for D2D/Chiplet/Inter-die standards, protocol, and PHY |
| `ucie_1p1.txt` | ~0.8MB | Searchable plain-text extraction of the UCIe 1.1 PDF (grep-friendly) |

### AlphaWave IP vendor authorities (not available in the verified Git catalog)

AlphaWave (Alphawave Semi) supplies Element's licensed D2D/UCIe silicon IP. The stack has three
layers: **AresCORE** (the UCIe D2D **PHY** IP) → **GammaCORE** (the UCIe D2D **controller** IP) →
**GammaCORE32** (the packaged **1×S32 subsystem** = controller + PHY, 32 lanes). In Element FW
terms these map to the **EL AW D2D** driver family; the "UCIe PHY FW image" loaded/authenticated
during PRIMEBOOT is the **AresCORE PHY** firmware.

| File | Size | Content |
|------|------|---------|
| `arco_arescore_ucie_datasheet_n5_base_0_8_7_element.pdf` | ~6.1MB | **(A1 / N5 — not A2; A2 is N3P)** **AresCORE™ UCIe D2D PHY IP** — 5nm Base Datasheet, v0.1.0_0.8.7 (Sep 2024, NDA for Element). PHY layer: TX/RX/common-lane configurability, SerDes, supported standards, electricals |
| `Eland_gammaN64B1Tn5_ss_a_controller_100_051.pdf` | ~2.9MB | **GammaCORE™ UCIe D2D Controller IP** — Base Datasheet (Eland_S variant), v1.0.0_0.5.1 (Oct 2025). Controller layer: streaming protocol, packing/mux, TX/RX buffers, flit gen/termination, end-to-end credit-based flow control, AXI4/FDI |
| `Eland_gammaN64B1Tn5_ss_a_datasheet_100_053.pdf` | ~1.2MB | **(A1 / N5 — not A2)** **GammaCORE32™ 1×S32 Subsystem for UCIe** — 5nm Datasheet (Eland_S variant), v1.0.0_0.5.3 (Oct 2025). Subsystem-level: interfaces (APB, FDI, UCIe standard package, iJTAG), integration, miscellaneous signals |
| `Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.pdf` | ~3.1MB | **GammaCORE32 Register Descriptions (MMR)** — Eland variant, v1.0.0_0.5.3 (Oct 2025). Memory-mapped registers: DVSEC1/DVSEC2 (PCIe extended cap, UCIe Link DVSEC, sideband mailbox), GLOBAL_REG, CMN MCU control/IRQ registers, etc. |
| `Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.txt` | ~3.5MB | Historical MMR extraction title, unavailable as verified Git evidence; not an active register lookup source |

> **Vendor docs vs. internal spec:** The exact applicable AlphaWave documentation is the
> authority for **IP-internal register fields, PHY electricals, controller streaming/flit/credit
> behaviour and interface signals**. For **Element-specific integration** (wrapper,
> manageability bridge, multi-die topology, boot-flow responsibilities), consult the pinned A2
> project document and reconcile relevant newer general specifications with their owners. Do not
> infer document precedence or revision from a filename. Record verified reconciliations.
>
> A historical MMR plain-text extraction is not current spec evidence. Until an
> approved Git publication is verified, report vendor register-source gaps
> rather than searching an old local extraction.

## Source Code Repositories

| Repo | GitHub URL |
|------|------------|
| RTL | https://github.com/Elmnt-Internal/ip.d2d.rtl |
| Firmware | https://github.com/Elmnt-Internal/ip.d2d.sw.fw |

> **RTL branch strategy:** In the RTL repo, the **`main`** branch holds the **latest
> implementation for the most recent project** we are working on (currently **Atlantic**).
> Each **previous** project is frozen on its own **freezed RTL** branch — e.g. **Iridium**'s
> RTL lives on the Iridium *freezed rtl* branch, not on `main`. Neo knows both: ask
> with `--chip a1` (Iridium freeze) or `--chip a2` (Atlantic/A2 `main`).

**Source strategy**:
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/d2d/` or `ip/d2d/`
   - Also check the project manifest (e.g., `.star` files or `octl` manifests) for the actual deployment path
2. Read FW and CSR software headers locally, or search their specific software repos on GitHub.
3. For RTL, follow [rtl-vm-policy](../rtl-vm-policy/SKILL.md): ask **Neo** —
   `~/bin/neo-ask --chip a1|a2 "<question>"` — and relay only its paraphrased
   findings and citations (repo, path, symbol, mirror commit), never HDL or
   patches. No local/VM checkout or GitHub RTL reads. If Neo is unavailable,
   report the prerequisite and stop.

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified, so
repeat questions on the same subject are answered fast without re-reading the spec or RTL.

- **Location:** `~/.copilot/skills/d2d-ip/knowledge/`
- **Index:** `~/.copilot/skills/d2d-ip/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

Consult source-free knowledge first when available (Step 0) and update it with
paraphrased findings after non-trivial research (final step).

> **Recording trigger — read this twice.** Record a finding whenever you **verify** a
> non-trivial IP fact during this turn, regardless of *why* you were invoked. The
> trigger is the verification, not the question shape. This fires when:
> - the user asked you directly (the obvious case);
> - another skill (e.g. `pr-review`, `iridium-prj`, `atlantic-prj`, `ras-manageability`) consulted you;
> - you read FW driver code, generated register headers, or RTL during a code review
>   and locked in a durable register/protocol/sequence/erratum fact;
> - you reconciled two sources (spec vs RTL, doc vs FW) and resolved which is current.
>
> **"Non-trivial IP fact"** = anything a future query could re-ask: a register's
> reset/behaviour, a field/bit semantic, a bring-up or training sequencing rule, a
> protocol nuance, a controller/PHY quirk, a cross-IP interaction, a HW–FW contract.
> If in doubt, **record**. A too-detailed knowledge base costs milliseconds; a missing
> entry costs the next query a full re-read of a multi-thousand-page source.
>
> Skip only: pure code-style nits, project-specific FW architecture (belongs in the
> project skill, e.g. `iridium-prj` / `atlantic-prj`), tooling/CI/Python issues, and
> re-statements of facts already fully covered by an existing entry.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Use the topic names in this skill. Consult an index or note only if it is
   already known not to contain HDL excerpts; do not bulk-search the knowledge
   directory, which may contain historical source excerpts.
2. If no verified source-free note is available, proceed using the specification
   or ask Neo (`neo-ask`) for a paraphrased finding.
3. If a recorded finding fully answers the question, use it (cite it) and skip to verifying it
   is still current only if there's reason to doubt it. Otherwise, use it to **narrow** the
   search before diving into the spec/RTL.

### Step 1: Search the Specification

Select the mapped general or A2 D2D Markdown, verify its scope/revision, resolve
a commit and fetch its exact path with `gh api` as shown in the source map.
Search headings, text and tables. Cite repository, commit, path and section/table.
If the exact legacy or licensed manual is missing, report that gap; historical
knowledge can guide research but cannot establish a current specification claim.

### Step 2: Extract and Answer

1. Search with broad keywords first, then narrow to specific sections
2. For register lookups: search tables for register name or offset
3. For protocol/architecture questions: search paragraphs for feature descriptions
4. The docx may have images/diagrams — mention when a diagram would be helpful but cannot be rendered
5. Always cite the paragraph number or table index in your answer
6. If the spec doesn't cover it, ask Neo (`~/bin/neo-ask`) for a paraphrased RTL
   finding; never use GitHub, local or VM-worktree RTL reads.

### Step 3: Record Verified Findings (do this last)

After answering a non-trivial question, persist what you verified so the skill remembers it:

1. Create or update `~/.copilot/skills/d2d-ip/knowledge/<topic-slug>.md` using the template in
   `INDEX.md`. Keep it concise, durable, and reusable.
2. Add or refresh the topic's row in `INDEX.md` (newest at top) with tags, status, and date.
3. Always include citations (spec paragraph/table, RTL repo + file + symbol) so the finding can
   be re-verified and trusted.
4. If new research contradicts a prior entry, **correct** that entry rather than leaving stale
   information.

Skip recording only for trivial lookups already fully covered by an existing entry.

### Search Strategy Tips

- **Registers**: Usually defined in tables — always search both paragraphs AND tables
- **State machines**: Search for "state", "FSM", or specific state names
- **Timing**: Search for "latency", "cycle", "clock", "frequency"
- **Integration**: Search for "port", "signal", "interface", "pin"
- Search Git Markdown headings and tables; verify extraction provenance and revision.

## Important Notes

- Use mapped internal Git specs for integration; vendor internals need the
  missing exact licensed manual, not an internal-spec substitute.
- For cross-die questions that also involve PCIe or C2C, use the `pcie-ep-ip` or `su-ip` skills as well
- Check Markdown table conversion carefully; original merged cells can lose context.
