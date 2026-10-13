# PCIe6 PHY CPU run/ack — multi-PHY parallel bring-up + timeout state contract

- **Recorded:** 2026-06-30 (updated 2026-09-15: **ACK is not latched — poll REQ==0**, RTL-verified)
- **Status:** Verified from FW driver (`ip.pcie.sw.fw.ep`#254, heads `b06b236`/`84582c1`);
  ready-poll condition CORRECTED against Iridium (A1) RTL `ppw_misc.v`.
- **Tags:** phy cpu, cpu_run_req, cpu_run_ack, ppw_ctrl_cpu_ind, phy fw load, multi-phy, parallel bring-up, timeout, etimedout, k_busy_wait, vtable, el_pcie_phy, el_ppw_ctrl, auto-clear, req-goes-low

## Scope / Question
How should multi-PHY CPU start be sequenced for shortest aggregate wait, and what is the
correct register-state contract on ready-poll timeout? **And: what is the correct register
to poll to see the PHY CPU is running?**

## CORRECTION (2026-09-15) — poll `CPU_RUN_REQ.REQ == 0`, NOT `CPU_IND.CPU_RUN_ACK`

The original driver (and this note's first revision) polled `PPW_CTRL_CPU_IND.CPU_RUN_ACK`
to become all-ones. **That is wrong / racy.** Net-level proof from Iridium (A1) RTL
`ip.pcie.rtl@iridium ppw_macro/rtl/ppw_misc.v`:

- `cpu_ind_cpu_run_ack_wr = 1'b1`, `cpu_ind_cpu_run_ack_wr_data = cpu_run_ack` — the CSR
  `CPU_IND.CPU_RUN_ACK` field is **continuously loaded with the live `cpu_run_ack` net**,
  i.e. it is **not latched/sticky**. `cpu_run_ack` is a transient acknowledge pulse; once the
  CPU is steadily running it drops back to 0, so `CPU_IND.CPU_RUN_ACK` reads **0 again**.
  Polling it for all-ones is a race — you usually miss the pulse and then time out on a CPU
  that is actually running.
- `cpu_run_req_req_wr = |cpu_run_ack | <fw_copy_fsm pulse>` and
  `cpu_run_req_req_wr_data = ... : (cfg_cpu_run_req & ~cpu_run_ack)` — HW **auto-clears the
  REQ bit** for each macro when its `cpu_run_ack` asserts. This is a stored register value
  that stays 0 (until SW re-requests). Matches header attr
  `PPW_CTRL_CPU_RUN_REQ__REQ_HW_ACCESS = 0x2` (REQ field is HW-writable).
- Broadcast bit: `cpu_run_req_req_broadcast_wr = (cpu_run_ack == all-ones)`, wr_data `1'b0`
  → `REQ_BROADCAST` auto-clears only when **all** PHYs ack.

**Correct ready condition:** after `cpu_run_set` writes `PPW_CTRL_CPU_RUN_REQ.REQ` (mask
`0x6`), poll **`(read(PPW_CTRL_CPU_RUN_REQ) & REQ_MSK) == 0`**. REQ going low is the stable
HW indication that the PHY CPU acknowledged and is running. Fixed in `el_ppw_ctrl.c`
(`el_ppw_ctrl_fw_ready_poll`) under IPPCIESW-488.

The SE C2C path (`ip.se.sw.fw dmp_ctrl.c`, `DMP_CTRL` block) is **identical** and was fixed
the same way: `ip.se.rtl@iridium dmp_macro/rtl/dmp_misc.v` has the same
`cpu_run_req_req_wr_data = cfg_cpu_run_req & ~cpu_run_ack` auto-clear and the same live
non-latched `cpu_ind_cpu_run_ack_wr_data = cpu_run_ack` mirror. SE additionally sets
`DMP_CTRL_PHY_CTRL.CPU_RUN_REQ_INT_BYPASS` (release FW directly, no external run-req
handshake) — but the PHY CPU still emits `cpu_run_ack`, which drives the same REQ auto-clear,
so the REQ==0 condition holds. SE `REQ_MSK = 0x1` (PHY_NUM_MACROS=1) vs EP `0x6` (2 macros).

Register layout note: `CPU_RUN_REQ` (off `0xc`) holds `REQ_BROADCAST`[0] + `REQ`[2:1];
`CPU_IND` (off `0x10`, RO to SW) holds `CPU_RUN_ACK`[1:0] + `CPU_SYS_HALT`[3:2]. They are
**separate registers** — the first revision below conflated them.

## Findings (original 2026-06-30 — ACK-poll parts superseded by the correction above)

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
- FW (current, IPPCIESW-488): `drivers/el_ppw_ctrl/el_ppw_ctrl.c`
  (`el_ppw_ctrl_cpu_run_set`, `el_ppw_ctrl_fw_ready_poll`).
- SE FW (current): `Elmnt-Internal/ip.se.sw.fw` `drivers/dmp_ctrl/dmp_ctrl.c`
  (`dmp_ctrl_cpu_run_set`, `dmp_ctrl_fw_ready_poll`) — same REQ==0 fix.
- RTL (correction proof): `Elmnt-Internal/ip.pcie.rtl@iridium`
  `ppw_macro/rtl/ppw_misc.v` — `cpu_run_req_req_wr` / `cpu_run_req_req_wr_data`
  (`cfg_cpu_run_req & ~cpu_run_ack` auto-clear), `cpu_ind_cpu_run_ack_wr`/`_wr_data`
  (live, non-latched mirror of `cpu_run_ack`); `cpu_run_req_req_broadcast_wr`.
- SE RTL (correction proof): `Elmnt-Internal/ip.se.rtl@iridium`
  `dmp_macro/rtl/dmp_misc.v` — same `cfg_cpu_run_req & ~cpu_run_ack` auto-clear and
  live `cpu_ind_cpu_run_ack_wr_data = cpu_run_ack` mirror; `cfg_cpu_run_req_int_bypass`.
- Regmap: `ppw_ctrl_regs.h` / `ppw_ctrl_regs_attr.h` (pcie v1 / Iridium):
  `PPW_CTRL_CPU_RUN_REQ` off `0xc` (`REQ_MSK 0x6`, `REQ_HW_ACCESS 0x2`),
  `PPW_CTRL_CPU_IND` off `0x10` (`CPU_RUN_ACK_MSK 0x3`, RO to SW).
- Micro-arch (A1): `ip.pcie.rtl` `ppw_macro/misc/PPW_MACRO_Micro_Architecture.md`
  §3.9 ("on `cpu_run_ack` assertion: auto-clears the corresponding bit").
  NOTE: A2 (`..._A2.md` §3.9) drops the whole handshake — `CPU_RUN_REQ`/`CPU_IND`
  no longer exist in A2 `csr_ppw_ctrl`; this note is A1/Iridium-specific.
