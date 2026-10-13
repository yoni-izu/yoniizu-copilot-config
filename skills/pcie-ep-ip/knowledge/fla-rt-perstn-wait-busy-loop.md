# FLA.RT PERSTn-deassert wait — long blocking busy-loop before PIW access (A1 warm-reset)

- **Recorded:** 2026-09-23
- **Status:** Reported by owner (Yoni) — accepted for A1 bring-up, flagged risky
- **Tags:** perstn, perst, fla_runtime, fla rt, wait_for_perstn, k_busy_wait, watchdog, wdt, piw reset, warm reset, psoc reboot, phy fw staging, perstn wait timeout, FLA_RT_PCIE_PERSTN_WAIT, 50s timeout, busy-wait, bring-up hang risk

## Scope / Question
On A1, a warm reset reboots the PSoC and FLA.RT re-stages the PCIe PHY FW from flash
(SPSRAM → PIW SRAM → PHY CCM). PIW is held in reset while PERSTn is asserted, so
FLA.RT must wait for PERSTn to deassert **before** any PIW access or the staging
faults / is dropped. Is the wait safe?

## Answer / Finding
FLA.RT gates the staging behind `fla_rt_pcie_wait_for_perstn()` (fla_runtime
`src/pcie.c`), called after PLL clk-div config and before PHY-FW staging in
`fla_rt_pcie_linkup()`:

- Gated by `CONFIG_FLA_RT_PCIE_WAIT_FOR_PERSTN`; returns 0 immediately if PERSTn is
  already deasserted (`el_reset_get_perstn_status()`).
- Otherwise spins: `while (!perstn) { if (waited_ms >= CONFIG_FLA_RT_PCIE_PERSTN_WAIT_TIMEOUT_MS) return -ETIMEDOUT; k_busy_wait(1000); waited_ms++; }`.
- **A1 timeout is 50 s** (after a PSoC reboot a 2nd PERSTn can take ~8 s to
  deassert; the config allows generous headroom).
- MULTITHREADING is off in this image, so `k_busy_wait` cannot yield — it is a hard
  CPU spin for up to the full timeout.

Correctness that IS in place: the loop is bounded, and on timeout it returns
`-ETIMEDOUT` which `fla_rt_pcie_linkup()` propagates — FLA.RT does **not** touch PIW
after a timeout, so it doesn't fault on the reset-held PIW.

## Risk / Caveat (why this is flagged)
- **Watchdog:** a hard busy-spin of up to 50 s will trip any watchdog that is armed
  and not fed during FLA.RT. Before relying on this path, confirm the WDT is either
  disabled across the wait or fed inside the loop. (For A1 bring-up this is accepted
  as-is because the bring-up image has no active WDT on that path, but it is a
  deliberate risk, not a proven-safe design.)
- A 50 s blocking bring-up stage also blocks the 100 ms PCIe-link target entirely
  for that reset case — acceptable only because it is the warm-reset/PSoC-reboot
  recovery corner, not the nominal POR path.
- If MULTITHREADING is ever enabled in FLA.RT, `k_busy_wait` should become a
  yielding wait (or a WDT-fed loop) to avoid starving the system for up to 50 s.

## Related config
- `CONFIG_FLA_RT_PCIE_WAIT_FOR_PERSTN` (enable the wait)
- `CONFIG_FLA_RT_PCIE_PERSTN_WAIT_TIMEOUT_MS` (50000 on A1)
- Progress bits `FLA_RT_PCIE_PERSTN_WAIT` (94) / `_TIMEOUT` (95), decoded by the
  jtag `read_fla_rt_progress_bits.cmm` (proj_infra).
- Reset-domain rule this depends on: PIW is in the POR|WARM reset domain (held in
  reset while PERSTn asserted); HOT reset leaves PHY/PIW resident (no restage).
