# Pre-FIU transport reachability — SPI-GW reaches horizontal links MMIO can't

- **Recorded:** 2026-06-30
- **Status:** Verified from FW driver (`ip.d2d.sw.fw`#83, head `4c0fc02`)
- **Tags:** spi gateway, spi-gw, mmio transport, fiu, transport switch, dup broadcast, horizontal links, d2-d3, primeboot, sbinit, re-bring-up, verification reads

## Scope / Question
During PRIMEBOOT / early D2D bring-up, when can FW switch the D2D transport from SPI gateway
to direct MMIO, and which probes/reads must remain on SPI-GW?

## Findings

### Reachability rule
- Before the **FIU** has opened full-chip MMIO reachability, the SPI gateway (SPI-GW) is the
  only transport that can reach **certain horizontal D2-D3 links**. Direct MMIO at that stage
  cannot route to those wrappers — reads land somewhere else or time out.
- Therefore the MMIO transport switch must be **tightly scoped**. Only one operation requires
  it: the **DUP broadcast** in the FW-load path, which has to use MMIO.

### Scoping pattern (PR #83)
- MMIO switch is relocated into `drivers/d2d_fw_load_dup.c` and brackets only the broadcast
  itself. After the broadcast, transport reverts.
- **SBINIT-done probing**, **link-status checks**, and **per-wrapper verification reads** stay
  on SPI-GW for the duration of bring-up. Concretely: `el_d2d_mgr_step_read_sbinit`,
  `el_d2d_mgr_step_read_link`, and the spare-bit probe in `el_d2d_wrapper.c` all read via
  SPI-GW, aggregate across all wrappers, and do not early-break on first failure (so a
  partial-reach symptom is visible, not masked).
- Re-bring-up flows also stay on SPI-GW for the same reason — wider reach is the right
  default for diagnostics and recovery; MMIO is the narrow exception.

### Why this matters for FW
- Treat MMIO transport as the *narrow* path during early boot, SPI-GW as the *broad* path.
  Any new probe/verify step that needs to see **all** wrappers (incl. horizontals) must use
  SPI-GW, otherwise it will silently miss the links the FIU hasn't opened yet and the
  diagnostic will lie ("ok" when it isn't).
- Switching MMIO on globally during PRIMEBOOT to "simplify" — anti-pattern. Subsequent
  reads on horizontal D2-D3 will quietly land on the wrong target or time out.

## Sources
- FW: `Elmnt-Internal/ip.d2d.sw.fw` → `drivers/d2d_fw_load_dup.c` (transport switch site),
  `drivers/el_d2d_wrapper.c` (SBINIT spare-bit probe over SPI-GW),
  `drivers/el_d2d_manager.c` (`el_d2d_mgr_step_read_sbinit`, `el_d2d_mgr_step_read_link`
  aggregate-across-wrappers)
  (PR #83 / `IPD2DSW-155`)
- Related: `sbinit-done-probe.md` (the spare-bit probe itself)
