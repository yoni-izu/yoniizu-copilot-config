# SU / PCIe LinkStatus telemetry attribute — implementation status (A1 FW)

- **Recorded:** 2026-11-02
- **Last verified:** 2026-11-05
- **Status:** Verified from release source and full-ASIC ELF; open PR pair assessed from source and author-reported lab test; customer EP0 cause unconfirmed
- **Tags:** linkstatus, sulinkstatus, pcielinkstatus, telemetry attribute, middleware, provider, pldm state sensor, su subsystem, scale-up, el_se, dll_active, rdlh_link_up

## Scope / Question
"Did we implement the LinkStatus attribute?" — both the Scale-up (`SuLinkStatusL0..L31`)
and PCIe EP (`PcieLinkStatusL0/L1`) manageability attributes.

## Findings
The providers are wired end to end for both SU and PCIe EP (A1), but the
released PCIe EP PLDM values have the correctness defects described below.

### SU (`SuLinkStatusL0..L31`, attr IDs 506–537, u8, cacheable)
1. **Attribute DB (DTS):** `fw/proj_infra/soc/dts/a1/a1_mw_attributes.dtsi` (~line 3271+),
   IDs in `fw/proj_infra/include/el_mw_attr_ids.h:532-563`. Mirrored in the a2 DTS.
2. **PLDM state sensors:** `fw/proj_infra/soc/dts/a1/mng_generated/pldm_state_sensors.dtsi`
   declares `SuLinkStatusL0..L31` sensors (PLDM state set 33, Link state).
3. **MW providers:** `fw/ip/mng/subsystems/su/su_subsystem.c` — `SU_LINK_STATUS_PROVIDER`
   LISTIFY-generated for all 32 flat ports (line ~339-352); `su_read_link_status` (line 137)
   maps `status.link_up` → `PLDM_STATE_SET_LINK_STATE_CONNECTED/DISCONN`; absent/unpopulated
   port (`-ENODEV`/`-EINVAL`) degrades to DISCONN instead of erroring.
4. **SE backend:** `su_read_link_status` calls `el_se_get_port_link_status_flat()` —
   `fw/ip/se/drivers/scale_engine/el_se_orch.c:1216`, declared `fw/ip/se/include/el_se.h:280`.
   It resolves the flat index to the DM controller and calls
   `dwc_dm_ctl_get_link_status()`. The DM driver derives `.link_up` from its debug-signals
   LINK_UP bits together with `LTSSM_L0` (`fw/ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c:1382+`);
   it does **not** use the PCIe Link Status DLL_ACTIVE bit for this attribute.
5. **Build enablement:** `CONFIG_EL_MW_SU=y` in `zephyr_apps/mng/boards/el_arm_a1_asic.conf:285`
   and `el_arm_a1_palladium.conf:176`; `subsystems/CMakeLists.txt` gates on it.
   Single-die board intentionally falls back cleanly (EL_SE off → -ENOTSUP/degrade).

### PCIe EP in release 0.5.231 (`PcieLinkStatusL0/L1`)
- `fw/ip/mng/subsystems/pcie/pcie_subsystem.c:173-186` wires
  `pcie_read_link_info` for both ports with field `PCIE_LINK_INFO_DLL_ACTIVE`.
- On attribute read, `pcie_read_link_info` (`pcie_providers.c:159+`) looks up the EP device
  and calls its `el_pcie_ep_api.get_link_info` function pointer. This resolves to
  `el_pcie_ep_get_link_info_impl` (`ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep.c:229+`),
  which calls `dwc_ep_ctl_get_link_info`; the Synopsys EP controller reads the Link Status
  register DLL_ACTIVE bit (`ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c:1969+`) and the EP
  implementation returns `status.dll_active`.
- The public `el_pcie_ep_get_link_info()` syscall wrapper exposes the same API externally;
  the middleware provider calls the device API vtable directly.

### Related but distinct
- The **OEM PLDM command** `OemPcieLinkStatus` (`el_oob_oem_pcie_link_status`,
  `fw/ip/mng/services/el_oob/src/el_oob_oem_hw_pcie.c:191`, dispatched from
  `el_pmci/service/pldm/pldm_oem.c:1034`) is a separate command path covering both
  PCIe and SU subsystems — not the telemetry attribute.
- Sibling attributes implemented via the same SU provider pattern: `SuLinkStateL*`,
  `SuLinkSpeedCurrentL*`, connectivity, and RAS-DES event counters.

### PLDM PCIe EP state and status defects (release 0.5.231)

Verified against the official source and full-ASIC MNG ELF for
`fw_release_1.741.0-01112026-163934`:

- The A1 Manageability Specification, table index 60, rows 2–3, distinguishes
  `PcieLinkStateL<x>` (Enabled/Disabled) from the operational
  `PcieLinkStatusL<x>`. The generated PDR uses PLDM Availability for the former
  and Link State for the latter (`pldm_state_sensors.dtsi:431-459`); their
  relevant values are 1/2, not raw booleans.
- `pcie_read_link_info` passes through `link_state` and `dll_active` as 0/1.
  Thus a false value becomes 0 (unknown) instead of 2 (disabled/disconnected).
  `link_state` itself is SMLH up AND RDLH up AND LTSSM == L0; `dll_active` is
  the DBI Link Status bit. The name suffix `L0` identifies an attribute port,
  not the LTSSM L0 state. The released ELF's PCIe sensor descriptors contain
  the expected nonzero attribute IDs 346/348 and 347/349 for the two composites.
- The full-ASIC image wires only EP0. EP1's link-info provider returns 0 with
  success before any device or state conversion; the image reports port count
  1. Whether an absent port should be advertised or represented as a disabled
  state is a product contract decision.
- A PLDM sensor read failure is a different path: the command succeeds overall,
  but the affected sub-sensor has operational state 3 (status unknown) and
  present state 0 (`pldm_sensor.c:764-827`). A successful callback retains the
  sensor's configured operational state, normally 0 (enabled), which can also
  be changed independently. Capture both fields, not just the value.
- The released endpoint controller gates `get_link_info` on system-manager
  ownership (`dwc_ep_ctl_api.h:674-694`). An isolated ARM-emulation run of the
  released provider, with device readiness mocked and controller ownership set,
  returned `-EACCES` for EP0 state, status, and current speed. Neither ownership
  nor an initialization failure is established on a customer system.
- **Startup retry gap (PH-014):** middleware performs one provider sweep during
  init (`el_mw_init.c:82-89`). A failed read leaves an attribute INITIALIZING
  (`el_mw_attribute_provider.c:82-124`); ordinary locked reads then return
  `-EAGAIN` before calling its provider (`el_mw_attribute_access.c:29-46`), and
  periodic updates skip it (`el_mw_periodic.c:183-189`). Other successful
  update paths can still promote the attribute, so recovery is not impossible.
  In isolated execution of the released ARM ELF with manually initialized
  middleware and synthetic Gen4 x8 hardware, device ownership or not-ready
  status during the sweep left EP0 state/status and speed/generation/width at
  PLDM completion 0, operational state 3, and reading 0 even after the mocked
  condition cleared. An explicit second sweep recovered them to state/status
  1/1 and speed/generation/width 4/4/8 with operational state 0. This reproduces
  a conditional firmware failure, not the customer's runtime conditions.
- Isolated ARM execution of the released sensor handler with a mocked `-EACCES`
  callback produced completion 0, operational state 3, and present state 0;
  mocked successful boolean 0/1 produced present state 0/1. This is not a
  hardware reproduction. In particular, a successful EP0 read with the
  controller's L0/up condition true produces 1, so the exact cause of an
  observed EP0 zero still needs the raw response and runtime evidence.
  Synthetic L0s, L1, and Recovery samples produced state 0 while independently
  mocked DLL_ACTIVE kept status 1; these combinations are not HW observations.
- At the earlier reviewed MNG PR [#515](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/515)
  head `5a304d33849c6ce3e65caeed7cc0d8fafae74934`, the absent-device early return
  still bypassed state conversion. The later revision and release port below
  correct that path; they do not add a generic retry for an INITIALIZING attribute.

### Middleware enablement and release chronology

Verified 2026-11-05. Dates and merge times below are UTC; they are source
milestones, not proof of what a customer flashed.

- On **main**, Vasyl Gomonovych added the PCIe MW providers in
  [MNG #322](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/322) on
  2026-09-10 and enabled the ASIC PCIe MW subsystem and EP0 binding in
  [MNG app #260](https://github.com/Elmnt-Internal/project.iridium.sw.fw.psoc_mng/pull/260)
  on 2026-09-15. The ASIC PLDM state-sensor container was enabled separately
  by [MNG app #317](https://github.com/Elmnt-Internal/project.iridium.sw.fw.psoc_mng/pull/317)
  on 2026-09-25. The initial provider already passed raw booleans through
  ([source](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/aeb341e174bfd07c17ae2577de5603aa96ac6322/subsystems/pcie/pcie_providers.c#L150)).
- On **A1 power-on**, the ASIC PLDM state-sensor container was enabled by
  [MNG app #312](https://github.com/Elmnt-Internal/project.iridium.sw.fw.psoc_mng/pull/312)
  on 2026-09-23. Vasyl ported the PCIe providers and the unwired-port zero
  fallback in [MNG #400](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/400),
  merged 2026-10-14 12:52:53; he enabled the ASIC PCIe MW subsystem and
  bound EP0 in [MNG app #265](https://github.com/Elmnt-Internal/project.iridium.sw.fw.psoc_mng/pull/265),
  merged 2026-10-14 18:24:23. Yoav Filin merged both power-on PRs. The latter
  is the verified full-ASIC source-level MW wiring point on this branch.
- Published full-ASIC MNG ELFs for
  [0.5.151](https://github.com/Elmnt-Internal/project.iridium.sw.fw.infra/releases/tag/fw_release_1.726.0-13102026-171857),
  [0.5.16](https://github.com/Elmnt-Internal/project.iridium.sw.fw.infra/releases/tag/fw_release_1.728.0-14102026-123234),
  and the independently maintained
  [0.5.153 hotfix](https://github.com/Elmnt-Internal/project.iridium.sw.fw.infra/releases/tag/fw_release_1.726.0-16102026-134644)
  contain PLDM state-sensor descriptors for PCIe MW IDs 346/348 and 347/349,
  but no registered providers for IDs 346-349. Their ELF hashes match the
  respective release checksums. Thus the absence of full wiring does **not**
  establish those versions as good: the sensor definitions predate the
  provider. In the archived 0.5.16 source, a normal provider-less read fails
  ([MW accessor](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/ec8dae8f1735cb0be4d20b042ad4f9b339124255/modules/el_middleware/attribute/el_mw_attribute_access.c#L29));
  the [PLDM state handler](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/ec8dae8f1735cb0be4d20b042ad4f9b339124255/modules/el_pmci/service/sensor/pldm_sensor.c#L764)
  maps a failed read to completion 0, operational state 3 and present state 0.
  This is a source-path conclusion, not a customer or hardware reproduction.
- The earliest identified published firmware build with the full-ASIC
  wiring is
  [`1.729.0-16102026-133714`](https://github.com/Elmnt-Internal/project.iridium.sw.fw.infra/releases/tag/fw_release_1.729.0-16102026-133714),
  published 2026-10-16 14:25:26; its source archive is labeled `0.5.17`.
  The archive enables the PCIe MW subsystem, binds EP0, and enables the PLDM
  sensor container. Its checksum-verified ELF has provider entries for all
  four IDs 346-349, each pointing to `pcie_read_link_info`. The false-value
  raw-boolean mapping defect is present. This is the earliest build identified,
  not proof that no earlier intermediate build existed.
- The external
  [0.5.17 CD manifest](https://github.com/Elmnt-Internal/ip.infra.sw.manifests/blob/aqua-external-0.5.17-18102026-191141/projects/iridium/iridium_release_0.5.17/atlantic-a1-sw-cd.external.xml#L129)
  pins a **different** artifact, `1.729.0-18102026-175538`. Its exact binary
  was not inspected; the corresponding release-branch board, provider and PDR
  source files match the separately published 0.5.17 source archive. Do not
  conflate the two artifact versions or infer the customer's flashed image.

### Release 0.5.233 ports and updated main MNG (reviewed 2026-11-05)

The dependent, open release ports target `iridium_release_0.5.233`:
[EP #471](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/471) at
`f8cc901eb782c510c0ef9663ba3b23d1352e72dd` and
[MNG #522](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/522) at
`e9cb9ae64e5719ba94132a66fc79296276aa69e4`. Main-branch MNG #515 was also
rechecked at `7533ac2f2ac28f76d07752d9f5bc4dfee6a3b3b5`. None is a
customer-verified or released fix.

- EP #471's cherry-pick has the same stable patch ID as the previously reviewed
  EP #469 source commit; its additional commit only wraps a shell output line.
  The [release controller](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/blob/f8cc901eb782c510c0ef9663ba3b23d1352e72dd/drivers/snps_ep_ctl/dwc_ep_ctl.c#L1970)
  retains `dl_active = dll_active || rdlh_link_up` and the sampled link-up latch.
- The [MNG release provider](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/e9cb9ae64e5719ba94132a66fc79296276aa69e4/subsystems/pcie/pcie_providers.c#L160)
  applies named PLDM state-set conversion to both device-backed and unwired
  ports. An unwired port now returns Disabled/Disconnected (`2/2`) for
  state/status instead of `0/0`; numeric attributes still return zero. Whether
  an absent port should instead be omitted or marked unavailable is a product
  contract decision.
- The earlier simulated status-selector failure is corrected in updated main
  #515: its [array includes DL_ACTIVE](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/7533ac2f2ac28f76d07752d9f5bc4dfee6a3b3b5/subsystems/pcie/pcie_ep_sim.c#L32)
  and a [simulator test](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/7533ac2f2ac28f76d07752d9f5bc4dfee6a3b3b5/subsystems/pcie/tests/src/pcie_sim_tests.c#L89)
  exercises status reads and writes. The release branch has no PCIe simulator;
  #522 instead adds [provider tests](https://github.com/Elmnt-Internal/ip.mng.sw.fw/blob/e9cb9ae64e5719ba94132a66fc79296276aa69e4/tests/el_mw_tests/src/el_unit_pcie_link_tests.c#L54)
  with a fake EP, covering state conversion, unwired ports, numeric values,
  invalid arguments, and driver errors.
- The authors report isolated release builds, unit tests, and lint passed.
  GitHub reports successful build/lint checks for both release heads, but their
  `ci_test` jobs were skipped. No PR-head tests were independently run, and no
  new hardware or end-to-end customer PLDM test was reported.
- The pair removes the ownership rejection only for link information. When
  ownership is enabled, [RAS-DES and uncached device-info reads](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/blob/f8cc901eb782c510c0ef9663ba3b23d1352e72dd/include/dwc_ep_ctl_api.h#L607)
  remain ownership-gated. Other initial provider failures can still leave
  attributes INITIALIZING without a retry from normal reads or periodic
  updates; other explicit updates can promote them. The sampled-latch caveat,
  separate EP OEM Get Status stub, and link-speed encoding mismatch also remain.
  The actual cause of the customer EP0 zero is still unconfirmed.

### In-flight EP #469 and MNG #515 (reviewed 2026-11-04)

The open, dependent PRs were inspected at EP head
`6320656da738a41ed97f76adb81d7c077d14defb` and MNG head
`5a304d33849c6ce3e65caeed7cc0d8fafae74934`. They are not a released fix:

- [EP #469](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/469)
  removes the ownership gate from `dwc_ep_ctl_get_link_info`, exposes
  `dl_active = dll_active || rdlh_link_up`, and latches `link_state` once a
  link-info read sees `dl_active`. The latch only observes sampled reads; an
  entire up/down interval between reads can be missed. The PR attributes its
  latched semantics to the DMS; the A1 table checked above establishes
  Enabled/Disabled, but its stronger latch requirement was not independently
  located.
- [MNG #515](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/515)
  selects `PCIE_LINK_INFO_DL_ACTIVE` for status and maps device-backed raw
  state/status booleans to PLDM values 1/2. Together these changes address the
  normal EP0 state/status paths and the link-info ownership rejection, but
  do not implement a general retry after another startup failure. The absent
  EP1 path still returns successful zero before conversion. RAS-DES counters
  retain their separate ownership check; the EP OEM Get status stub and
  speed/generation encoding mismatch also remain.
- The EP author reports an A1 port-0 middleware-shell test with a trained
  Gen5 x8 link in L1: `rdlh_link_up=1`, Link Capabilities
  DLL-Active-Reporting-Capable `=0`, and Link Status `dll_active=0`. With both
  patches, state/status were `1/1` while up, `1/2` just after host reset,
  `2/2` after a subsequent MNG reboot before training, then `1/1` after
  training. This is author-reported MW-shell evidence, not an independently
  executed customer or end-to-end PLDM test. The customer EP0 cause remains
  unconfirmed.
- **Simulator regression, established by source inspection:** the new
  `PCIE_LINK_INFO_DL_ACTIVE` enum follows `PCIE_LINK_INFO_LINK_STATE`, but
  `pcie_ep_sim.c` still sizes its array as `LINK_STATE + 1`; both the simulated
  getter and `pcie_ep_sim_set_attr` reject the new status selector with
  `-EINVAL`. This affects simulated ports with `CONFIG_EL_MW_PCIE_SIM=y`,
  including the existing `pcie_sim_prj.conf`, not the normal ASIC backend.
  Existing simulated link-info tests cover speed rather than the new status
  selector. No PR-head build or test was independently run.

### PCIe EP current link speed unit mismatch (release 0.5.231; PH-018)

The EP getter returns the same link-status speed encoding for both current
speed and current generation (`el_pcie_ep.c:252-257`); the controller extracts
that encoding from the PCIe link-status register (`dwc_ep_ctl.c:1981-1983`).
For mocked Gen4 x8, the released PLDM path returns speed/generation/width
4/4/8. However, the speed PDR declares bytes with modifier 6 per second and
resolution 1 (`pldm_numeric_sensors.dtsi:1919-1945`), while the A1
Manageability Specification table index 60 describes link speed in GT/s
separately from generation. The code is therefore not returning the advertised
rate; the intended per-lane versus aggregate conversion needs confirmation
before choosing a fix. This speed defect is distinct from PH-014's zero symptom.

### OEM PCIe EP Get status is an unsupported stub (release 0.5.231)

For a valid `OemPcieLinkControl` Get status request targeting EP, the released
MNG backend selects `pcie_link_unsupported_get` and maps `-ENOTSUP` to PLDM
completion 0x05 (`el_oob_oem_hw_pcie.c:60-67,145-171`). Both full-ASIC and
single-die release ELF dispatch tables select the stub; executing the full-ASIC
ELF's `el_oob_oem_pcie_link_status` in isolation returned 0x05. This matches
the reported symptom, conditional on the actual request and deployed image.

Open EP PR [#463](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/463)
adds a status API, but the MNG OEM backend still needs wiring. The separate MNG
OEM PR [#504](https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/504), at head
`8af3ee5bdac2e957f44733d1767c34d7fa8044b3`, retains an EP no-backend stub.

### Successful zero is not proof of a hardware read (release 0.5.231)

Verified 2026-11-03 against the official source archive and `mng_asic_fw.elf` for
`fw_release_1.741.0-01112026-163934`, manifest `iridium_release_0.5.231`:

- The ASIC overlay wires only `devices = <&pcie_ep0>` into `pcie_mw_subsystem`.
  The shipped `pcie_read_port_count` returns 1. EP1 providers deliberately return
  zero with success without accessing hardware (`pcie_providers.c:67,138,173,207`).
- EP0 link, device-info and counter providers require `device_is_ready(dev)`.
  A not-ready EP returns `-ENODEV`; a provider left INITIALIZING returns
  `-EAGAIN` in `el_mw_get_attribute_locked` before reaching that check.
- The shipped in-band `el_ib_get_att` suppresses middleware read errors under
  `CONFIG_EL_IB_IGNORE_ATTR_READ_ERRORS`, copies its initially zero value, and
  returns success (`services/inband/el_ib/api/el_ib_api.c:30-60`). This branch
  was verified in the release ELF, not inferred solely from Kconfig defaults.
  UART signature: `Attribute ID %d read failed (%d), returning 0`.
  Do not assume the PLDM/OOB path shares this error-masking behavior.
- `PcieLinkStateL0` (346) is a boolean derived from SMLH up AND RDLH up AND
  LTSSM == L0 (`dwc_ep_ctl.c:1977,2002`), distinct from `PcieLinkStatusL0`
  (348), which reads DLL_ACTIVE. Neither is the OEM link-control status enum.
- The released `el_mw_read_attribute` calls the provider path without the DB
  snapshot/cache-hit branch. Do not blame the latent cache issue IPMNGSW-716
  for this release without new evidence.
- Diagnostic controls: port count (345) needs no ready EP and should be 1;
  vendor ID (1), link state (346), DLL_ACTIVE (348), current speed (350) probe
  the device-backed path. Collect `mw info` as well as `mw get`: shell GET can
  itself fall back to the DB for `-ENOENT`/`-ENOTSUP`.

These are verified implementation mechanisms, not a complete explanation of
every observed customer value. For PLDM, in-band error masking is not a direct
explanation; the separate OEM and sensor paths need not share a root cause.
Hardware reproduction and the detailed customer PLDM response remain unavailable.

## Learnings

Overall PLDM success does not prove a provider read succeeded: inspect the
operational state as well as the value, and test recovery after a transient
startup failure. A successful explicit retry can expose a stranded
INITIALIZING attribute even when the underlying device is now ready. Do not
equate a PCIe generation encoding with a transfer rate or treat synthetic MMIO
as evidence of a customer's runtime state.

## Sources
- FW: `ip/mng/subsystems/su/su_subsystem.c` (`su_read_link_status`, `SU_LINK_STATUS_PROVIDER`)
- FW: `ip/se/drivers/scale_engine/el_se_orch.c:1216` (`el_se_get_port_link_status_flat` → `dwc_dm_ctl_get_link_status`)
- FW: `ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c:1382` (`dm_get_link_status`, debug link-up + LTSSM L0)
- FW: `ip/mng/subsystems/pcie/pcie_providers.c:159` (`pcie_read_link_info`)
- FW: `ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep.c:229` (`el_pcie_ep_get_link_info_impl`)
- FW: `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c:1969` (`dwc_ep_ctl_get_link_info_impl`)
- FW: `ip/mng/subsystems/pcie/pcie_subsystem.c:173`
- FW: `ip/mng/modules/el_middleware/core/el_mw_init.c:82`,
  `attribute/el_mw_attribute_provider.c:82`, `attribute/el_mw_attribute_access.c:29`,
  `periodic/el_mw_periodic.c:183` (startup sweep and retry gates)
- FW: `ip/mng/tests/el_integration_tests/pytest/test_commands.py:811`
  (sensor-existence test does not assert operational or present state)
- Design doc: `ip/mng/docs/design-scale-up-subsystem.md` §§4, 7.3, 8
- DTS: `proj_infra/soc/dts/a1/a1_mw_attributes.dtsi`,
  `mng_generated/pldm_state_sensors.dtsi`, `mng_generated/pldm_numeric_sensors.dtsi:1919`
- Controller databook: `DWC_pcie_ctl_ep_databook.pdf`, PDF pp. 475, 683,
  Table 29-9 (`rdlh_link_up` is a data-link up/down indicator).
- In-flight EP head `6320656da738a41ed97f76adb81d7c077d14defb`:
  `include/el_pcie_ep.h:39`, `include/dwc_ep_ctl_api.h:674`,
  `drivers/snps_ep_ctl/dwc_ep_ctl.c:1864`, `drivers/el_pcie_ep/el_pcie_ep.c:229`;
  the [PR description](https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/469)
  contains the author-reported A1 measurements.
- In-flight MNG head `5a304d33849c6ce3e65caeed7cc0d8fafae74934`:
  `subsystems/pcie/pcie_subsystem.c:173`, `subsystems/pcie/pcie_providers.c:188`,
  `subsystems/pcie/pcie_ep_sim.c:32`,
  `subsystems/pcie/tests/src/pcie_sim_tests.c:31`,
  `tests/el_mw_tests/pcie_sim_prj.conf:7`,
  `services/el_oob/src/el_oob_oem_hw_pcie.c:147`.
