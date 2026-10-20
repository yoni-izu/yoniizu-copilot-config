# SE link-init: two independent "external config" waits (conn-scheme vs MW LUT)

**Status:** Verified from FW source (`ip.se.sw.fw`, `ip.mng.sw.fw`) on
`iridium_release_0.5.15` · **Recorded:** 2026-10-14

## Summary

The Scale Engine link-init thread blocks on **two different** pieces of
externally-supplied configuration. They are frequently confused because a single
Kconfig symbol used to gate both, but only **one** of them is actually a
middleware (BMC) transfer.

| | Wait A — connection scheme | Wait B — MW LUT tables |
|---|---|---|
| Payload | one enum: which port↔peer wiring scheme the board is in | per-die NPA (64-bit BARs) + BDF (16-bit requester IDs) arrays |
| Semaphore | `el_se_scheme_sem` (`el_se.c:1423`) | `mw_lut_ready` (`el_se_lut.c:748`) |
| Wait site | `el_se.c:1597/1601` — **before** the deferred `device_init()` | `el_se.c:1658` — after init, before `el_se_lut_program()` + LTSSM enable |
| Timeout Kconfig | `EL_SE_SCHEME_WAIT_TIMEOUT_MS` (5 ms PLD-ARM / 100 ms PLD-POSIX / 500 ms) | `C2C_LUT_MW_TIMEOUT_MS` |
| Setter API | `el_se_set_conn_scheme()` (`el_se.c:1917`, exported `el_se.h:66`) | `el_se_lut_commit()` + `el_se_lut_signal_mw_ready()` |
| **Real producers** | board strap `el_se_strap_get_scheme()` (`el_se_strap.c:102`, checked first and short-circuits the wait) · SE shell (`el_se_shell.c:1269`). **No middleware caller exists** — a workspace-wide grep finds zero callers in `ip/mng`, `el_modules` or any OOB/PLDM path | **BMC over PLDM OemDataLoad** → `soc_load_npa_table()`/BDF → `soc_lut_commit_if_complete()` (`soc_providers.c:1170-1177`) under `CONFIG_EL_OOB_WITH_BMC` (default) · or the sample filler `el_se_lut_sample.c:144` under `CONFIG_EL_OOB_STANDALONE` |
| Fallback | devicetree `default-conn-scheme` | `se_lut_seed_dts_defaults()`, gated by `C2C_LUT_DTS_FALLBACK` |

**Takeaway:** "both come from MW" is wrong. Only Wait B has a middleware
producer. Honouring Wait A costs a flat timeout with no possible benefit in any
current image.

## The IPSESW-379 dead-config trap

`el_se.c` (top of file) contains a hard override:

```c
#undef CONFIG_EL_SE_SKIP_EXTERNAL_CONFIG_WAIT
#define CONFIG_EL_SE_SKIP_EXTERNAL_CONFIG_WAIT 1
```

Consequence: `skip_external_wait` (`el_se.c:1571`) is **always true on every
build**. Before the fix below, that forced `timeout = 0` at the LUT wait, which
made **`CONFIG_C2C_LUT_MW_TIMEOUT_MS` dead on all platforms** — changing its
value produced a bit-identical runtime behaviour.

> **Debugging rule:** before trusting any SE "timeout" Kconfig, check whether
> `el_se.c` `#undef`/`#define`s its gate. This file carries power-on bring-up
> overrides that silently outrank Kconfig.

## Resolution (commit `4bfebd3`, branch `dev/yoni.izutskiver/se-lut-mw-timeout-10min`)

The two waits were split onto separate gates:

- `EL_SE_SKIP_EXTERNAL_CONFIG_WAIT` — keeps the IPSESW-379 blanket skip, now
  scoped to the **conn-scheme wait only**.
- `EL_SE_SKIP_LUT_MW_WAIT` (new) — gates the **LUT wait**.
  `default n if EL_ASIC && !ARCH_POSIX`, else `default y`.
  The `!ARCH_POSIX` term matters: `CONFIG_EL_ASIC=y` is also set by the
  *native/sim* asic boards (`proj_infra/boards/native/el_host_a1/el_host_a1_asic_defconfig:10`),
  which have no BMC — without the exclusion a host build would block too.
- `C2C_LUT_MW_TIMEOUT_MS` — added `default 600000 if EL_ASIC` (10 minutes).

Timeout selection is now (`el_se.c`, LUT phase):

```c
if (IS_ENABLED(CONFIG_EL_SE_SKIP_LUT_MW_WAIT))      timeout = 0;
else if (IS_ENABLED(CONFIG_C2C_LUT_DTS_FALLBACK))   timeout = CONFIG_C2C_LUT_MW_TIMEOUT_MS;
else                                                timeout = UINT32_MAX;   /* K_FOREVER */
```

## Why the ASIC timeout must be long

`el_se_lut_commit()` (`el_se_lut.c:708`) returns **`-EACCES`** once
`state.die[die].hw_programmed` is set. So if the DTS fallback fires *before* the
BMC delivers, the BMC's real tables are rejected outright and the chip runs on
devicetree routing. A short timeout on silicon is therefore not "fail fast", it
is "silently route wrong".

## Cost of disabling the skip

With no table producer present, link init blocks the **full** timeout before the
DTS fallback, delaying `el_se_lut_program()`, LTSSM enable, and the `se_ready`
device-state gate. On ASIC with 600000 ms that is a 10-minute quiet boot. A
`LOG_WRN` was added at the wait so the quiet period is attributable.

Note `se_ready` is currently raised right after LTSSM enable, *before* link
training completes (TEMPORARY, IPSESW-379, `el_se.c` Phase 2 comment) — so the
MW-wait delay propagates straight into KMD-visible boot-done.

## `el_mng_get_tray_id()` is NOT ordered against the LUT MW wait at every call site

`el_mng_get_tray_id()` (`ip/mng/include/el_mng_device_info.h:54`) is currently a
stub (returns 0, or the `ARCH_POSIX` CLI value) — it carries **no** semaphore
of its own. *If* it were ever wired to an async BMC-delivered value (the same
style as the LUT tables), callers would need to be checked individually for
ordering against `mw_lut_ready`; there is no blanket guarantee:

| Call site | Called from | Runs relative to `el_se_lut_wait_for_mw()` (`el_se.c:1897`) | Safe if async? |
|---|---|---|---|
| `el_se_get_chip_id()` → `el_se_program_fiu_chip_id()` (`el_se.c:1059,1065`) | `el_se_configure_fiu()` (`el_se.c:1443`), called inside `el_se_init()`, itself invoked by `device_init(dev)` at `el_se.c:1863` | **Before** — `device_init(dev)` (1863) precedes the `el_se_lut_wait_for_mw()` call (1897) in `el_se_link_init_thread_fn()` | **No** — reads tray_id before the LUT-ready rendezvous; would race |
| `se_lut_get_chip_id()` (`el_se_lut.c:170,174`), called from the REMOTE_REQ_ID programming helper (`el_se_lut.c:494`), itself called as `el_se_lut_program_all_remote_req_ids()` (`el_se.c:1956`) | Phase 2, after LTSSM enable | **After** — `el_se_lut_wait_for_mw()` (1897) and `el_se_lut_program()` (1904) have both already returned by Phase 2 (1956) | Yes — ordered after the rendezvous |
| `el_mng_comms_get_device_info()` (`el_mng_comms.c:179`) | On-demand mailbox/EQ RPC handler (host/BMC query), no caller relationship to SE link-init at all | **Unordered** — can fire at any time, independent thread/context | No — no synchronization with the LUT wait exists or is implied |

**Takeaway:** do not assume "it's read after the LUT semaphore" just because one
call site in the same file happens to be. Each caller of `el_mng_get_tray_id()`
must be checked individually; the FIU chip-id programming path in particular
runs *before* the MW rendezvous, not after.

## Build/verification gotchas hit while doing this

- `octl ws lint ip/se` **cannot run in a never-built workspace**: starfiles load
  `build_artifacts/include/projects/csr_versions.star`, which only exists after a
  build. Error surfaces as `cannot load build_framework/fw_build.star`.
- `mctl mirror` can report `✔ mirror aligned` while the remote repo is still on
  the old branch. The saved map
  (`~/.wip/saved/<tag>.wip`, tag = path under `$HOME` with `/`→`_`) was correct,
  but the remote `wip ws checkout` did not take. **Always re-probe the remote
  branch/SHA after mirroring** — do not trust the success line.

## Citations

- `ip/se/drivers/scale_engine/el_se.c` — IPSESW-379 override (top of file),
  `skip_external_wait` 1571, conn-scheme wait 1597/1601, LUT timeout select ~1652
- `ip/se/drivers/scale_engine/el_se_lut.c` — `el_se_lut_commit()` 708 (`-EACCES`),
  `el_se_lut_signal_mw_ready()` 743, `el_se_lut_wait_for_mw()` 748
- `ip/se/drivers/scale_engine/el_se_strap.c:102` — strap scheme source
- `ip/se/drivers/scale_engine/el_se_shell.c:1269` — shell scheme setter
- `ip/se/drivers/scale_engine/el_se_lut_sample.c:144` — standalone sample producer
- `ip/mng/subsystems/soc/soc_providers.c:1170-1177` — BMC/PLDM producer
- `ip/mng/include/el_mng_device_info.h:43-58` — `el_mng_get_tray_id()`/`el_mng_get_rack_id()` stub (0 or ARCH_POSIX CLI value)
- `ip/se/drivers/scale_engine/el_se.c:1059,1065,1443,1863` — FIU chip-id path calls `el_mng_get_tray_id()` **before** the LUT MW wait (1897)
- `ip/se/drivers/scale_engine/el_se_lut.c:170,174,494` — REMOTE_REQ_ID path calls `el_mng_get_tray_id()` **after** the LUT MW wait (via `el_se.c:1956`, Phase 2)
- `ip/mng/drivers/el_mng_comms/el_mng_comms.c:179` — on-demand RPC call site, no ordering relationship to the LUT wait at all
- `ip/mng/services/el_oob/Kconfig:155-184` — `EL_OOB_TABLE_SOURCE` choice
- `infra/Kconfig:45-67` — `EL_ASIC`/`EL_FPGA`/`EL_PALLADIUM` platform choice
- `proj_infra/boards/native/el_host_a1/el_host_a1_asic_defconfig:10` — host board sets `EL_ASIC`
