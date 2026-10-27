---
name: psoc-ip
description: 'Answer PSOC (Platform/Management SoC block) design, uC cluster, boot/reset, GPIO/straps, eFuse, slow-IO, HSM/Caliptra, and platform-integration questions using the internal PSOC specification and PSOC RTL. Use when: PSOC, platform SoC, management uC (MuC), power/thermal uC (PTuC), reset uC (RuC), tiny uC (TuC), uC cluster, Cortex-M7, boot flow, FLA, secure/unsecure boot, reset manager, reset notification, GPIO/straps, eFuse, slow IOs (SPI/I2C/I3C/UART/AVSBus/SVID/MDIO/PWM/RPM), mailbox, HSM, Caliptra, PUF, crypto (SHA/ECC), remote debug, multi-die, FIU, platform guidelines.'
argument-hint: 'Ask about PSOC — the uC cluster, boot/reset, slow IOs, eFuse/GPIO, HSM/Caliptra, or platform integration'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# PSOC (Platform / Management SoC) IP Expert

## When to Use

- **PSOC** block design and behavior: the platform/management sub-system that owns boot, reset,
  slow IOs, GPIO/straps, eFuse, and the on-die **micro-controller cluster**.
- **Micro-controller cluster**: the **Management uC (MuC)**, **Power/Thermal uC (PTuC)**, **Reset
  uC (RuC)** (all Arm **Cortex-M7**), plus the **Tiny uC (TuC)** sensor/power offload engine —
  their roles, memories, connectivity, reset isolation, and mailbox communication.
- **Boot**: PLL config, fault-injection detection, boot types, **Firmware Loading Agent (FLA)**,
  secure/unsecure boot, unsecure-boot recovery (PCIe Gen1 from ROM).
- **Reset**: reset manager (HW), SW reset flow, reset types/config/notification, gradual reset,
  multi-die reset notification, PSOC sub-system reset.
- **Slow IOs**: SPI, I2C, I3C, UART, AVSBus, SVID, ADC, MDIO/SMI, PWM, RPM, JTAG, RMII.
- **Peripheral engines**: Copy Engine, Duplication (Dup) Engine, local SRAMs, PoR/GP flops,
  write-once regs, TuC, WDT, timers, timestamp, **crypto (SHA-384/256/512, ECC P-384)**, mailbox,
  eFuse controller, transaction drain, error aggregator, TRNG, remote debug (Eth/I2C/SPI),
  HW semaphore.
- **HSM integration**: the **Caliptra** sub-system + **HSM Wrapper** (PUF, WhoAmI, SoC eFuse, FLA,
  isolation, security enforcement, address manipulation).
- **Platform guidelines**: ref clock, SPI/QSPI flash, BMC connectivity, power measurement, temp
  sensors, FRU, PCIe platform wiring, straps, SiP GPIO.
- **PSOC RTL** implementation questions.

> **Scope:** PSOC overlaps with `ras-manageability` (PLDM/MCTP, power/thermal, error handling)
> and with the project skills. Use **`psoc-ip`** for the PSOC *block* itself (uC cluster, boot,
> reset, slow-IO, GPIO/eFuse, HSM). For manageability protocol/RAS content defer to
> `ras-manageability`; for project context / A2-vs-A1 use `atlantic2-prj`.

## Project Context

- The current PSOC spec is the **Atlantic2 (A2)** revision. PSOC hosts the SoC-level FW agents.
  RTL repo: **`Elmnt-Internal/ip.psoc.rtl`** (A1/Iridium = branch `iridium`, A2 = `main`;
  diff `iridium...main` for the A2 delta). SW/FW repo: `ip.psoc.sw.fw`.
- **The three SoC FW agents (Boot&Reset / Management / Power&Thermal from the A2 Overview) are the
  PSOC uC cluster**: MuC, PTuC, RuC (Cortex-M7). Note the A2 Overview lists a "Boot & Reset uC";
  the PSOC spec splits reset into **RuC** and folds secure-boot duties into **MuC**.

## Document Catalog

> **Rule of thumb:** Rev2.x = A2, Rev1.x = A1/Iridium. Diff Rev2 vs Rev1 for the A2 delta.

| File | Path | Content |
|------|------|---------|
| **PSOC Specification Rev 2.0.1** | `~/docs/PSoC/PSOC Specification Rev2.0.1.docx` | **A2** PSOC spec — overview, slow IOs, peripheral engines, boot, reset, GPIO, uC cluster, clock/reset domains, binning, trace/debug, fabric mapping, interrupts, RAS, security, multi-die, platform guidelines, HSM (Caliptra) integration, FIU |
| PSOC Specification Rev 1.2.0 | `~/docs/PSoC/PSOC Specification Rev1.2.0.docx` | **A1/Iridium** baseline (diff for the A1→A2 delta) |
| Cortex-M7 | `Cortex-M7.docx` (referenced) | Arm Cortex-M7 processor details |

### Spec section map (H1)
`Overview` · `Slow IOs` · `Peripheral Engines` · `Boot` · `Reset` · `GPIO` · `Micro Controllers` ·
`Clock and Reset Domain` · `Binning and Clock Gating` · `Trace and Debug` · `Fabric Mapping` ·
`Interrupts` · `Performance` · `RAS` · `Security` · `Multi-Die Implications` · `Platform
Guidelines` · `HSM Integration` · `FIU`.

## Knowledge Base (Expert Memory)

- **Location:** `~/.copilot/skills/psoc-ip/knowledge/`
- **Index:** `~/.copilot/skills/psoc-ip/knowledge/INDEX.md`
- Detail files: `knowledge/<topic-slug>.md` — verified findings with citations.

Consult it first (Step 0); update it after non-trivial research (final step).

## Procedure

### Step 0: Consult the Knowledge Base
Read `knowledge/INDEX.md`; full-text search `knowledge/` for the question's keywords.

### Step 1: Classify → pick the spec section (see map). uC/CPU → "Micro Controllers"; boot →
"Boot"; reset → "Reset"; crypto/eFuse/mailbox → "Peripheral Engines"; HSM → "HSM Integration".

### Step 2: Search the Spec (`python-docx`)
```python
import docx
doc = docx.Document("/home/yoniizu/docs/PSoC/PSOC Specification Rev2.0.1.docx")
for i,p in enumerate(doc.paragraphs):
    if p.style.name.lower().startswith("heading") and p.text.strip():
        print(i, p.style.name, "|", p.text.strip())
# keyword-search paragraphs AND tables (memory sizes / config params live in tables).
```

### Step 3: Search the RTL
```bash
gh api repos/Elmnt-Internal/ip.psoc.rtl/compare/iridium...main --jq '{ahead:.ahead_by, files:(.files|length)}'
gh api --paginate repos/Elmnt-Internal/ip.psoc.rtl/compare/iridium...main --jq '.commits[].commit.message'
```

### Step 4: Answer + cite (spec para/table, RTL repo/branch/commit).

### Step 5: Record verified findings in `knowledge/<slug>.md`; add a row to `INDEX.md` (newest
at top); correct stale entries when contradicted.

### Search Strategy Tips
- **Acronyms**: spec Terminology/Acronyms section near the top (MuC/PTuC/RuC/TuC/FLA/HSM).
- **A2-vs-A1**: diff Rev 2.0.1 vs Rev 1.2.0; for implementation reality diff `ip.psoc.rtl`
  `iridium..main`.
- **uC memories / config params**: in tables (Table 10-1 uC Memories, 10-2 Cortex-M7 params).
