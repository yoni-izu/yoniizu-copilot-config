# PHY BER counters (DFT_5/DFT_6) are test-mode only — no operational CDR error counter

- **Recorded:** 2026-07-02
- **Status:** Verified from doc (PHY databook + reference)
- **Tags:** cdr, ber, bit_error_count, dft_5, dft_6, bermon, bert, pattern matcher, prbs, lfsr, test mode, loopback, CDRErrorsPerLane, redfish, mission mode, rx margining

## Scope / Question
Does the DWC PCIe6 PHY have a per-lane CDR error count (e.g. for a Redfish `CDRErrorsPerLane`
telemetry attribute)? Can `DFT_5`/`DFT_6::bit_error_count` be used in operational/mission mode?
(SNPS support case 02034236; Ziv proposed DFT_5/DFT_6 as CDR-quality inference registers.)

## Findings
- **No dedicated CDR error counter exists.** CDR is an analog RX function that recovers the clock
  and tolerates jitter within PCIe6 spec (150 fs RMS). It has no mislock/error counter. CDR
  mislock only *manifests* as RX bit errors; nothing attributes errors to CDR itself.
- `DFT_5` (offset 0x0 + j*0x800 + 0x120): 32-bit `bit_error_count`.
  `DFT_6` (offset ...+0x124): `[7:0] bit_error_count`, `[15:8] ber_ref_timer_snap`,
  `[23:16] first_bit_error_count`, `[31:24] RESERVED`. The full counter is
  `DFT[6:5]::bit_error_count[39:0]` (40-bit, saturates at 0xFF_FFFF_FFFF). All are `R`,
  reset 0x0, in the **RXS DFT (Design-For-Test) block** ("Register defining various test mode setting").
- **These are the BERT pattern-matcher counter — test mode only.** The BERT matcher generates an
  *expected* LFSR/PRBS pattern (LFSR7/15/23/31, PCIe LFSR23, 52UI jitter, 256b custom) and
  synchronizes to an incoming copy of that same known pattern, counting deviations. Mission-mode
  traffic is real scrambled PCIe data (not a fixed PRBS), so the matcher can't lock and the count
  is meaningless.
- BER measurement is a deliberate session: boot µC → PMD power-up → `DFT_1::pattern_sel` →
  `ber_ref_timer` (`DFT_2`/`DFT_3`) → `DFT_1::ber_en=1` → wait `ber_seed_verif_status` →
  assert `DFT_1::read_mode_en` → read `DFT[6:5]::bit_error_count`. Runs in loopback or against a
  partner sending PRBS (LTSSM in Loopback, not L0). BER = bit_error_count / (128 * ber_ref_timer).
- **Explicitly incompatible with live datapath:** "the local Protocol PCS triggers RX Data
  Recovery and disrupts the RX serial data seen by the BERT pattern checker" — mission-mode PCS
  and BERT checker are mutually exclusive.
- **Ziv's answer verdict:** register identification correct, but "typically used to infer CDR
  quality per lane" is misleading — cannot be polled during operational traffic.
- **What exists in mission mode instead:** CDR-induced errors surface as PCIe-level errors
  (LCRC/framing → replay; controller-side RAS DES event counters). PHY also supports
  non-destructive per-lane RX margining (databook §5.2.3) during L0 — an eye-margin probe, not a
  free-running CDR/BER counter.

## Sources
- Doc: `phy/dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` p.277 (DFT_5), p.278 (DFT_6)
- Doc: `phy/dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` §5.1.12 BERMON p.109-110,
  §5.2.2 BERT p.115-118, p.114 note (PCS disrupts BERT checker), §5.2.3 RX Margining p.118
- Context: SNPS support case 02034236 (CDR errors); PHY FW answer re: 150 fs RMS jitter
