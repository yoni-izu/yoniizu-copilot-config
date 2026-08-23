# Debugging Memoir

Persistent notes on debugging style, failure signatures, and root-cause patterns for the HSI FW
codebase. Grows with every investigation — captures symptom→root-cause mappings, "always check"
items, and environment traps so recurring bugs are recognized instantly.

Read this in full before every investigation (Step 0). Fold new, reusable lessons back in after
(Step 9) — consolidate under the right theme; keep entries tight and principle-first.

---

## Debugging Philosophy

- **Reproduce before theorizing.** A hypothesis you can't reproduce is a guess. If it won't
  reproduce, that non-determinism is itself the clue (race, uninit state, env dependence).
- **Verify against the code path, not the symptom.** Read callers, init order, and the state the
  failing line assumes — not just the crashing line.
- **Regression vs latent is the first fork.** A regression → bisect the manifest/commit history. A
  latent/new bug → reason from the code and the spec. Don't bisect a bug that never worked.
- **Label every finding.** *Confirmed* (reproduced / reverted-and-proved), *likely* (strong
  evidence, not proven), *unverified* (hypothesis). Never present a guess as a fact.
- **Check the spec before blaming HW code.** The register field semantics (RW1C, RO, sticky, reset
  value) frequently flip the conclusion — pull them from the IP skill first.
- **Confirm the root cause by reverting it.** The gold standard is: revert the suspected change on
  the bad build and watch the failure vanish. Correlation of a commit in a bisect is not yet proof.
- **Isolation is non-negotiable.** Never probe/edit/bisect in the user's active dev workspace or over
  a dirty tree — in-progress work is sacred.

## Always Check

- **Init order & stage.** Which boot stage (FLA.RT / PRIMEBOOT / MANAGEMENT / POWER&THERMAL) owns the
  code, and whether the failing code assumes state a *later* stage sets up.
- **Reset/value assumptions.** Does the code assume a register/reset value that the current variant or
  reset domain doesn't guarantee?
- **Cross-repo skew.** A regression with no local code change is often a *dependency* repo moving —
  diff the manifest, not just the app repo.
- **Env vs code.** Same source, different result across sim/palladium/onix/asic → environment,
  timing, or uninitialized-memory dependence, not a source bug.
- **Known-issue first.** Search Jira (incl. recently-closed) before a deep dive — a re-opened or
  fixed-then-broken bug shortcuts the whole investigation.

## Failure Signatures

_Symptom → root cause → fix. Grouped by theme/IP. Add entries as they're confirmed._

### Boot / Reset / Init
_(none yet)_

### PCIe EP / PHY
- **"device not appearing" (no `208b` on the bus) ≠ "device did not become operational" (probe timeout) —
  they are different failure *stages* with different owners. Do not lump them.**
  - *Device ABSENT* — log shows **`Detected Simulator device`** then `no PCI device with vendor 208b was
    found - refusing to run simulator`. The atl endpoint never appeared on the PCI bus, so the KMD refuses
    to bind — the failure is *before* enumeration. A healthy detection instead prints `Detected Palladium
    device (revision: 0xNN)` + `atl device found [208b:0001]`. Absence ⇒ **emulation/station bring-up**
    (design/image not loaded on that Palladium box, host PCIe transactor-link down, or the design crashed
    and dropped the link) — owned by station/emulation infra + `ip-pcie-kmd`/emulation-utils, **not**
    `ip-pcie-firmware-ep`.
  - *Device PRESENT, FW didn't boot* — log shows `atl device found [208b:0001]` then `Waiting for firmware
    boot…` → `probe with driver atl failed with error -110` (ETIMEDOUT) / `-5` (I/O). This is the
    `ip-pcie-firmware-ep` FW-boot family (anchor **MODINFSDKSW-11548**).
  - **Decisive test to tell them apart: check a *sibling station in the same run/manifest/FC image*.** If a
    sibling enumerated `208b` fine (e.g. MODINFSDKSW-12003 run #30850615308: `edk0-z3a` saw *no device* /
    "Detected Simulator device" while `edk0-z2a` on the identical image printed `Detected Palladium device
    (rev 0x23)` + `[208b:0001]`), then the image's FW can enumerate — so a device-absent station is a
    **per-station emulation-instance** problem, **not** a systemic image/FW bug, and **not** a duplicate of
    the FW-boot family. Same FC image enumerating on one station and absent on another is the tell.
- **Palladium "device did not become operational" (device PRESENT) is a *family*, not one bug.** On the
  `regression-ci.xml` / FC-image line (FC tag `2_18_0_2p0_1240`) it surfaces as stage-signatures that flip
  run-to-run on the *same* station/image once the device is on the bus: (b) `atl ... probe failed with
  error -110` (ETIMEDOUT) / "component ready 0x1" — FW didn't boot; (c) `probe ... error -5` (I/O). Classify
  by **FC tag + failure stage**. Distinguish the true outliers: `el_pci.ko "Invalid module format"` =
  kernel-ABI/KMD mismatch (component `ip-pcie-kmd`, distinct), and `PSOC_MNG_0 FW channel init failure` /
  `hw_dirty not cleared by FW` = mgmt/PSOC-FW (component `ip-psoc-firmware`, distinct).
- **The device-PRESENT failures split by PHASE — cold-init vs steady-state — and are mostly KMD-timing
  artifacts exposed by Palladium's slow ARM firmware, not silicon/FW defects.**
  - *Steady-state heartbeat race → **IPPSOCSW-956** (KMD, `ip-psoc-kmd`, fix In Review).* The PSOC heartbeat
    has two responses — synchronous **CQ** reply (`hb_seq++`) and async **EQ** event (`hb_eq_seq++`). Guard
    `if (hb_seq != hb_eq_seq) return -EIO` (`el_psoc_fw_if.c`) is over-strict: on slow Palladium the CQ
    reply advances before the EQ event is processed → spurious `-EIO`. And `elp_heartbeat_work()`
    (`elp_main.c`) does `return;` on any error **without re-queuing**, so ONE transient skew permanently
    kills heartbeats → FW `hb_timer` fires → reset. Signature: `PSOC heartbeat sequence mismatch with EQ:
    N vs. N-1` / `PSOC_HEARTBEAT` timeout `-110`/`-5` then reset `-5`/`-16` (e.g. MODINFSDKSW-11763).
  - *Cold-init handshake timeout (no dedicated anchor yet).* `MBOX_INIT` (opcode 511, **msg id 0** = first
    message) or boot-status poll times out. `el_psoc_wait_for_boot_ready()` polls for
    `EL_FW_BOOT_STS_DONE (0x2)`; **"component ready state 0x1" = boot stuck BELOW done (partial)**, not a
    multi-component bitmask. Then `fw_channel_init("PSOC_MNG")` `MBOX_INIT` no-ack → `Failed to init FW
    channel PSOC_MNG_0` → `-110` (e.g. MODINFSDKSW-11764, -12117, -12058). Mechanism (FW hang vs
    timeout-too-short vs KMD bug) needs the **PSOC FW-side UART/console log** — the GH runner log is only
    the host KMD view.
  - **Decode discipline:** the exact log strings (500 s timeout, "component ready state") come from the KMD
    rev **baked into the FC image**, which can differ from the local `main` KMD tree — grep both, and cite
    which tree you read.
- **Anchor family root-cause in the *Known Error* ticket.** The oldest/most-analyzed ticket (resolved
  *Known Error*, e.g. MODINFSDKSW-11548) usually carries the candidate fix-set PR list; a new re-file
  inherits it. A ticket whose *exact run-id + station + error* already appears in another ticket's comment
  thread is a re-file → duplicate.
- **The family has *two* FW root-cause anchors, split by sub-signature — resolve to the right one.**
  PCIe-EP `-110`/`-5` probe-timeout facet (device present, FW didn't boot) → **MODINFSDKSW-11548**
  (`ip-pcie-firmware-ep`, Known Error). FW-boot `hw_dirty`-not-cleared / mailbox-timeout facet →
  **IPPSOCSW-956** (`ip-psoc-firmware`); e.g. MODINFSDKSW-12409 was closed as a duplicate of it. Component
  on the re-file (`ip-pcie-firmware-ep` vs `ip-psoc-firmware`) is the tell for which anchor a given re-file
  inherits. **A device-absent (`no 208b`) re-file belongs to *neither* — it's emulation/station infra
  (see the "device not appearing ≠ did not become operational" entry above).**
- **"Fixed" ≠ gone while the FC image tag is unchanged.** A dev declaring the FW issue fixed only takes
  effect once a *new FC image* (tag > the bad one) is rolled. As long as re-files carry the **same FC tag**
  (`2_18_0_2p0_1240`), the latent bug is still baked in and regressions keep reproducing — weeks later,
  across stations. For the manager, the honest status is "fix identified, pending a rebuilt FC image," and
  the real close-out is a re-run on a newer FC tag, not per-station re-triage.

### PCIe EP — MNG-port SEI drain ("Drain activated due to timeout", SLVERR)
- **Decode the drained address first — it names the register and pins whether it's a known benign case.** MNG-port drain `addr_h` is tied 0, so the log's low-32 address decodes directly against a1 `project_bases.h` (base low + offset). Confirmed anchors: `0x19da8` = `BOOT_AND_RST_TOP.RST_MISC.RESET_IN_PROCESS` (MNG); `0x53501c` = `MNG_SE_TOP_7_C2C_CFG_SPACE_C2C_CORE_0 + 0x1c` (C2C/scale-up core config-space).
- **The benign-MNG-drain suppress (PR #267 / MODINFSDKSW-4101) is narrow — READ + RESET_IN_PROCESS + hard-reset one-shot ONLY.** `el_piw.c::el_piw_port_drain_process_cause_impl()` suppresses a drain only when *all* hold: `CONFIG_ARCH_POSIX && CONFIG_EL_IRQ_MON` (host-run), `port==MNG && trans==READ && reason==ACTIVATED`, and `el_irq_mon_consume_post_reset()` returns true. That one-shot is armed **only on `EL_RESET_TYPE_HARD`** (`el_irq_mon.c` `el_irq_mon_reset_cb`, PRE_CFG). Consequences:
  - A **write** drain (e.g. `0x53501c` C2C-cfg write) is **never** suppressed — the guard is read-only. A write-drain re-file is a *new/distinct* facet, not a regression of #267.
  - A **read** drain at `0x19da8` outside the hard-reset one-shot window (soft/warm reset, or after the one-shot was consumed) still reports. "Already fixed" ≠ gone if the load path doesn't raise a *hard* reset.
- **Always ask which reset type the load path issues.** The suppression hinges on `EL_RESET_TYPE_HARD`; a soft/warm reset arms nothing, so the benign read-drain surfaces anyway.
- **Owner ≠ ticket component.** These tickets often land on `ip-psoc-firmware`, but the drain suppress-vs-notify policy is in **`ip.pcie.sw.fw.ep`** (`el_piw.c`); the one-shot arm is in **`ip.infra.sw.fw`** (`el_irq_mon.c`). C2C-cfg targets point at `ip.se.*`. Reassign accordingly.

### Scale-Up (C2C) / D2D
_(none yet)_

### RAS / Manageability
_(none yet)_

### Cross-IP / Project-Level
_(none yet)_

## Environment & Tooling Traps

### Manifest Bisection
- Manifest `version=` is timestamped (`<semver>-<DDMMYYYY>-<HHMMSS>`) — **sort by the timestamp**,
  not lexically by semver, to order candidates for the binary search.
- A good→bad manifest step usually moves exactly one repo's `revision`; diff the manifests to find it
  before falling back to a per-repo `git bisect`.

### Build / Emulation
_(none yet)_

### CI Logs
- CI runs **only** on a `/ci` comment (not on open/push); close/reopen does not re-trigger it.
- `gh run view <run-id> --log-failed` is the fast path — read failed steps first, widen only for context.
- Stale check-run trap: `statusCheckRollup` counts superseded runs on the same SHA. Trust
  `gh pr checks` (latest-per-context) or `gh run list -b <branch> -L1` for the true state.
- A failure that reproduces with `build-fw` locally is a source bug; one that only fails in CI points at
  manifest/env skew → bisect the manifest, not the source.
