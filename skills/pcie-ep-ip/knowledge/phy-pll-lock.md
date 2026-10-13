# PCIe PHY PLL lock — live status readback (el_regmap_pcie6 CM block)

- **Recorded:** 2026-09-16
- **Status:** Verified from generated CSR headers (Iridium `four` ws)
- **Tags:** pll lock, phy pll, pll0_lock_int, pll1_lock_int, pll0_stat_1, pll1_stat_1, vco_cnt,
  en_lock_detect, skip_lock_detect, raw_lock, cm block, el_cm, el_regmap_pcie6, clocking,
  linkup debug, per macro, pll status

## Scope / Question
"Can I pull a PHY PLL lock?" — yes. Which register/bit, and caveats.

## Key facts
**PHY PLL lock IS a genuine live RO status bit** (unlike CDR-lock / sig-detect which have no live
MMIO bit — see `linkup-debug-phy-status-regs.md`). Element in-house SerDes regmap `el_regmap_pcie6`,
**CM (common) block**, **per PHY macro**. Each x4 macro has **two PLLs** (PLL0, PLL1).

| PLL | Register | Offset | Lock bit | RO? |
|-----|----------|--------|----------|-----|
| PLL0 | `EL_CM_CM_PLL0_STAT_1` | 0x30c0 | `PLL0_LOCK_INT` b15 (0x8000) | yes (SW_ACCESS 0x0) |
| PLL1 | `EL_CM_CM_PLL1_STAT_1` | 0x3154 | `PLL1_LOCK_INT` b15 (0x8000) | yes |

Same word also exposes `PLLx_VCO_CNT` b[14:0] (VCO freq count — sanity that VCO runs) and
`PLLx_SDM_SREG_PLL_DTUNE` b[30:16].

### Absolute addresses (a1/Iridium; base+offset, verified method vs linkup-debug entry)
Macro0 base `0x100000000480000` (lanes 0–3), Macro1 base `0x1000000004c0000` (lanes 4–7):
| | PLL0_STAT_1 | PLL1_STAT_1 |
|---|---|---|
| Macro0 | `0x1000000004830c0` | `0x100000000483154` |
| Macro1 | `0x1000000004c30c0` | `0x1000000004c3154` |
Read word, check bit 15; 1 = locked.

### Caveats
- `EL_CM_CM_PLL0_STAT_0` (0x3048) `PLL0_EN_LOCK_DETECT` b18 — is lock detect enabled?
- `PLL0_CFG_1` `PLL0_SKIP_LOCK_DETECT` b19 — if set, lock detect bypassed ⇒ lock indication forced.
- `CM_PIN_OVRDEN/OVRDVAL` expose `PLLx_LOCK_O` (filtered pin) vs `PLLx_RAW_LOCK_O` (raw). Normal
  poll target = internal debounced `PLLx_LOCK_INT`.
- Read **each macro independently** — macro0/macro1 are separate CM instances behind the one
  Synopsys x8 IP; APB broadcast is write-only (see `ep-phy-dual-macro-broadcast.md`).

**Use:** PLL lock is the right early clocking gate — if `PLLx_LOCK_INT`=0 or VCO_CNT stuck/zero,
nothing downstream (CDR, RX adapt, LTSSM) will come up.

## Sources
- CSR: `sim/build_artifacts/include/prototype/el_regmap_pcie6_regs.h`
  (`EL_CM_CM_PLL0_STAT_1.PLL0_LOCK_INT` 0x30c0, `EL_CM_CM_PLL1_STAT_1.PLL1_LOCK_INT` 0x3154,
  `PLL0_STAT_0.PLL0_EN_LOCK_DETECT` 0x3048, `PLL0_CFG_1.PLL0_SKIP_LOCK_DETECT`)
- Bases: `build_artifacts/include/a1/project/project_bases.h` (`EP_PPW_0/1_PHY_CTRL_0`)
- Related KB: `linkup-debug-phy-status-regs.md`, `ep-phy-dual-macro-broadcast.md`,
  `receiver-detect-terminations.md`
