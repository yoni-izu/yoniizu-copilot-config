# Protection-Block Node Die Count

- **Recorded:** 2026-10-23
- **Status:** Verified from FW source and board DeviceTree
- **Tags:** protection bits, pb, pmmu, die count, devicetree, iridium

## Scope / Question
What `pb_sec_die_count` means on an Iridium protection-block DeviceTree node.

## Findings

`pb_sec_die_count` is the number of dies a PB node may program, not the number of PB
instances. The `el_pb` driver maps a count of N to allowed dies 0 through N-1, then rejects
requests outside that bitmap. The Iridium ASIC management overlay sets the PMMU PB node to
two dies; the quad-die Palladium overlay sets it to four. These are variant-specific allowed
die ranges and should be interpreted together with the overlay's hardware configuration.

## Sources
- `fw/infra/drivers/el_pb/el_pb.c`: `el_pb_drv_config.die_count`, `el_pb_allowed_die_mask()`,
  the `pb_sec_die_count` DeviceTree property, and the die-ID validation before programming.
- `fw/zephyr_apps/mng/boards/el_arm_a1_asic.overlay`: `&pb_sec_pmmu` uses
  `pb_sec_die_count = <2>`.
- `fw/zephyr_apps/mng/boards/el_arm_a1_palladium_aw_quad_die.overlay`: `&pb_sec_pmmu` uses
  `pb_sec_die_count = <4>`.
