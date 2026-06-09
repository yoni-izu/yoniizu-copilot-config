---
name: ras-manageability
description: 'Answer RAS, manageability, error handling, telemetry, PLDM/MCTP, power/thermal, and FW responsibility questions using internal RAS and Device Manageability specifications. Use when: RAS, reliability availability serviceability, error handling, error containment, viral, poison, ECC, error logging, crash dump, manageability, PLDM, MCTP, SPDM, SPI management, streaming boot, mailbox, telemetry, health monitoring, power thermal loop, throttling, TDP, EDP, margin tool, FW update, flashless, device initialization, Module Alert, MC communication, PDR, sensor, effecter, error injection, FRU, OOB, OEM commands, device bring-up.'
argument-hint: 'Ask a question about RAS design, manageability protocol, error handling, or FW responsibilities'
---

# RAS & Device Manageability Skill

## When to Use

- Questions about Reliability, Availability, Serviceability (RAS) architecture
- Error classification (SDC vs DUE), detection, correction, containment, propagation
- Viral and Poison error handling flows
- ECC protection (SRAM, HBM), datapath checksum, CRC
- Error logging: volatile and non-volatile error logs, crash dump
- HBM row remapping, post-package repair (PPR), page offlining
- RAS engine: predictive failure analysis, self-test (MBIST/LBIST/FBIST)
- Device manageability protocols: PLDM, MCTP, SPDM over SPI
- SPI management communication: physical layer, command set, mailbox protocol
- Streaming boot flow and flashless mode operation
- Device initialization: location/connectivity, RAS history, configuration
- Power/thermal loop: TDP, EDP, OCW, throttling, rate limiters, thermal sensors
- Telemetry and health monitoring attributes
- PLDM sensors/effecters/PDRs, event logging, file transfer
- Margin tools (PCIe SU, PCIe EP, D2D)
- FW update flow (PLDM for FW Update DSP0267)
- Platform manageability: Module Alert#, CPLD, MC behavior
- Error injection mechanisms
- FW responsibilities for each RAS/manageability feature

## Architecture Context

- **SoC**: A1 accelerator device with multi-die architecture
- **Management Controller (MC)**: External entity (e.g., BMC) communicating OOB via SPI
- **FW**: Runs on on-die micro-controllers (FLA, MuC, PTuC) under Zephyr RTOS
- **PLDM over MCTP over SPI**: Primary OOB management protocol stack
- **In-band**: Management Library (ML) and SMI over PCIe
- **Key FW responsibilities**: Error handling/logging, PLDM command serving, telemetry aggregation, power/thermal control, device initialization, FW update, margin tools

## Document Catalog

### RAS & Manageability Documents (`~/docs/RAS/`)

| File | Content |
|------|---------|
| `RAS Specification.docx` | RAS architecture: error classification, detection, correction, containment, viral/poison, ECC, error logging, crash dump, RAS engine, datapath protection, SoC guidelines, IP-specific RAS (PCIe, NIC, HBM, D2D, MMU, GPU) |
| `Device Manageability Specification.docx` | Full manageability spec: PLDM/MCTP/SPDM protocols, SPI communication, streaming boot, mailbox protocol, device initialization, flashless mode, power/thermal loop, telemetry attributes, margin tools, FW update, platform manageability, OEM commands |

## Key Domain Knowledge

### RAS Error Hierarchy

1. **SDC** (Silent Data Corruption) — undetected error, worst case
2. **DUE** (Detectable Uncorrectable Error) — detected but cannot be corrected
3. **Correctable** — detected and corrected (e.g., single-bit ECC)

### Error Handling Chain

```
Detection → Logging → Containment → Communication → Recovery
```

- **Detection**: ECC, parity, CRC, protocol checks
- **Logging**: Volatile (cleared on reset) + non-volatile (SPI flash or MC in flashless mode)
- **Containment**: Engine halt, Poison propagation, Viral assertion
- **Communication**: Interrupts → FW → BMC/Host (via PLDM or GPIO)
- **Recovery**: Device reset, row remapping, page offlining, RMA

### Poison vs Viral

- **Poison**: Data-associated error. Set on AWUSER/RUSER bus. Consumer can handle it.
- **Viral**: Fatal, uncontained error. Propagates to external interfaces (PCIe, NIC). Requires device reset.
- **Rule**: Data-path errors → Poison. Control-path errors → Viral.

### Error Log Structure (Non-volatile, SPI Flash)

- 32KB per error type (SRAM / per-HBM)
- 1024 entries for uncorrectable errors
- 3072 entries for correctable errors
- Correctable errors escalate after N occurrences (default 8)
- Full uncorrectable log → RMA

### SPI Management Communication Stack

```
MC ←→ SPI Physical ←→ Mailbox Transport ←→ PLDM/MCTP Protocol ←→ A1 FW
```

- SPI Mode 0, up to 12.5 MHz
- Commands: Write, Write-Bulk, Get-Status, Read-Req, Read-Bulk-Req, Read-Data
- CRC protection on all commands
- Two independent mailboxes: REQ (MC→SoC) and RSP (SoC→MC)

### Device Bring-up Sequence

1. **FW Loading** — from Boot SPI flash or Streaming Boot over SPI
2. **Manageability init** — MNG_MBX_INIT, handshake, API version check
3. **Device initialization** — NPA tables, BDF tables, RAS history (flashless)
4. **Device configuration** — thresholds, power/thermal limits
5. **Boot end handshake** — MC sends 'Go', device confirms ready

### Power/Thermal Loop

- **PTuC** runs closed-loop control autonomously
- Reads: DTS (on-die thermal), EA100 sensors, VR power/current
- Controls: Token-bucket rate limiters on VCore and VME
- Thresholds: TDP (sustained), EDP (burst up to 1500W/100µs), OCW
- Failsafe: PWRBRK# → PLL ref-clock fallback (~100ns response)

### PLDM Entity Model

- **Terminus** — communication endpoint (TID)
- **Sensors** — Numeric (temperature, power) and State (health, errors)
- **Effecters** — Numeric (set TDP limit) and State (trigger operations)
- **PDRs** — Platform Description Records describing all entities
- **Events** — Health state changes, polled via PollForPlatformEventMessage

### Flashless Mode

- MC acts as persistent storage for all RAS history, error logs, configuration
- MC must reload RAS history to device during every boot via OemDataLoad
- Error logs stored in SP-SRAM (persistent across reset, not power cycle)
- Crash dumps accessible via direct SPI access or PLDM file transfer

### OEM PLDM Commands (Type 0x3F)

| Cmd | Name | Purpose |
|-----|------|---------|
| 0x01 | OemWriteReg | MC writes any chip register (debug/test only, with OemMcOwnership) |
| 0x02 | OemReadReg | MC reads any chip register |
| 0x20 | OemMcOwnership | MC takes/returns ownership of a subsystem |
| 0x30 | OemDataLoad | MC loads bulk data: NPA tables, BDF tables, RAS SRAM errors |

### Manageability Data Groups

| Group | Key Attributes |
|-------|---------------|
| Device Information | PCI IDs, board info, FW versions, placement |
| Device Status | Boot progress, power mode, utilization |
| Health & Reliability | HealthState, CriticalChipStatus, fault flags |
| Thermal | On-die temp, VR temp, board temp, thresholds |
| Power | Instantaneous/avg/max power, TDP, EDP, energy |
| Throttling | Cause, duration, frequency band percentages |
| Errors | Correctable/uncorrectable counters per subsystem |
| PCIe EP | Link status, speed, width, AER counters |
| Scale-up Ports | Link status, BW utilization, error stats |
| D2D | Aggregated link reliability and utilization |
| Compute | Engine utilization, operational states |
| Error Injection | Memory, PCIe EP, SU port injection controls |

### FW Responsibilities Summary

| Area | FW Role |
|------|---------|
| Error Detection | Handle interrupts, read error status CSRs |
| Error Logging | Write to volatile/non-volatile error log, manage escalation |
| Error Containment | Engine halt, assert Viral GPIO, Poison propagation control |
| Error Communication | PLDM events to MC, interrupt to host, GPIO assertion |
| Crash Dump | Save device state to SP-SRAM before reset |
| PLDM Serving | Parse MCTP/PLDM requests, serve sensor/effecter/PDR/file commands |
| Device Init | Apply NPA/BDF tables, load RAS history, configure thresholds |
| Power/Thermal | Run control loop, read sensors, apply rate limiting |
| FW Update | Handle DSP0267 FW update FSM, authenticate images |
| Margin Tools | Execute margin measurements, expose results via PLDM |
| Platform | Module Alert# handling, MC reboot recovery |

## Procedure

### Step 1: Search the Specifications

Both specs are `.docx` files. Use `python-docx` to search and extract content.

#### Keyword search across all paragraphs (RAS spec):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/RAS/RAS Specification.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i}: {para.text[:300]}")
```

#### Keyword search (Manageability spec):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/RAS/Device Manageability Specification.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i}: {para.text[:300]}")
```

#### Extract a range of paragraphs for context:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/RAS/<spec_file>")
for i in range(START, END):
    if i < len(doc.paragraphs):
        print(f"[{i}] {doc.paragraphs[i].text}")
```

#### Search tables (telemetry attributes, registers, PLDM commands are in tables):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/RAS/<spec_file>")
for t_idx, table in enumerate(doc.tables):
    for r_idx, row in enumerate(table.rows):
        row_text = " | ".join(cell.text.strip() for cell in row.cells)
        if "<keyword>" in row_text.lower():
            print(f"Table {t_idx}, Row {r_idx}: {row_text}")
```

### Step 2: Choose the Right Document

| Question About | Search In |
|---------------|-----------|
| Error types, ECC, Poison, Viral, containment | RAS Specification |
| Error log structure, crash dump, SRAM/HBM errors | RAS Specification |
| RAS per-IP (PCIe, NIC, HBM, D2D, MMU, GPU) | RAS Specification |
| SoC RAS guidelines, data path protection | RAS Specification |
| PLDM, MCTP, SPDM protocol details | Device Manageability Specification |
| SPI communication, mailbox, command set | Device Manageability Specification |
| Streaming boot, flashless mode | Device Manageability Specification |
| Device initialization, configuration | Device Manageability Specification |
| Power/thermal loop, throttling, TDP/EDP | Device Manageability Specification |
| Telemetry attributes, health monitoring | Device Manageability Specification |
| FW update (DSP0267) | Device Manageability Specification |
| Margin tools | Device Manageability Specification |
| Platform manageability, Module Alert# | Device Manageability Specification |
| OEM PLDM commands | Device Manageability Specification |
| FW responsibilities (cross-cutting) | Both documents |

### Step 3: Extract and Answer

1. Search with broad keywords first, then narrow to specific sections
2. For telemetry attributes: search tables in the Manageability spec (Tables 16-1 through 16-15)
3. For protocol flows: search paragraphs for step-by-step descriptions
4. The docx files contain diagrams — mention when a diagram would be helpful but cannot be rendered
5. Always cite the document name and paragraph/table index in your answer
6. For FW implementation questions, also check relevant FW repos:
   - Infra FW: `module.infra.sw.fw.zephyr`
   - PCIe EP FW: `ip.pcie.sw.fw.ep`
   - SE FW: `ip.se.sw.csr`

### Search Strategy Tips

- **Error flows**: Search RAS spec for "error", "viral", "poison", "containment", "halt"
- **PLDM commands**: Search Manageability spec tables for command names or codes
- **Telemetry**: Search Manageability spec for attribute names or group names
- **Boot flow**: Search for "initialization", "streaming boot", "bring up", "handshake"
- **Power/thermal**: Search for "TDP", "EDP", "throttl", "thermal", "power", "rate limit"
- **Flashless**: Search for "flashless", "non-volatile", "MC responsibility", "history"
- **Margin tools**: Search for "margin", "eye", "BER", "PRBS"
- The docx format preserves heading hierarchy — check `para.style.name` for section structure

## Important Notes

- The RAS spec is the authoritative source for error handling architecture
- The Manageability spec is the authoritative source for MC communication and FW-facing protocol
- For cross-domain questions (e.g., "how does a PCIe ECC error reach the MC?"), trace through both docs:
  RAS spec (detection → containment → interrupt) → Manageability spec (FW → PLDM event → MC)
- Register-level details may also be in IP-specific specs — use `pcie-ep-ip`, `su-ip`, or `d2d-ip` skills
- The Manageability spec has ~59 tables with detailed attribute definitions — always search tables for data questions
