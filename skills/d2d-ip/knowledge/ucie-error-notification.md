# UCIe Error Notification / Reporting Mechanism

- **Recorded:** 2026-06-16
- **Status:** Verified from spec (UCIe 1.1)
- **Tags:** ucie, error, notification, reporting, escalation, errmsg, sideband, linkerror, rdi, fdi, viral, dvsec, correctable, non-fatal, fatal, uie

## Scope / Question
How UCIe notifies/reports/escalates errors: classes, layer-to-layer signaling,
cross-die sideband messages, LinkError containment, and SW-visible registers.

## Findings

### Error classes
- Correctable, Uncorrectable Non-Fatal, Uncorrectable Fatal.
- UIE = Uncorrectable Internal Error (p1190). CRC/framing errors detected by Protocol
  Layer are typically treated as UIE and force RDI to LinkError (p2574, p10519-10520).

### Layer-to-layer escalation (RDI: PHY <-> Adapter)
Physical Layer -> Adapter status/error signals (Ch 8 RDI):
- `pl_cerror`  — correctable error, no data-path impact, no retrain. Adapter ORs with
  pl_error for Correctable Error Logging.
- `pl_error`   — recoverable framing error; triggers Retrain; Flit discarded + retry.
- `pl_nferror` — non-fatal error detected.
- `pl_trainerror` — fatal error; PHY transitions to LinkError; escalate to upper layers.
- `lp_linkerror` (Adapter->PHY) — forces PHY to LinkError and hold while =1; decoupled
  from normal state transitions for fastest containment (e.g. viral escalation maps to
  LinkError). Adapter ORs internal error conditions with FDI lp_linkerror from Protocol Layer.
- `pl_state_sts = 1010b` is the LinkError state encoding.

### Cross-die notification — sideband {ErrMsg}
- `{ErrMsg}` msgcode **09h** (message without data) — error reporting/escalation FROM the
  remote Link partner; sent by Retimer or Device die to the Host; destination = D2D Adapter.
  Msgsubcode: 00h = Correctable, 01h = Non-Fatal, 02h = Fatal (p9681-9686, p9524).
- Adapter status bits latch receipt: Sideband Fatal/Non-Fatal/Correctable Error Message
  received (p12001-12114) with Mask + Severity companion registers.
- `{LinkMgmt.RDI.*.LinkError}` msgcode 0Ah — RDI SMs coordinate LinkError transition with
  remote partner over sideband (p9549/9576, p3674-3679). RDI must be in LinkError before
  Adapter LSM, which must precede vLSMs (CXL). LinkError takes priority over LinkReset/Disabled.

### SW-visible registers (PCIe/CXL-style, in UCIe Link DVSEC + D2D/PHY block)
- **UCIe Link Status (Offset, Table 7-10):** bit19 Detected correctable error (RW1CS),
  bit20 Uncorrectable Non-Fatal (RW1CS), bit21 Uncorrectable Fatal (RW1CS).
- **Error Notification Control (Offset 1Ah, Table 7-12):** per-class enables. NOTE: only
  controls *propagation*, not the Status bits. Three reporting paths selectable per class:
  1. Protocol-layer mechanism (RP/DSP maps to PCIe/CXL Correctable/Uncorrectable Internal Error),
  2. UCIe Link Error Interrupt (RP/DSP),
  3. Sideband error message (EP/USP, or retimer-targeted). Behavior differs by role
     (RP/DSP vs EP/USP vs retimer).
- **Link Event Notification Control (Offset 18h, Table 7-11):** link status changed / HW
  autonomous BW changed events + MSI/MSI-X vector selection. UCIe gen-1 = max 2 interrupt vectors.
- **Uncorrectable Error Status (10h) / Mask (14h) / Severity (18h)** in D2D/PHY block
  (Tables 7-27/28/29) — classic status/mask/severity triplet for uncorrectable errors.
- Runtime link testing parity errors are signaled as Correctable errors; SW can enable
  interrupt on correctable errors (p3931-3932, p11207-11209).

### Containment
- Viral containment maps to LinkError state (p1933, p14004). lp_linkerror exists to give
  the quickest path to error containment.

## Sources
- Spec: `~/docs/D2D/ucie_1p1_with_legal_disclaimer_july_10th_2023.pdf` (UCIe 1.1, 2023-07-10)
  - {ErrMsg} encoding: Table 6-8 (~p9524, p9681-9686)
  - RDI error signals pl_cerror/pl_error/pl_nferror/pl_trainerror/lp_linkerror: Chapter 8 RDI
    signal list (~txt lines 13998-14060)
  - LinkError coordination: §8.3.x (~txt 3674-3679, 9549/9576)
  - Error Notification Control Table 7-12 / Link Status Table 7-10 / Link Event Notif 7-11:
    §7.5.1.6-7.5.1.8 (~txt 11440-11640)
  - Uncorrectable Error Status/Mask/Severity Tables 7-27/28/29: §7.5.3.2-7.5.3.4
  - Adapter sideband ErrMsg-received status/mask/severity: ~txt 12001-12114
- Cached plaintext: `~/docs/D2D/ucie_1p1.txt`
