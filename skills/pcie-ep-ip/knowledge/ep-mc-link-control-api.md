# PCIe EP MC link-control API (APP_LTSSM_EN)

- **Recorded:** 2026-10-28
- **Status:** Verified from Iridium PCIe EP firmware source
- **Tags:** pcie ep, link control, app_ltssm_en, ownership, status

## Findings

- The host PCIe endpoint now exposes MC link control as an **ownership-gated**
  API pair in `el_pcie_ep`:
  - `el_pcie_ep_set_link_enabled(bool)` for non-blocking request acceptance.
  - `el_pcie_ep_get_link_control_status(...)` for read-only status snapshots.
- Implementation controls only wrapper `APP_LTSSM_EN` (no standard PCIe Link
  Disable usage on EP), with RMW + readback validation and idempotent behavior.
- Status reports use five-state encoding compatible with OEM link-control
  semantics: held / training / up / down / transitioning, plus LTSSM code,
  negotiated width/speed (only when up), and `held_by_mc` attribution.
- `held_by_mc` is set only after successful MC requests and is not treated as a
  persistent state across firmware restart.
- Normal link-up and interrupt flows are fenced while MC hold is active:
  - ISR handler exits early when endpoint is system-manager-owned.
  - `dwc_ep_ctl_linkup_impl()` returns `-EACCES` while MC hold is asserted.
  - Normal `ltssm_en_set(..., true)` no longer clears an active MC hold.

## Sources

- `fw/ip/pcie/ep/include/el_pcie_ep.h` — public API + status contract.
- `fw/ip/pcie/ep/include/dwc_ep_ctl_api.h` — ownership/embedded guards and
  controller-level API wrappers.
- `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_link_control.c` — MC control and
  status implementation.
- `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c` — hold-aware ISR/linkup/LTSSM behavior.
- `fw/ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_shell.c` and
  `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl_shell.c` — operator commands.
