# A2 (Atlantic2) — D2D Sub-System

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Overview + D2D Rev2 vs Rev1 diff) + **architect confirmation**
- **A2-vs-A1 delta:** **D2D is kept AS-IS for A2** (architect Maxim Leshin, 2026-08-30). Spec Rev2
  == Rev1 (exact copy), **no D2D initiatives**. Only possible FW work = config deltas **if the new
  AlphaWave (AW) IP drop requires a different configuration**.
- **RTL branch/tag:** RTL diff done (mostly "align to a2"). n/a for spec.
- **Tags:** d2d, ucie, phy, aw, alphawave, arescore, gammacore, chiplet, die-to-die, as-is, no initiatives, fw config

## Architect input (AUTHORITATIVE — Maxim Leshin, 2026-08-30)
The FW planner emailed the architect: "I don't see any initiatives open for D2D at
all. Also the Rev 2 specification is an exact copy of Rev 1. What should be done on FW side on D2D
for A2?" Architect reply:
> **"We are keeping the D2D as is. The FW should be updated only if the new AW IP requires a
> different configuration."**

Interpretation:
- **D2D design is unchanged for A2** — no architectural/spec delta, no D2D initiatives.
- **D2D FW = essentially no A2 work**, UNLESS the new **AW (AlphaWave) IP** version ships with a
  different configuration requirement (register defaults, init/config sequence). Action for FW
  planning: **track the AW IP drop; diff its config/register requirements vs the A1 AW IP** — if
  identical, D2D FW is carried over as-is.
- This **supersedes** the earlier "brand-new 3nm PHY for D2D" framing: whatever new AW IP arrives,
  the D2D block itself is kept as-is and FW only reacts to config differences. (The N3 PCIe7 PHY
  seen in RTL is on the **SU/DMP** path, not D2D — see a2-rtl-analysis.md / a2-scaleup-c2c.md.)

## Scope / Question
What changes for D2D in A2 vs A1, from the D2D Rev2 spec, the A2 Overview, and the user's PHY note.

## Findings (A2)
- **A2 D2D = UCIe-based, 12× UCIe 2.0 Standard Package**, BW/dir = 12 × 16b @ 32 Gbps =
  **768 GB/s** (A2 Overview para 128-129). Low-latency controller ~15 ns/direction (D2D spec).
- UCIe stack: Protocol (PCIe / CXL / streaming) → controller → PHY; standard & advanced package;
  UCIe 1.1 & **2.0** enhancements; UCIe-3D; UCIe manageability; UCIe RAS.
- **Two PHY IP versions in A2** (per user, authoritative):
  1. **5nm PHY** — same family as A1 (AlphaWave **AresCORE** PHY / **GammaCORE** controller /
     **GammaCORE32** N5 subsystem). Possibly minor logic changes vs A1.
  2. **Brand-new 3nm (N3) PHY** — NEW for A2. No N3 datasheet in `~/docs/D2D/` yet (only N5 docs:
     `arco_arescore_ucie_datasheet_n5...`, `Eland_gammaN64B1Tn5_...`). Add when available.

## A2 vs A1 (Iridium) delta
- **D2D spec content is near-identical A1→A2** — `D2D Specification Rev 2.0.0.docx` (A2) vs
  `D2D Specification Rev1.docx` (A1) differ only in figure/table caption numbering (verified by
  full paragraph diff: 1016 paras each, no substantive text change).
- **The A2 change is HW/PHY**: add the 3nm PHY; keep the 5nm PHY (maybe minor logic tweaks). FW
  impact = supporting a second PHY variant in the D2D PHY-FW load/auth/init path (the "UCIe PHY FW
  image" is the AresCORE PHY firmware). Confirm against RTL diff + any N3 datasheet.

## Sources
- Doc: `~/docs/A2/Atlantic2 Overview.docx` (D2D 231-235; feature set 127-129).
- Doc: `~/docs/D2D/D2D Specification Rev 2.0.0.docx` (A2) vs `D2D Specification Rev1.docx` (A1) —
  paragraph diff showed no substantive content change.
- PHY node facts: user statement (5nm same-as-A1 + new 3nm). See `d2d-ip` skill for PHY/controller
  datasheets and register maps.
- **RTL (confirmed)**: `ip.d2d.rtl` `iridium...main` — mostly "align to a2" + UCIe link updates;
  **D2D PHY macros are hard-macro stubs** (swap in progress) → D2D logic delta modest, PHY is the
  real change. See a2-rtl-analysis.md.
