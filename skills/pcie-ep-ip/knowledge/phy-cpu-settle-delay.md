# PCIe PHY clock-apply-to-CPU-enable delay — 1 ms is not a Synopsys timing requirement

- **Recorded:** 2026-10-27
- **Status:** Verified from Synopsys PHY docs and open Iridium FW PR descriptions; 1 ms hardware validation pending
- **Tags:** phy cpu, settle delay, clock straps, phy clock, pre-run, linkup timing, iridium, fla runtime

## Finding

The 1 ms configured in the Iridium FLA runtime change is a **firmware delay between
applying PHY clock settings through `el_phy_wrap_ctrl_clk_apply` and enabling the
PHY CPU**. It is not the CPU's startup/acknowledgement duration, the end-to-end
PCIe link-up time, or a documented Synopsys requirement.

### Reset-order finding

Synopsys's PHY initialization sequence says to configure PLL and lane inputs
while reset is asserted (databook §4.2, p.49, step 2), then deassert reset
(p.50, step 3), load ICCM/DCCM and program application settings (steps 4–5),
and only then request CPU run (step 6).

The FW ordering inspected in the Iridium workspace does not match that sequence:
- `fla_rt_pcie_linkup()` waits for PERSTn deassertion before proceeding, then
  stages/distributes PHY FW. The early-clock PR description also places
  `el_pcie_ep_phy_clk_prepare()` after PERSTn deassertion and before staging.
- The EP PHY deploy path loads firmware into PHY CCMs, applies PPW clock
  settings, and then requests CPU run (`el_pcie_ep_phy.c`).
- In RTL, `ppw_wrap.v` drives the PHY's `phy_reset` as `~phy_perst_n`; the
  `phy_perst_n` signal is also connected to `phy_presetn_i`. Thus an apply
  performed after PERSTn deassertion is not shown to occur under PHY reset.

The host `perst_n` and generated PHY `phy_perst_n` are distinct signals. In the
Iridium subsystem integration description, host PERST# is routed independently
and is expected to deassert after the subsystem's power-up reset; PHY reset
outputs are released after that subsystem reset. Therefore, on the normal POR
path, by the time FLA.RT's `fla_rt_pcie_wait_for_perstn()` returns and the
firmware reaches the later PPW apply, the PHY reset should already be deasserted.
This confirms the ordering discrepancy; checking the live generated
`phy_perst_n` on hardware would still establish the actual state for a given run.

The observed no-delay case where PHY CPUs stayed halted is consistent with a
failed prerequisite for link training, and can explain why the link then does
not come up. However, the available evidence does **not** prove the delay itself
is the root cause: Synopsys gives no clock-apply-to-CPU-enable interval, and
waiting 1 ms after programming cannot substitute for programming the mode inputs
before PHY reset deassertion if that reset-time sequence is required. Treat the
1 ms result as an empirical workaround/candidate; investigate reset-time
application and confirm on hardware.

The PR descriptions report:
- Late clock straps plus a 5 ms pre-run delay: link came up.
- Early straps plus 2 ms: PHY CPUs ran and LTSSM transitioned, but this did not
  prove a trained link.
- Early straps with no delay: PHY CPUs remained halted.
- Early straps plus 1 ms: selected in the FLA runtime config, but hardware
  confirmation is still pending.

Therefore 1 ms should be described as a **candidate configured clock-apply-to-CPU-enable
delay**, not as a validated minimum or guaranteed expected time. CPU startup/acknowledgement
and subsequent PCIe training are later, distinct parts of initialization.

### What Synopsys specifies

- The PCIe6 PHY databook gives minimum PHY reset assertion durations: **15 µs
  after power-supply ramp-up** and **10 ns for subsequent warm-reset
  assertions** (p.50). It requires the reference clock to be stable before reset
  deassertion (p.50, footnote 1).
- Its initialization sequence says to configure the PLL and lane inputs while
  reset is asserted, then deassert reset, load firmware, program application
  settings, and assert `cpu_run_req_i` (pp.49–50). It does **not** prescribe a
  numerical delay between programming those inputs and enabling the CPU.
- After enabling the CPU, the sequence separately requires waiting for
  `cpu_run_ack_o`, then for lane acknowledgements to deassert (p.50); no fixed
  duration is given for those handshakes either.
- The PHY firmware application note describes `sdk_start_cpu` as a
  request/acknowledge operation, ordered after `sdk_pre_cpu_cfg`, but gives no
  numerical wait time (pp.10, 20).
- The PHY databook's **24 ms (Gen1–Gen5) / 48 ms (Gen6)** number (p.58) applies
  specifically to the PCIe6 MAC's 12-preset link-partner TX equalization
  adaptation procedure; it is not the PHY CPU startup delay or total link-up
  time. The recommended preset-application wait there is at least 2 µs.
- The controller databook's post-reset link-training figure says to run a
  simulation to obtain accurate timing (p.103), rather than specifying one
  universal link-up duration.

Thus, Synopsys documents reset minima, configuration ordering, and status
handshakes, but the reviewed documents do not establish **1 ms**—or another
fixed value—as the required delay from wrapper clock-setting application to CPU
enable. The 1 ms value is a project-specific firmware setting.

## Sources

- Synopsys, *PCIe6 PHY x4 for TSMC5FF Databook*, v2.63a, §4.2, pp.49–50; §4.6.3, p.58.
- Synopsys, *DesignWare Cores PCIe 6.0 PHY Firmware Application Note*, v1.03a, pp.10, 20.
- Synopsys, *PCI Express EP Controller Databook*, v6.42a-lca01, Fig. 2-25, p.103.
- FW: `project.iridium` workspace `one/fw/zephyr_apps/fla_runtime/src/pcie.c`
  (`fla_rt_pcie_wait_for_perstn`, `fla_rt_pcie_linkup`) and
  `one/fw/ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_phy.c`
  (`el_pcie_ep_phy_fw_deploy`).
- RTL: `project.iridium/rtl/ip/pcie/ppw_macro/rtl/ppw_wrap.v`
  (`phy_perst_n` → `phy_presetn_i`, `phy_reset`).
- `project.iridium.sw.fw.fla_runtime` PR #268, summary/configuration and hardware
  observations: https://github.com/Elmnt-Internal/project.iridium.sw.fw.fla_runtime/pull/268
- `ip.pcie.sw.fw.ep` PR #462, configurable `CONFIG_EL_PCIE_PPW_CTRL_CPU_RUN_SETTLE_US`
  and hardware observations: https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/462
- PRs were open and unmerged when checked on 2026-10-27.
