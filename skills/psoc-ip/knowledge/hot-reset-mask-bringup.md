# Hot-Reset Masking for Bring-Up

- **Recorded:** 2026-10-04
- **Status:** Verified from PR diffs and local firmware; hardware behavior not tested
- **RTL branch/tag:** n/a
- **Tags:** hot reset, secondary bus reset, sbr, msk, d2d_msk, el_enable_reset, ep start, skip-init

## Findings

- The reset-manager type mask uses both `MSK` and `D2D_MSK`: masking HOT blocks
  its local and incoming-D2D reset propagation. Other reset types are independent.
- PCIe PR #403 added `el_reset_mask_reset_type(EL_RESET_TYPE_HOT, true)` before
  EP reset preparation/bring-up. It also added an unrelated raw DBI diagnostic API.
- Merged PCIe PR #417 reverted #403, including both additions. Restoring the whole
  revert is therefore broader than restoring the hot-reset behavior alone.
- Normal PSOC `el_enable_reset()` initialization clears every reset type's masks.
  Consequently an earlier EP-start mask can be undone by a later boot image.
- Experimental PSOC PR #563 sets `MSK|D2D_MSK` only for HOT during this loop,
  retaining the existing behavior for other types and SE unit-reset enables.
- A devicetree `skip-init` image returns before `el_enable_reset()`. The PSOC
  change alone does not apply a mask in that path. An EP-start mask supplies the
  complementary coverage when the image starts its EP and has a reset manager.
- On 2026-10-04 the user approved both minimal changes in the local workspace:
  EP-start masking and the PSOC HOT exception, without the unrelated DBI API.
  These edits were uncommitted and only statically checked at recording time.
- Treat this as a temporary bring-up policy, not the permanent reset design.
  PR #563 says the intended production behavior includes PSoC hot reset and
  identifies D2D hot-reset-aware initialization as the permanent fix.

## Sources

- [PCIe PR #403](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/403)
  (merge `24a58cd425278603f9635cae6cbaab7299b5decb`)
- [PCIe PR #417](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/417)
  (merge `b1b039f57d9c6a0695b4beb2906a7adf6ac206d4`)
- [PSOC PR #563](https://github.com/Elmnt-Internal/ip.psoc.sw.fw/pull/563)
  (head `9d69d3a235242975d9b16a0edaef9155e1c694f1`; open experimental PR)
- `ip/psoc/drivers/el_reset/el_reset.c`: `el_enable_reset()`, `el_reset_drv_init()`.
- `infra/include/el_reset_common.h`: `el_reset_mask_reset_type()`.
- `ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep.c`: `el_pcie_ep_start_dev()`.
