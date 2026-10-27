# Preserved DM Port Role Cache

- **Recorded:** 2026-10-23
- **Status:** Verified from FW source
- **Tags:** dm, role, preservation, device type, root port, endpoint, flr

## Scope / Question
How the DM driver maintains its cached EP/RP role when a live port is preserved across
firmware reinitialization.

## Findings

When a DM port is marked preserved, `dwc_dm_ctl_set_preserved()` reads the controller's
`DM_CTRL_DM_CTRL_CORE_DEVICE_TYPE` field and refreshes the driver's cached role if it is a
known EP or RP value. This is necessary because the normal `dm_set_role()` programming path is
skipped for a preserved port. `dwc_dm_ctl_set_role()` writes that same field from the role enum.
The enum encodes EP as `0` and RP as `4`.

This records the FW cache-coherency behavior; it does not establish additional hardware reset
semantics.

## Sources
- `ip.se.sw.fw`, `drivers/snps_dm_ctl/dwc_dm_ctl.c`: `dwc_dm_ctl_set_preserved()` and
  `dwc_dm_ctl_set_role()` at PR #229 head `70c1a073c51fc2e18cccf069f2a0da9b206ce66a`.
- `ip.se.sw.fw`, `include/dwc_dm_ctl.h`: `dwc_dm_ctl_role_e` (`DWC_DM_CTL_ROLE_EP = 0`,
  `DWC_DM_CTL_ROLE_RP = 4`) at the same head.
