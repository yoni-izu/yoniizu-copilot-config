# PCIe6 PHY CPU run/ack — multi-PHY parallel bring-up + timeout state contract

- **Recorded:** 2026-06-30
- **Status:** Verified from FW driver (`ip.pcie.sw.fw.ep`#254, heads `b06b236`/`84582c1`)
- **Tags:** phy cpu, cpu_run_req, cpu_run_ack, ppw_ctrl_cpu_ind, phy fw load, multi-phy, parallel bring-up, timeout, etimedout, k_busy_wait, vtable, el_pcie_phy

## Scope / Question
How should multi-PHY CPU start be sequenced for shortest aggregate wait, and what is the
correct register-state contract on `CPU_RUN_ACK` timeout?

## Findings

### Register / fields
- `PPW_CTRL_CPU_IND` is the PHY's CPU-control register on the SNPS PCIe6 PHY.
  - `CPU_RUN_REQ` — **2-bit** field, value `0x6` (set whole, not as a single-bit toggle).
  - `CPU_RUN_ACK` — **2-bit** field, all-ones (`0x3`) means "both ACK bits set".
- "All bits set" idiom used in driver: `FIELD_GET(CPU_RUN_ACK_MSK, CPU_RUN_ACK_MSK)` →
  expected value `3`. Mirrors `CPU_RUN_REQ`'s symmetric 2-bit usage.

### Parallel topology — "assert all, then poll all"
Multi-PHY bring-up runs **three separate loops** across `cfg->phy_devices_num`:
1. `ccm_fast_access_set` per instance
2. `cpu_run_set` (assert `CPU_RUN_REQ`) per instance
3. `cpu_start_poll` (wait for `CPU_RUN_ACK`) per instance

Reason: loops 1 and 2 are non-blocking; loop 3 is the only one that waits. Doing
"assert all → poll all" means all PHYs are running their settle-and-ack work **in parallel**
during loop 3, so the aggregate wait is `max(per-PHY ack time)`, not `sum`. Merging steps 2+3
into a single per-instance "set+poll" op serializes the waits → aggregate becomes `sum`.

Keep the ops as separate vtable entries (`cpu_run_set`, `cpu_start_poll`). The DSP-style API
abstraction (`el_pcie_phy`) must preserve the split.

### Timeout / return-code contract
- `dwc_pcie6_phy_cpu_run_ack_poll` returns `int`: `0` on success, `-ETIMEDOUT` on poll
  exhaustion. Log `LOG_DBG` on success, `LOG_ERR` on timeout — log **before** the poll on the
  success-emit path so the message always prints regardless of which branch wins.
- **The `int` return must be threaded all the way up**:
  - vtable op `cpu_start_poll` must be `int (*)(const struct device *)`.
  - inline wrapper `el_pcie_phy_cpu_start_poll(dev)` must `return api->cpu_start_poll(dev)`.
  - call site (`el_pcie_ep_do_phy_fw_load`) must capture rc, log per-PHY index on failure,
    and `return rc` so the syscall chain (`z_impl_el_pcie_ep_phy_fw_load` →
    `el_pcie_ep_phy_fw_load` → `fla_rt_pcie_init`) can stop init.
- Common bug: typing any of those three as `void` discards `-ETIMEDOUT` at the boundary and
  EP init silently proceeds with a wedged PHY CPU. Symptom: a single `LOG_ERR("Timeout
  waiting for PHY CPU run ack")` followed by linkup attempts on an unresponsive PHY.

### Register-state on timeout
- The poll exits leaving `CPU_RUN_REQ` **asserted**. This is acceptable **only if intentional
  and documented as the abort contract** — i.e. the caller chain aborts init on the
  `-ETIMEDOUT`, so the half-driven sticky `CPU_RUN_REQ` is moot. Otherwise, deassert on
  timeout to avoid half-driven sticky state on a path that might retry.
- Either resolution is fine; the contract just has to be explicit. Most idiomatic: leave
  `CPU_RUN_REQ` asserted **and** add a one-line comment that the caller aborts on
  `-ETIMEDOUT` so deassert isn't needed.

### Timing
- Default Kconfig: timeout `100 ms`, poll interval `50 µs` → `DIV_ROUND_UP(100000, 50) + 1
  = 2001` poll attempts. Uses `k_busy_wait` because PHY FW load runs in `PRE_KERNEL_1`. The
  full 100 ms only elapses on the failure path.

## Sources
- FW: `Elmnt-Internal/ip.pcie.sw.fw.ep` → `drivers/snps_pcie6_phy/dwc_pcie6_phy.c`
  (`dwc_pcie6_phy_cpu_run_req_set`, `dwc_pcie6_phy_cpu_run_ack_poll`),
  `drivers/snps_pcie6_phy/Kconfig`,
  `include/el_pcie_phy.h` (`struct el_pcie_phy_driver_api`),
  `drivers/el_pcie_ep/el_pcie_ep_phy.c` (`el_pcie_ep_do_phy_fw_load`)
  (PR #254 / `IPPCIESW-396`)
