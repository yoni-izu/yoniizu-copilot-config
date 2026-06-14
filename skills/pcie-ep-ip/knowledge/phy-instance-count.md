# PCIe6 PHY hard-macro count + lanes per macro (Iridium, per die)

- **Recorded:** 2026-06-14
- **Status:** Verified from PHY databook + project DTS (mirrors RTL instantiation)
- **Tags:** phy instances, phy macros, per die, lanes per macro, x4, pcie ep, scale up, se, c2c, count

## Scope / Question
- How many PCIe6 PHY IP **hard macros** are instantiated per die in Iridium RTL?
- How many lanes does each PHY macro have?

## Findings

### Lanes per macro
Each PCIe6 PHY hard macro is the Synopsys **`dwc_pcie6phy_g2_tsmc5ff_x4ns`** quad
configuration → **4 lanes per macro (x4)**. The databook Quick Reference (p.18) is explicit:
- "Lane Configurations: **x4**"
- High-speed bumps: `TXP/N[3..0]` and `RXP/N[3..0]` (4 lanes)
- Macro dimensions: 1345.329 µm × 1120.896 µm; "0.37699 mm²/lane"
- The IP family supports 1/2/4-lane variants ("One, two, or four full-duplex PAM-4
  transceivers" — Features list, p.20); the variant integrated in Iridium is the **quad/x4**
  (`_x4ns` suffix).

> Wider links (e.g. host x8) are built at the PCS/controller level by ganging multiple x4
> macros — they are NOT a single wider PHY macro.

### Macros per die (Iridium)
Per die there are **10 PCIe6 PHY hard macros** (all the same Synopsys IP listed above),
split into two roles:

| Role | Macros | Lanes/macro | Lanes total | DT labels |
|------|--------|-------------|-------------|-----------|
| PCIe EP (host link) | **2** | 4 | **8** | `pcie_phy0`, `pcie_phy1` (under `D0_MNG_PCIE_EP_CLS_EP_PPW_{0,1}`) |
| SE / C2C scale-up | **8** | 4 | **32** | `se_phy_p0`..`se_phy_p7` (under `D0_MNG_SE_TOP_{0..7}_DMP`) |
| **Total per die** | **10** | 4 | **40** | |

The DT instantiation count is 1:1 with RTL hard-macro instantiations: every
`pcie6_phy@<addr>` node is one PCIe6 PHY hard macro. The newer "split PHY into vendor PHY +
control device" PR (IPPCIESW-488) adds sibling **control** nodes (`ppw_ctrl` / `dmp_ctrl`)
next to each macro — those are NOT extra PHY macros, they are wrappers around the per-link
PPW/DMP CPU run/halt registers.

Multi-die platforms replicate this whole inventory per die (address-map prefix `D<n>_MNG_...`).

## Sources
- Databook: `~/docs/PCIe EP/phy/dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf`
  - p.18 Quick Reference — "Lane Configurations: x4", bumps `TXP/N[3..0]` / `RXP/N[3..0]`
  - p.20 Features — "One, two, or four full-duplex PAM-4 transceivers" (family supports
    x1/x2/x4; Iridium uses x4)
- DT (mirrors RTL): `fw/proj_infra/soc/dts/a1_hsi.dtsi`
  - EP PHYs: lines 739 (`pcie_phy0`), 756 (`pcie_phy1`)
  - SE PHYs: lines 356, 370, 384, 398, 412, 426, 440, 454 (`se_phy_p0..p7`)
- Bindings:
  - `fw/ip/pcie/dts/bindings/snps,pcie6-phy.yaml` (includes `elmnt,pcie-phy.yaml`)
  - `fw/ip/se/dts/bindings/elmnt,c2c.yaml` (SE references the PHY via `phy-dev`)
