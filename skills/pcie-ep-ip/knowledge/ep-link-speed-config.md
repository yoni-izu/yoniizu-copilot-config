# EP target link speed — DTS-driven (`link-speed`), and the A1 Gen5 cap for PHY FW 7.0.127

- **Recorded:** 2026-10-12
- **Status:** Verified from FW + binding + build output
- **Tags:** link speed, link-speed, target link speed, def_link_speed, pcie_cap_target_link_speed, link_control2_link_status2_reg, gen5, gen6, snps,ep-ctl, dwc_ep_ctl, a1_hsi.dtsi, proj_infra, phy fw 7.0.127, gen6 instability, workaround, ippciesw-689, enum_idx, linkup cfg

## Scope / Question

Where is the PCIe **EP** target link speed configured in this firmware, how does
it reach the controller, and how do you cap the link (e.g. to Gen5) for a
bring-up workaround?

## Findings

### The knob is devicetree, not code

`link-speed` is a property of the **`snps,ep-ctl`** binding — there is no
Kconfig, no runtime default in C, and nothing to patch in the driver:

```yaml
# ip/pcie/ep/dts/bindings/pcie_ep/snps,ep-ctl.yaml
link-speed:
  description: Target link speed for the PCIe link
  type: string
  enum: ["no-link", "gen1", "gen2", "gen3", "gen4", "gen5", "gen6"]
  default: "gen6"
```

**The enum index equals the PCIe generation number** (`no-link`=0 … `gen6`=6), which
is why the driver can feed `DT_INST_ENUM_IDX_OR` straight into the register field:

```c
/* ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c, DWC_EP_CTL_INIT(inst) */
.def_link_speed = DT_INST_ENUM_IDX_OR(inst, link_speed, PCIE_LINK_SPEED_GEN6_64GT),
```

### Path from DTS to hardware

1. `link-speed` → `def_link_speed` in the `dwc_ep_ctl` config struct (above).
2. `dwc_ep_ctl_set_def_link_speed_impl()` → `dwc_ep_ctl_set_link_speed_impl()`.
3. That is an RMW of **`DWC_PCIE_USP_PF0_PCIE_CAP_LINK_CONTROL2_LINK_STATUS2_REG`**,
   field **`PCIE_CAP_TARGET_LINK_SPEED`** (via DBI).
4. Called from `el_pcie_ep_linkup_cfg()` (`drivers/el_pcie_ep/el_pcie_ep_link.c`)
   — i.e. **before PHY FW load and link training**, which matches step 1 of the
   documented init sequence in `ip/pcie/ep/docs/pcie-init-sequence.md`.

> Note: the comment above that call in `el_pcie_ep_link.c` still reads
> `/* set target link speed to gen 6 */` — it is stale/misleading; the value is
> whatever the DTS says, and defaults to gen6 only via the binding.

### Where the A1 nodes live

The `dwc_ep_ctl` nodes are **not** in the app overlays — the overlays only flip
`status = "okay"`. The nodes themselves are in **project data**:

| Project | File | Nodes |
|---------|------|-------|
| A1 / Iridium | `proj_infra/soc/dts/a1/a1_hsi.dtsi` | `ep_ctl0` (inside `pcie_ep0`), `ep_ctl1` (inside `pcie_ep1`) |
| A2 / Atlantic2 | `proj_infra/soc/dts/a2/a2_hsi.dtsi` | `ep_ctl0` |

**So a project-wide speed cap is a one-file, one-repo change** in `proj_infra`.
Editing the app overlays instead (`primeboot`, `fla_runtime`, …) would span 3–4
repos and need a cross-repo `DEPENDS_ON`. Changing the **binding default** is
worse still — it would also move A2.

Precedent for the placement: the scale-up DM controllers in the same file
(`snps_dm_ctl0..7`) already carry `link-speed = "gen6";` right before
`status = "disabled";`. Mirror that.

### The A1 Gen5 cap (IPPCIESW-689)

**PHY FW 7.0.127 is unstable on the EP side at Gen6 on silicon.** Workaround
applied on `a1_power_on_branch`:

```dts
/* proj_infra/soc/dts/a1/a1_hsi.dtsi — ep_ctl0 and ep_ctl1 */
/* Gen6 is unstable with PHY FW 7.0.127 (IPPCIESW-689). */
link-speed = "gen5";
```

Both EP instances must be set — `ep_ctl0` (D0) and `ep_ctl1` (D1). A2 untouched
(A2 targets Gen7). **Revert to Gen6 once a PHY FW drop stabilizes Gen6.**

### How to verify a `link-speed` change without silicon

Build any app that instantiates the node and grep the generated devicetree —
the enum index is the generation:

```bash
octl ws build zephyr_apps/fla_runtime --args "platform=arm variant=asic"
grep link_speed_ENUM_IDX \
  zephyr_apps/fla_runtime/build/arm/asic/a1/*/zephyr/include/generated/zephyr/devicetree_generated.h
# DT_N_S_soc_S_pcie_ep0_f0440000_S_dwc_ep_ctl_f0440000_P_link_speed_ENUM_IDX 5
```

`ENUM_IDX 5` == `PCIE_LINK_SPEED_GEN5_32GT`. A bad enum string fails at CMake
configure (binding check), so a successful configure is itself the validation.

## Sources

- Binding: `ip/pcie/ep/dts/bindings/pcie_ep/snps,ep-ctl.yaml` (`link-speed`, default `gen6`)
- FW: `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c` — `DWC_EP_CTL_INIT()` `.def_link_speed`,
  `dwc_ep_ctl_set_link_speed_impl()`, `dwc_ep_ctl_set_def_link_speed_impl()`
- FW: `ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_link.c` — `el_pcie_ep_linkup_cfg()`
- Doc: `ip/pcie/ep/docs/pcie-init-sequence.md` — step 1 sets
  `PF0_PCIE_CAP.LINK_CONTROL2_LINK_STATUS2_REG.PCIE_CAP_TARGET_LINK_SPEED` before PHY FW load
- DTS: `proj_infra/soc/dts/a1/a1_hsi.dtsi` (`ep_ctl0`, `ep_ctl1`, and the
  `snps_dm_ctl*` precedent), `proj_infra/soc/dts/a2/a2_hsi.dtsi`
- Erratum + change: Jira **IPPCIESW-689**, PR
  `Elmnt-Internal/project.iridium.sw.fw.infra#1434` (base `a1_power_on_branch`,
  label `PO-TEMP-ONLY`)
