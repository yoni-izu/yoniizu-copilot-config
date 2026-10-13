# PCIe SEI interrupts — enablement & handling per Iridium (A1) application

- **Recorded:** 2026-10-07
- **Status:** Verified from FW source + generated `.config` / `zephyr.dts` (build tag `3_0_11_3p0_1389`)
- **Tags:** sei, pcie sei, intr_sei, piw sei, sei tree, el_irq_tree, el_irq_mon, el_piw_irq_actions,
  piw0_sei_root, a1_piw_irq_trees_enable_all, fla_runtime, fla rt, primeboot, mng, management,
  irq connect, sei mask, drain agg, fiu sei, rr sei, iridium a1, per-app enablement

## Scope / Question
Are PCIe SEI interrupts enabled and handled in the Iridium A1 applications — FLA.RT,
PRIMEBOOT, and MANAGEMENT (mng)?

## The four gates that must ALL line up

A PIW/EP SEI reaches FW only if every one of these holds:

1. `CONFIG_EL_PIW=y` + `CONFIG_DWC_EP_CTL=y` — the EP stack is built at all.
2. **HW source unmask** — `PIW_*_SEI_MASK` bits cleared:
   - drain aggregates (`MEM_DRAIN_AGG`/`MNG_SEI_AGG`/`CTRL_SEI_AGG` + leaf drain masks) are
     cleared **unconditionally** by `el_piw_drain_cfg_impl()` whenever `CONFIG_EL_PIW_DRAIN=y`
     (default y) — called from `el_pcie_ep_bring_up()`;
   - FIU-master (`MEM/MNG/CTRL_FIU` + per-port `ERR_RSP`) and RANGE_REG AR/AW miss bits are
     cleared **only if** `IS_ENABLED(CONFIG_EL_IRQ_TREE) && IS_ENABLED(CONFIG_EL_PIW_IRQ_ACTIONS)`
     (`el_pcie_ep.c` `el_pcie_ep_bring_up()`, the `el_piw_fiu_sei_unmask()` / `el_piw_rr_sei_unmask()`
     pair). Deliberate: "without them the SEI would assert with no handler".
3. **CPU line** — `PIW_CONNECT_SEI_IRQ(inst)` in `el_piw.c` requires
   `CONFIG_EL_IRQ_TREE` **AND** `DT_INST_IRQ_HAS_NAME(inst, sei)` **AND**
   `DT_NODE_HAS_STATUS(piw<inst>_sei_root, okay)`. Without `EL_IRQ_TREE` the macro expands to
   **nothing** (comment: no handler would clear the cause ⇒ storm risk), and `irq_set_enabled`
   has no line to toggle. The line is enabled in `el_piw_init()` only when
   `el_piw_owned_check(dev) == 0` (device-owned, not host-owned).
4. **Tree topology enabled in DT** — all `piw0_sei_*` nodes are `status="disabled"` in
   `proj_infra/soc/dts/a1/a1_hsi.dtsi`; a board overlay must
   `#include "a1_piw_irq_trees_enable_all.dtsi"` to flip them to okay.

Alternative (no HW line): `CONFIG_EL_IRQ_MON` polling.
- `EL_IRQ_MON && EL_IRQ_TREE` → `el_piw_sei_tree.c` registers `el_irq_tree_walk_isr` per
  `piw<inst>_sei_root` that is okay.
- `EL_IRQ_MON && !EL_IRQ_TREE` → `el_piw.c` registers the flat
  `el_piw_main/mng/ctrl_process_sei` pollers.
- **`EL_IRQ_MON` is `n` on every `el_arm_a1_*` (silicon) build** — it is a palladium/host-sim aid.

## Verified per-app matrix (A1, `el_arm_a1_*` ARM builds)

| App | Board | EL_PIW / EP | EL_IRQ_TREE | EL_IRQ_MON | EL_PIW_IRQ_ACTIONS | `piw0_sei_root` | PIW `sei` IRQ in DT | SEI handled? |
|---|---|---|---|---|---|---|---|---|
| **FLA.RT** | asic | y | **n** | n | n | disabled | none | **NO** |
| **FLA.RT** | palladium | y | **n** | n | n | disabled | none | **NO** |
| **PRIMEBOOT** | asic | **n** (EP stack off) | y | n | n | disabled | n/a | **NO (no PCIe at all)** |
| **PRIMEBOOT** | palladium | y | **n** | n | n | okay | none | **NO** |
| **MNG** | asic | y | **y** | n | **y** | **okay** | `sei`=0x11b, `spi`=0x11d @ `&plic` | **YES** (hard ISR) |
| **MNG** | palladium | y | **y** | n | **y** | **okay** | `sei`=WIRE_D0(0x11b) | **YES** (hard ISR) |

Source of the DT wiring: only `zephyr_apps/mng/boards/el_arm_a1_asic.overlay` (~L1651) and
`el_arm_a1_palladium.overlay` (~L606) add `interrupts`/`interrupt-names = "sei","spi"` to
`&pcie_ep_piw0`. `a1_piw_irq_trees_enable_all.dtsi` is included by mng arm-asic / arm-palladium /
host-palladium and primeboot arm-palladium / host-palladium overlays only.

**Net: MANAGEMENT is the only Iridium app that enables and services PCIe SEI.**
FLA.RT services none (minimal bring-up only, `EL_PCIE_EP_MINIMAL_INIT=y`); PRIMEBOOT has the
PCIe EP stack compiled out entirely on silicon.

## Handling path when it IS enabled (mng)
`PLIC sei line → EL_IRQ_TREE_IRQ_CONNECT(piw0_sei_root) → el_irq_tree_walk_isr` walks
MAIN SEI cause → children (`fiu_mem/mng/ctrl`, `mailbox`, `mem_drain`, `piw_mng`→`mng_drain`,
`piw_ctrl`→`ctrl_drain`, `iip_core`, `iip_aux`, `sei2`→`pep_ctrl`) → per-bit
`el_piw_irq_action_dispatch()` with LOG / RECOVER / HOST_NOTIFY flags.
The EP controller's own SEI (`PEP_CTRL_AUX`/`PEP_CTRL_CORE INTR_SEI_CAUSE`, incl. the AER
`cfg_*_err_sts` bits and `DS_MSTR_TERM`) is NOT a separate controller IRQ — it enters FW only
as the `iip_aux` / `iip_core` / `pep_ctrl` leaves of this PIW SEI tree.

### Maxim's A1 cause-register snapshot list

The mail's 32-bit `0xA...` addresses are aliases; normalize to the 64-bit A1 ARM address by
replacing the alias prefix as shown below. The bases and offsets resolve against the A1
`3_0_11_3p0_1389` generated project bases and CSR headers:

| Mail address | Normalized A1 address | Register | Meaning |
| --- | --- | --- | --- |
| `0xA04080F4` | `0x1000000004080F4` | `PIW_MAIN_CFG_INTERRUPT_PIW_SEI_CAUSE` | PIW top-level SEI aggregates |
| `0xA0408104` | `0x100000000408104` | `PIW_MAIN_CFG_INTERRUPT_PIW_SEI_2_CAUSE` | split-response, parity, drain-fork and IIP_CTRL causes |
| `0xA0408C4C` | `0x100000000408C4C` | `PIW_MAIN_CFG_MEM_DRAIN_INTERRUPT_SEI_CAUSE` | memory drain causes |
| `0xA040A2C8` | `0x10000000040A2C8` | `PIW_MNG_CFG_INTERRUPT_PIW_MNG_SEI_CAUSE` | PIW MNG termination/error causes |
| `0xA040A2EC` | `0x10000000040A2EC` | `PIW_MNG_CFG_DRAIN_INTERRUPT_SEI_CAUSE` | MNG drain causes |
| `0xA040B07C` | `0x10000000040B07C` | `PIW_CTRL_CFG_INTERRUPT_PIW_CTRL_SEI_CAUSE` | PIW CTRL termination/error causes |
| `0xA040B0A0` | `0x10000000040B0A0` | `PIW_CTRL_CFG_DRAIN_INTERRUPT_SEI_CAUSE` | CTRL drain causes |
| `0xA0440008` | `0x100000000440008` | `PEP_CTRL_INTERRUPT_INTR_SEI_CAUSE` | PEP downstream-master termination |
| `0xA0440098` | `0x100000000440098` | `PEP_CTRL_PEP_CTRL_CORE_INTERRUPT_INTR_SEI_CAUSE` | RTLH/RPLH and completion-timeout causes |
| `0xA044111C` | `0x10000000044111C` | `PEP_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE` | AER sideband, RAS-DP, termination and sent-error causes |
| `0xA0401D80` | `0x100000000401D80` | `FIU_CFG_MEM_MST_INTERRUPT_SEI_CAUSE` | FIU MEM master error responses |
| `0xA0402080` | `0x100000000402080` | `FIU_CFG_CTRL_MST_INTERRUPT_SEI_CAUSE` | FIU CTRL master error responses |
| `0xA0403D80` | `0x100000000403D80` | `FIU_CFG_MNG_MST_INTERRUPT_SEI_CAUSE` | FIU MNG master error responses |
| `0xA040D180` | `0x10000000040D180` | `MAILBOX_INTERRUPT_INTR_SEI_CAUSE` | mailbox overflow |

These are cause snapshots, not a complete dump of PCIe AER status/header logs. The generic IRQ
tree dispatches handlers and then W1C-clears each cause post-order, so a raw snapshot must be
captured before/during the walk's reads; printing the registers after the walk may see cleared
values. Keep MMIO capture small in the hard ISR and defer formatting/log output where possible.

## Gap / risk worth flagging
In **FLA.RT** and **PRIMEBOOT-palladium**, `el_piw_drain_cfg()` still unmasks the drain SEI
aggregates and leaf masks (`EL_PIW_DRAIN=y`) while no SEI line is connected and no poller is
registered. A drain/termination event therefore asserts and **latches** a PIW SEI cause that
nobody reads or clears. PIW SEI cause regs are in the PIW (PoR+warm) reset domain, so a later
MANAGEMENT boot can walk into a stale, pre-existing cause on its first tree walk.

## Citations
- `fw/ip/pcie/ep/drivers/el_piw/el_piw.c` — `PIW_CONNECT_SEI_IRQ`, `PIW_IRQ_LINE_OP`,
  `el_piw_init()`, `EL_IRQ_MON_PIW_REGISTER` (`#if EL_IRQ_MON && !EL_IRQ_TREE`),
  `el_piw_drain_cfg_impl()`, `el_piw_fiu_sei_unmask_impl()`, `el_piw_rr_sei_unmask_impl()`
- `fw/ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep.c` — `el_pcie_ep_bring_up()` gate
  `IS_ENABLED(CONFIG_EL_IRQ_TREE) && IS_ENABLED(CONFIG_EL_PIW_IRQ_ACTIONS)`;
  `el_pcie_ep_init()` / `el_pcie_ep_start()` under `EL_PCIE_EP_MINIMAL_INIT`
- `fw/ip/pcie/ep/drivers/el_piw/el_piw_sei_tree.c` — `EL_PIW_SEI_ROOT_IRQ_MON_REGISTER`
  (`#if EL_IRQ_MON && EL_IRQ_TREE`), cause descriptors & bindings
- A1 CSR decode: `fw/build_artifacts/include/projects/a1/3_0_11_3p0_1389/bases/project_bases.h`,
  `fw/build_artifacts/include/ips/pcie/v1/3_0_11_3p0_1389/prototype/{piw_main_cfg,piw_mng_cfg,piw_ctrl_cfg,pep_ctrl,pep_ctrl_aux}_regs.h`,
  `fw/build_artifacts/include/projects/a1/3_0_11_3p0_1389/prototype/{fiu_cfg_mem_mst,fiu_cfg_ctrl_mst,fiu_cfg_mng_mst,mailbox}_regs.h`
- IRQ ordering: `fw/infra/drivers/el_irq_tree/el_irq_tree.c` (`walk_tree`, `w1c_and_retry`)
- `fw/proj_infra/soc/dts/a1/a1_hsi.dtsi` — `pcie_ep_piw0` (no `interrupts` in base node),
  `piw0_sei_*` nodes all `status="disabled"`
- `fw/proj_infra/soc/dts/a1/a1_piw_irq_trees_enable_all.dtsi` — the opt-in enabler
- Board confs/overlays under `fw/zephyr_apps/{fla_runtime,primeboot,mng}/boards/`
- Generated ground truth: `fw/zephyr_apps/<app>/build/arm/<board>/a1/3_0_11_3p0_1389/zephyr/{.config,zephyr.dts}`
