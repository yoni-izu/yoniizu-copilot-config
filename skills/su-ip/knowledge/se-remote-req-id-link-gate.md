# SE `REMOTE_REQ_ID` programming is link-up gated

- **Recorded:** 2026-10-25
- **Status:** Verified from Iridium SE firmware source
- **Tags:** se, c2c, remote_req_id, link training, bus fault

## Finding

The SE firmware defers `REMOTE_REQ_ID` writes until C2C link training completes.
The register is accessed through the C2C core window; firmware comments report
that accessing it before the link trains bus-faults on Palladium. The ordinary
NPA/CAM table writes use the C2C LUT programming path and are performed earlier
in bring-up, separately from this core-register write.

The boot path invokes `el_se_lut_program_all_remote_req_ids()` only after link
polling reports all links up. Runtime reconfiguration uses
`el_se_orch_all_links_up()` as an all-dies/all-FW-owned-links gate before calling
that helper. The helper iterates each die's ports, so the current runtime policy
is global: one link down skips `REMOTE_REQ_ID` programming for all dies.

This is the firmware's safety rationale, not an independently verified C2C
protocol requirement. Because link-up alone does not wake the LUT worker, a
runtime apply skipped by the gate needs a later explicit signal (or a retry
mechanism) to apply `REMOTE_REQ_ID`.

## Sources

- `ip/se/drivers/scale_engine/el_se_orch.c`: `el_se_orch_apply_lut_config()`,
  `el_se_orch_all_links_up()`, and the post-link-poll bring-up path.
- `ip/se/drivers/scale_engine/el_se.c`: `el_se_die_lut_program_remote_req_ids()`
  iterates the die's C2C ports.
- `ip/se/drivers/c2c/el_c2c.c`: `el_c2c_write_remote_req_id_impl()` writes
  `C2C_CORE_TLP_NATIVE_TRANS` through `core_base`.
