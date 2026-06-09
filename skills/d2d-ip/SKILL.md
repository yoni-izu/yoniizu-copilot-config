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
   - Also check the project manifest (e.g., `.star` files or `elmcli` manifests) for the actual deployment path
2. If found locally — read directly from the workspace (fastest, no network)
3. If NOT found locally — use GitHub search tools to query the remote repos above

## Procedure

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
