# PCIe equalization preset readback

- **Recorded:** 2026-11-08
- **Status:** Verified from PCI Express Base Specification 6.3 and Synopsys DM reference
- **Tags:** pcie, equalization, preset, transmitter preset, lane equalization control, debug readback

## Finding

PCIe Base Specification 6.3 defines a per-lane `Upstream Port 32.0 GT/s Transmitter Preset`
field in the 32.0 GT/s Lane Equalization Control entry. When operating as an Upstream Port
with Crosslink Supported=0, the field is read-only and intended for debug/diagnostics. Capture
from 128b/130b EQ TS2 or received equalization requests with `Use_Preset` set is **MUST@FLIT**:
mandatory when the component's Flit Mode Supported bit is set, and strongly recommended
otherwise. This is a capability condition, not the current Link mode. Requests with
`Use_Preset` clear should not affect the capture.

This is a direct register indication of preset-mode requests observed by the USP, not an
unconditional report that the final request was accepted or that the current transmitter is
using that preset. In the normal preset-only FOM flow, the EQ lead re-requests the highest-FOM
preset after evaluating the vector. After successful equalization and acceptance, the capture
should identify that preset, rather than merely the last preset tested. Optional FOM coefficient
requests or later DIR tuning can leave the capture stale. The field is unavailable to software
when Crosslink Supported=1. The Downstream Port's Phase 2 tuning of its own TX is not reported
by this USP-side field. Verify the capture behavior on hardware.

## Sources

- PCI-SIG, `NCB-PCI_Express_Base_6.3.pdf`, p.118 (`MUST@FLIT` definition); §7.7.6.9,
  Table 7-83, p.1160 (32.0 GT/s field description); p.467 (equalization phases and
  accepted-request reflection in TS1).
  Local copy: `/mnt/c/Users/YoniIzutskiver/OneDrive - Element labs/Downloads - BKP/NCB-PCI_Express_Base_6.3.pdf`.
- Synopsys `DWC_pcie_ctl_reference.pdf`, v6.42a-lca01, PL32G Lane Equalization Control
  register (`PL32G_CAP_OFF_iH` fields).
- Synopsys `DWC_pcie_ctl_dm_databook.pdf`, p.839 (final preset request) and p.840-842
  (optional FOM coefficient requests).
