# Link capability (Max Link Speed / Width) vs Target Link Speed — how to cap an EP/USP link

- **Recorded:** 2026-10-18
- **Status:** Verified from controller reference + databook + FW. Core RTL (Synopsys `solution_ws`,
  `/vlsi/proj/.../pcie/v45/ep/solution_ws`) NOT accessible locally — runtime coupling claim is
  doc-based (no doc text describes coupling). Confidence: high on register semantics, medium-high
  on "no auto-propagation".
- **Tags:** target_cap, max link speed, max link width, PCIE_CAP_MAX_LINK_SPEED, PCIE_CAP_MAX_LINK_WIDTH,
  LINK_CAPABILITIES_REG, LINK_CAPABILITIES2_REG, PCIE_CAP_SUPPORT_LINK_SPEED_VECTOR,
  PCIE_CAP_TARGET_LINK_SPEED, LINK_CONTROL2_LINK_STATUS2_REG, DBI_RO_WR_EN, MISC_CONTROL_1_OFF,
  app_dbi_ro_wr_disable, LINK_CAPABLE, PORT_LINK_CTRL_OFF, NUM_OF_LANES, GEN2_CTRL_OFF, speed cap,
  width cap, upstream port, gen5 cap, se dm ctl, CX_MAX_PCIE_SPEED

## Findings

There is **no field literally named `TARGET_CAP`**. The "capability" knobs are:

| Field | Reg (offset) | Access | Notes |
|---|---|---|---|
| `PCIE_CAP_MAX_LINK_SPEED` [3:0] | `LINK_CAPABILITIES_REG` (PCIe cap +0xC) | Wire: none; **DBI: R/W only if `DBI_RO_WR_EN==1`**, sticky | encoding = bit index+1 in Supported Link Speeds Vector (Gen1=1..Gen6=6). Ref p2164 |
| `PCIE_CAP_MAX_LINK_WIDTH` [9:4] | `LINK_CAPABILITIES_REG` | same, sticky | 1/2/4/8/0x10. Ref p2163 |
| `PCIE_CAP_SUPPORT_LINK_SPEED_VECTOR` [7:1] | `LINK_CAPABILITIES2_REG` (+0x2C) | **R (not DBI-writable)** | **default derived from MAX_LINK_SPEED** (`6→0x3F, 5→0x1F, 4→0xF, 3→0x7, 2→0x3, else 0x1`). Ref p2224–2226 |
| `PCIE_CAP_TARGET_LINK_SPEED` [3:0] | `LINK_CONTROL2_LINK_STATUS2_REG` (+0x30) | DBI R/W, sticky | **reset default = Max Link Speed (config-time)**. Writing a value not in the Supported Speeds Vector ⇒ **"result is undefined"**. **For Upstream Ports (EP/USP), "if Enter Compliance is Clear, this field is permitted to have no effect."** Ref p2239–2240 |

Width (databook App K "How to Turn Off Unused Lanes", p861) — three fields, all via DBI:
`PORT_LINK_CTRL_OFF.LINK_CAPABLE` (2w−1: x1=1,x2=3,x4=7,x8=0xF,x16=0x1F; used by LTSSM in Detect),
`GEN2_CTRL_OFF.NUM_OF_LANES` (=w; lanes checked to exit L2.Idle / Polling.Active),
`LINK_CAPABILITIES_REG.PCIE_CAP_MAX_LINK_WIDTH` (=w; what the RP reads as max width; needs DBI_RO_WR_EN).

Databook p448 (note): **"Only PCIE_CAP_MAX_LINK_SPEED … determines the maximum link speed of the port."**

### Answers
1. **Setting the cap does NOT update Target Link Speed.** Target's "default = Max" is the
   config-time reset value only. After lowering Max (e.g. 6→5) Target stays 6 → points to a bit
   no longer in the (auto-shrunk) Supported Speeds Vector → undefined. ⇒ **FW must program
   Target ≤ Max** (convention: Target = Max unless an explicit lower target is given).
2. The **Supported Link Speeds Vector does follow** Max automatically (it's RO, derived).
3. On an **EP / USP**, Target alone is spec-permitted to have no effect on training ⇒ the
   reliable way to cap an EP is the **cap** (Max Link Speed). A Target-only override (the
   original IPPCIESW-689 approach) is incomplete.
4. Cap writes need `MISC_CONTROL_1_OFF.DBI_RO_WR_EN=1` (open/close window) and, on the Element
   EP, wrapper input `app_dbi_ro_wr_disable` (`PEP_CTRL_AUX_APP_CTRL.APP_DBI_RO_WR_DISABLE`)
   must be 0 — if set, DBI_RO_WR_EN can't be set and the cap write is silently dropped ⇒
   **read back** the field to verify.
5. Program before LTSSM enable. Cap can only be **lowered** below the HW (CX_MAX_PCIE_SPEED /
   CX_NL) reset value — read the reset value first and reject raises.

### FW state at time of recording (a1_power_on)
- EP `dwc_ep_ctl.c`: only Target speed (DTS `link-speed`); no cap, no width.
- SE `dwc_dm_ctl.c`: `dm_set_target_link_speed` (Target only), `dm_set_target_link_width`
  (LINK_CAPABLE + NUM_OF_LANES, **missing MAX_LINK_WIDTH**); never sets DBI_RO_WR_EN.

### FW change implemented (2026, a1_power_on, uncommitted/unbuilt at recording)
- EP: DTS `max-link-speed` ("hw-default"|gen1..6) + `max-link-width` (0|1..16) →
  `dwc_ep_ctl_set_link_cap()` (reject raise vs current sticky value, pow2 width,
  DBI_RO_WR_EN window, also LINK_CAPABLE=2w-1 + NUM_OF_LANES, read-back → -EIO).
  Called in `el_pcie_ep_linkup_cfg()` before `set_def_link_speed`; target clamped to
  MAX_LINK_SPEED with LOG_WRN; link validation compares against min(target, max).
- SE: Kconfig `EL_SE_PCIE_MAX_LINK_SPEED` (0=keep) → `dwc_dm_set_max_link_speed()` step
  before target; target clamped; `dm_set_target_link_width` now validates and also writes
  MAX_LINK_WIDTH (read-back); post-link check uses min(target, ls.max_speed).
- Known limitation: caps are sticky, so once lowered a later FW wanting a higher
  value gets -EINVAL until a sticky/cold reset (config-time max is not readable).

## Sources
- `DWC_pcie_ctl_reference.pdf` (6.42a-lca01) p2163–2164 (LINK_CAPABILITIES_REG), p2221–2226
  (LINK_CAPABILITIES2_REG), p2227, p2239–2240 (LINK_CONTROL2 Target Link Speed)
- `DWC_pcie_ctl_ep_databook.pdf` p245 (§3.11.1.1 speed change), p448 (max-speed note), p861 (App K)
- RTL `ip.pcie.rtl` main: `pep_macro/rtl/pcie_iip_device_wrap.v:670,2726` (`app_dbi_ro_wr_disable`)
- FW: `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c` (`dwc_ep_ctl_set_link_speed_impl`, `dbi_ro_wr_en_set`),
  `ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c` (`dm_set_target_link_speed`, `dm_set_target_link_width`)
