---
name: d2d-ip
description: 'Answer D2D IP design, usage, and integration questions using internal D2D specification and RTL. Use when: die-to-die, D2D interface, D2D link, D2D protocol, D2D PHY, multi-die, chiplet, inter-die communication, D2D registers, D2D specification.'
argument-hint: 'Ask a question about D2D IP design, protocol, or integration'
---

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

### D2D & UCIe Documents (`~/docs/D2D/`)

| File | Size | Content |
|------|------|---------|
| `D2D Specification Rev 2.0.0.docx` | ~5.5MB | Internal Element D2D specification — protocol, PHY, registers, integration |
| `ucie_1p1_with_legal_disclaimer_july_10th_2023.pdf` | ~7MB | UCIe 1.1 specification — industry standard for Universal Chiplet Interconnect Express (UCIe), relevant for D2D/Chiplet/Inter-die standards, protocol, and PHY |

## Source Code Repositories

| Repo | GitHub URL |
|------|------------|
| RTL | https://github.com/Elmnt-Internal/ip.d2d.rtl |
| Firmware | https://github.com/Elmnt-Internal/ip.d2d.sw.fw |

**Local-first strategy**: These skills are global (user-level). The workspace may or may not contain the source code.
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/d2d/` or `ip/d2d/`
   - Also check the project manifest (e.g., `.star` files or `octl` manifests) for the actual deployment path
2. If found locally — read directly from the workspace (fastest, no network)
3. If NOT found locally — use GitHub search tools to query the remote repos above

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified, so
repeat questions on the same subject are answered fast without re-reading the spec or RTL.

- **Location:** `~/.copilot/skills/d2d-ip/knowledge/`
- **Index:** `~/.copilot/skills/d2d-ip/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

The knowledge base is **always consulted first** (Step 0) and **always updated after**
non-trivial research (final step). This is what makes the skill grow into a true D2D expert
over time.

> **Recording trigger — read this twice.** Record a finding whenever you **verify** a
> non-trivial IP fact during this turn, regardless of *why* you were invoked. The
> trigger is the verification, not the question shape. This fires when:
> - the user asked you directly (the obvious case);
> - another skill (e.g. `pr-review`, `iridium-prj`, `ras-manageability`) consulted you;
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
> Skip only: pure code-style nits, project-specific FW architecture (belongs in
> `iridium-prj`), tooling/CI/Python issues, and re-statements of facts already fully
> covered by an existing entry.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Read `~/.copilot/skills/d2d-ip/knowledge/INDEX.md` and scan the topic table.
2. If the question overlaps a recorded topic, read that detail file. Also full-text search the
   `knowledge/` directory for the question's keywords:
   ```python
   import os, glob
   kb = os.path.expanduser("~/.copilot/skills/d2d-ip/knowledge")
   for f in glob.glob(os.path.join(kb, "*.md")):
       txt = open(f, encoding="utf-8").read()
       if "<keyword>" in txt.lower():
           print(f"== {os.path.basename(f)} ==")
   ```
3. If a recorded finding fully answers the question, use it (cite it) and skip to verifying it
   is still current only if there's reason to doubt it. Otherwise, use it to **narrow** the
   search before diving into the spec/RTL.

### Step 1: Search the Specification

The D2D spec is a `.docx` file. Use `python-docx` to search and extract content.

#### Keyword search across all paragraphs:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/D2D/D2D Specification Rev 2.0.0.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i}: {para.text[:300]}")
```

#### Extract a range of paragraphs for context:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/D2D/D2D Specification Rev 2.0.0.docx")
for i in range(START, END):
    if i < len(doc.paragraphs):
        print(f"[{i}] {doc.paragraphs[i].text}")
```

#### Search tables (register definitions are often in tables):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/D2D/D2D Specification Rev 2.0.0.docx")
for t_idx, table in enumerate(doc.tables):
    for r_idx, row in enumerate(table.rows):
        row_text = " | ".join(cell.text.strip() for cell in row.cells)
        if "<keyword>" in row_text.lower():
            print(f"Table {t_idx}, Row {r_idx}: {row_text}")
```

### Step 2: Extract and Answer

1. Search with broad keywords first, then narrow to specific sections
2. For register lookups: search tables for register name or offset
3. For protocol/architecture questions: search paragraphs for feature descriptions
4. The docx may have images/diagrams — mention when a diagram would be helpful but cannot be rendered
5. Always cite the paragraph number or table index in your answer
6. If the spec doesn't cover it, check RTL via GitHub search tools

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
- The docx format preserves heading hierarchy — check `para.style.name` for section structure

## Important Notes

- This is a single-document IP — the docx is the primary and only spec
- For cross-die questions that also involve PCIe or C2C, use the `pcie-ep-ip` or `su-ip` skills as well
- Register tables in docx can have merged cells — handle gracefully when parsing
