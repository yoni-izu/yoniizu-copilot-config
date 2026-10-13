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

- **Precise data-bus fault during a register dump: decode BFAR's offset and access
  permissions before calling it a hole.** A1 PEP core `+0x10` (BFAR `0xf0440010`)
  is the implemented write-only `INTR_SEI_INJECT` CSR (`__SW_ACCESS=1`), so a linear
  hexdump reads an illegal address despite a valid devicetree window. Enumerate
  reviewed readable offsets and reject unlisted offsets in the owning driver
  before MMIO; do not probe holes or attempt to continue after hard faults.
  Check generated array-level attributes when elements lack their own metadata.
  CSR evidence and an actual-helper/MMIO-spy host test verified the rejection;
  silicon retest remains required. See PCIe knowledge `diagnostic-csr-read-safety.md`.
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
  - *Cold-init handshake timeout — ANCHOR **IPSESW-308** (root cause CONFIRMED, `ip-se-firmware`).*
    `MBOX_INIT` (opcode 511, **msg id 0** = first message) or boot-status poll times out.
    `el_psoc_wait_for_boot_ready()` polls for `EL_FW_BOOT_STS_DONE (0x2)`; **"component ready state 0x1" =
    boot stuck BELOW done (partial)**, not a multi-component bitmask. Then `fw_channel_init("PSOC_MNG")`
    `MBOX_INIT` no-ack → `Failed to init FW channel PSOC_MNG_0` → `-110`. **Root cause (Lauterbach, Akshay
    Bharad, 08-2026): the ARM (PSOC/SE) FW takes a Zephyr USAGE FAULT ("Illegal use of the EPSR" / unaligned
    / PC=0) at boot — the UART/shell IRQ handler does `dev->api->fifo_read(...)`, reading a function pointer
    out of the UART device struct that holds garbage (e.g. `0x12`) → jump to invalid PC. It is UART
    device-struct MEMORY CORRUPTION, not a logic bug; coding-side (vector table, driver, args, thread) all
    ruled out. Heisenbug ~1/3 boots — a spin-trap in `uart_callback()` masks it.** The ARM halt is why the
    host KMD `MBOX_INIT` never acks (host log is only the downstream KMD view). **This overturns the old
    "Palladium timing, not a defect" read** — it's a real ARM-FW defect, proven because the **HOST FW flavor
    never shows it** and even a 360000 ms mailbox timeout still never acks.
    - **FIX CONFIRMED — `project.iridium.sw.fw.infra` PR #1110 (merged 2026-08-24), `[IPSESW-308] set uart
      prio same as rest of isrs`, one line in `boards/arm/a1/el_arm.dts`.** The real mechanism is now
      authoritative: the **UART IRQ was left at priority `0x1` while every other ISR is `0`; when the UART
      ISR preempts, it corrupts the stack** of whatever thread was running. So the *surface* fault is
      RANDOM — it can be the USAGE-fault/jump-to-garbage in `el_se_link_init_tid` OR a Data-Access-Violation
      MPU fault in a totally different thread (a clobbered stack-local pointer gets dereferenced). **Because
      the fix is a blanket IRQ-priority change, it eliminates ALL of these manifestations at once — which
      means you CANNOT tell a 308 repro from a distinct bug by the crash signature; the discriminator is the
      FW image VERSION (does it carry #1110, i.e. built after 08-24 / > `1.684.0`?).** A crash on a pre-#1110
      image with any stack-corruption shape is very likely 308; the same crash on a confirmed post-#1110
      image is a NEW bug. The clean test is a re-run on a post-#1110 image, not more log archaeology.
    - **Two sub-classes:** (1) crash from *remote programming in the SE thread* — **worked around by an
      SE-LPBK pallad image** (`..._se_lpbk` passes; `..._isr3` / plain `_w_gmem` fail); (2) crash *in
      execution* — still under debug.
    - **Dup chain to know:** MODINFSDKSW **11764 → 12978 → 13013 → IPSESW-308**. Its component drifts
      across the chain (`ip-pcie-firmware-ep` → `ip-psoc-firmware` → `ip-se-firmware`) — the real owner of
      *this* facet is `ip-se-firmware` (the SE/UART FW).
  - **CAUTION — host `MBOX_INIT -110` is a shared ENDPOINT with ≥2 distinct ARM causes. Do NOT dup on the
    dmesg symptom alone; read the ARM `uart.log` + FW `manifest ver:` to disambiguate.** Verified split
    (MODINFSDKSW-12649 vs the IPSESW-308 chain, both edk7-z3a, both `-110`):
    - *(a) el_se Zephyr fault (IPSESW-308):* `uart.log` ends `ZEPHYR FATAL ERROR` in `el_se_link_init_tid`
      → `Halting system` (UART device-struct corruption). Seen on FW `1.684.0-21082026-165020`.
    - *(b) MNG CPU-comms MMU/DMA fault (12649, distinct):* `uart.log` boots FLA-RT/PrimeBoot/Management to
      "boot DONE", then `el_mmu: page translation error VA:0xf3b9e000` + `el_dma_sei_isr` (DMA SEI d2h/h2d)
      at MNG CPU-comms init, and **FW stays alive at the `mng` shell (no halt)**. Seen on FW
      `1.682.0-17082026-204948`. Not the el_se crash; likely a different owner (MNG/CPU-comms + DMA/MMU).
    - *(c) MBOX_RCV_WORKQ MPU fault (MODINFSDKSW-12117 bucket, third distinct cause, CONFIRMED via uart.log):*
      ARM boots fine, then at **runtime** `<wrn> el_mng_comms: Heartbeat lost - no HB received within 5000 ms`
      → `HARD FAULT` (escalation) → `MPU FAULT / Data Access Violation`, **MMFAR `0x60c0c000`**, **PC
      `0x6034650a`** (LR `..0b`, r12 `0xf3be0018`, r3 `0xf0c040c4`, xpsr `0x21000000`), `ZEPHYR FATAL ERROR
      19`, thread **`MBOX_RCV_WORKQ`**. This is a *post-boot* crash (unlike (a) cold-init), likely a
      teardown/use-after-free race in the mailbox-RX workqueue triggered by the MNG heartbeat-loss path.
      **REVISED with the #1110 finding: a Data-Access-Violation from a clobbered stack-local pointer is
      exactly what the 308 UART-priority stack corruption produces in an arbitrary thread — so (c) may be a
      308 manifestation, NOT a separate teardown UAF. Decide by the image version (pre/post #1110), not the
      signature.** Decisive lesson: once the ARM MPU-faults, EVERY subsequent host reprobe fails at
      (opcode 511, msg id 0) on `PSOC_MNG_0` with `-110` — identical to the IPSESW-308 cold-init host
      symptom. One decisive run (33272705912, edk4-z3a, 08-30) showed all three host signatures back-to-back
      on one device: heartbeat-mismatch `-5` (956) → the MPU fault → repeated `MBOX_INIT -110`.** So a host
      `MBOX_INIT/PSOC_MNG_0 -110` is NOT proof of cold-init; only the ARM `uart.log` tells cold-init (a) from
      a post-boot MPU-fault (c).
    - **`EL_MBX - Error: ARM CPU device is NULL` prints in BOTH → it is NOT a discriminator.** The
      discriminators are the *halt vs alive-at-shell* outcome and the MMU/DMA-SEI vs Zephyr-fatal line.
    - **How to get the ARM log:** `gh api repos/Elmnt-Internal/ip.infra.sw.manifests/actions/runs/<run>/artifacts`
      → find the `...pldm-<station>-logs` artifact → download `/zip` → untar → read `uart.log` (ARM console)
      and `dmesg.log` (host KMD). The GH job/step logs are often empty for old sub-jobs; the **artifact** is
      the reliable source. Note some runs ship an **empty `uart.log`** — those runs are *unclassifiable*, not
      a match.
    - **Outcome (MODINFSDKSW-12649):** stays **OPEN / distinct** — *not* merged into IPSESW-308. Its ARM
      signature is a **MNG CPU-comms DMA/MMU fault**, root-caused in code: `mng_cpu_comms.c`
      (`project.iridium.sw.fw.psoc_mng`) → `el_cpu_comms_start()` brings up the PQ/CQ/EQ mailbox; a comms
      DMA hits an **unmapped VA `0xf3b9e000` (ASID 0, hop_num 5 leaf miss)** → `el_mmu` translation-error SEI
      (`ip.mmu.sw.fw`) → `el_dma_sei_isr` resp_error (`ip.dma.sw.fw`, `el_dma_comms_set_user_attr` sets the
      comms MMU-bypass/ASID). Owner = `ip-dma-firmware` + psoc_mng + `ip-mmu-firmware`, **not** `compute-kmd`
      (12653 is only a Polaris idea-datapoint link — a KMD "don't block insmod" feature, NOT a root-cause dup).
      Confirms the discipline: reading the ARM `uart.log` (not the shared `-110` dmesg) kept a distinct DMA/MMU
      mapping bug from being buried under the el_se anchor. **Lesson also: don't trust a verbal "it was
      closed" — verify status via the API before recording a close-out.**
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

### PCIe EP — ARM-FW "EP device not ready" / `-ENODEV` spam during boot
- **Symptom cluster on Palladium + `CONFIG_EL_PCIE_EP` (e.g. 1-die GMEM PLDM):** `el_pcie_ep: EP device
  not ready` (`-ENODEV`/`-19`) fired by THREE consumers together — `el_vme_runtime_gate: Failed to get
  PCIe revision ID, assuming VME not available`, `el_pcie_ep: Failed to warm PCIe EP device-info cache:
  -19`, and `dwc_ep_ras_des: EP device not ready` → `Failed to enable RAS DES`. Fail-soft (all
  `LOG_WRN`), boot completes, but VME is wrongly gated off + RAS-DES counters + revision cache never set.
- **Root cause = a `device_is_ready()`-gated public accessor called before the device is ready-marked.**
  Anchor **IPPCIESW-668**, regressor **`f2a02ed` `[IPPCIESW-443]` (ip.pcie.sw.fw.ep, merged 2026-09-08)**.
  It rerouted the init-time revision warm from a DIRECT child call
  `dwc_ep_ctl_get_revision_id(DEV_CFG(dev)->ep_ctl_device,…)` to the gated wrapper
  `el_pcie_ep_get_dev_info_impl(dev,…)` → `el_pcie_ep_get_ep_ctl_device(dev)` which checks
  `device_is_ready(dev)` (parent) AND `device_is_ready(ep_ctl)`. It also retargeted the VME gate from the
  ungated cached `get_revision_id` to the gated `get_dev_info`, and added RAS-DES `dwc_ep_ctl_ec_enable`
  (gates on ep_ctl readiness) into `el_pcie_ep_post_bring_up_cfg`.
- **KEY Zephyr fact:** `device_is_ready(self)` is **false during a device's own init fn** (`state->initialized`
  is set only after init returns). So any gated PUBLIC-API wrapper called from within `*_init` /
  bring-up / post_bring_up self-`-ENODEV`s every boot. Direct child-device register access
  (`DEV_CFG(dev)->ep_ctl_device`) is immune — it doesn't consult the readiness flag.
- **Lesson / always-check:** never call a `device_is_ready`-gated PUBLIC API from inside init or from a
  runtime gate that runs before the target device is ready. Keep the gate on external entry points only;
  use direct/internal helpers on the init/bring-up path. "1-die GMEM" here was just the tested image — the
  VME-gate PCIe path is `CONFIG_EL_PALLADIUM && CONFIG_EL_PCIE_EP` only (ASIC returns true), so it looks
  image-specific but is generic to Palladium PCIe-EP builds.
- **Do NOT confuse with the host-side family:** this is ARM-FW `el_pcie_ep` init, NOT the host dmesg
  "device did not become operational" / MBOX family. Same 1-die GMEM image can carry BOTH (host-side is
  **IPPSOCSW-1156**, MMU_CACHE_INVALIDATION mailbox timeout, distinct owner/stage — not a dup).

### PCIe EP — MNG-port SEI drain ("Drain activated due to timeout", SLVERR)
- **Decode the drained address first — it names the register and pins whether it's a known benign case.** MNG-port drain `addr_h` is tied 0, so the log's low-32 address decodes directly against a1 `project_bases.h` (base low + offset). Confirmed anchors: `0x19da8` = `BOOT_AND_RST_TOP.RST_MISC.RESET_IN_PROCESS` (MNG); `0x53501c` = `MNG_SE_TOP_7_C2C_CFG_SPACE_C2C_CORE_0 + 0x1c` (C2C/scale-up core config-space).
- **The benign-MNG-drain suppress (PR #267 / MODINFSDKSW-4101) is narrow — READ + RESET_IN_PROCESS + hard-reset one-shot ONLY.** `el_piw.c::el_piw_port_drain_process_cause_impl()` suppresses a drain only when *all* hold: `CONFIG_ARCH_POSIX && CONFIG_EL_IRQ_MON` (host-run), `port==MNG && trans==READ && reason==ACTIVATED`, and `el_irq_mon_consume_post_reset()` returns true. That one-shot is armed **only on `EL_RESET_TYPE_HARD`** (`el_irq_mon.c` `el_irq_mon_reset_cb`, PRE_CFG). Consequences:
  - A **write** drain (e.g. `0x53501c` C2C-cfg write) is **never** suppressed — the guard is read-only. A write-drain re-file is a *new/distinct* facet, not a regression of #267.
  - A **read** drain at `0x19da8` outside the hard-reset one-shot window (soft/warm reset, or after the one-shot was consumed) still reports. "Already fixed" ≠ gone if the load path doesn't raise a *hard* reset.
- **Always ask which reset type the load path issues.** The suppression hinges on `EL_RESET_TYPE_HARD`; a soft/warm reset arms nothing, so the benign read-drain surfaces anyway.
- **Owner ≠ ticket component.** These tickets often land on `ip-psoc-firmware`, but the drain suppress-vs-notify policy is in **`ip.pcie.sw.fw.ep`** (`el_piw.c`); the one-shot arm is in **`ip.infra.sw.fw`** (`el_irq_mon.c`). C2C-cfg targets point at `ip.se.*`. Reassign accordingly.

### Scale-Up (C2C) / D2D
- **`test_d2d_pldm_single_chip_run` returns `False` on pldm-edk0-z3a = the `die_2_die_bw` qual test.
  Decode the FAILURE at the sub-test level before calling it a dup of MODINFSDKSW-11910 — "same test,
  same station, same FC tag" is NOT enough.** The test spawns one worker per die-pair; the real
  verdict is in the per-worker result CSV `reports_Die_2_Die_Test/local/<w>/*_Die_2_Die_Test_*.csv`
  (columns `sub_test_name,status,test-result,fail-threshold,warn-threshold`). Read THAT, not the
  journal (`qual_journal_340.log` only says `Worker N reported failure`) and not the GH job log
  (`--failed-only` mode prints only the journal path).
  - **Distinguish the sub-signatures — they are different bugs:** (a) **hang-then-cancel** (11595:
    die_2_die error then job hung ~3 h to the 300-min timeout, 0 summary) vs (b) **ran-to-completion
    assertion-fail** (11910, and MODINFSDKSW-12719 run 32063981909). Within (b), decode WHICH metric
    failed: a **BW-below-threshold** (`avg_bw` Failure) is a *performance* verdict, totally distinct
    from a data error (all `*_error_count`/CE/UE metrics were `0/Success` in 12719 — no corruption,
    no D2D CE/UE/fatal, no SEI in dmesg during the test window).
  - **Confirmed 12719 signature (record it for comparison):** only 4 sub-tests fail, all
    `vertical-avg_bw-lmem` (read+write) on the **two INNER die-pairs die1,die2 ≈ 275–284 GB/s** vs
    fail-threshold **360**; the **EDGE die-pairs die0,die3 ≈ 400** pass. Horizontal/diagonal/all-gmem
    pass everywhere. A clean ~30% inner-vs-edge vertical-lmem BW asymmetry on image
    `2_18_0_2p0_1240` → either a real inner-die vertical D2D BW limit or an over-uniform qual
    threshold. **This is NOT provably the same as 11910's D2D half — 11910's ticket has no per-op
    breakdown.** To confirm/deny identity, pull 11910's run artifact CSVs and compare failing
    sub-tests.
  - **11910 was closed on an EDP-only fix.** Qual PR #342 (`project.iridium.prod.qual`) touched only
    the EDP `--duration` switch + a plot decode — **never the D2D path**; the D2D failure was
    separately masked by a test-reorder (Idan Pazi). So `EDP result: True` + `D2D result: False` on
    the same image is expected, and the D2D BW assertion has been open the whole time. Reinforces
    "Fixed ≠ gone while FC tag unchanged" — the merged fix wasn't even a FW/FC change.
  - **Owner is the D2D qual/val side** (el-qual / val-d2d — Omri Zeneva / Idan Pazi / Tomer Mankita),
    NOT `ip-d2d-firmware` (the auto-filed component is usually wrong here).
  - **Artifact gotcha:** the station-log tarball keeps only SKELETON per-worker `qual.log`s
    (session-start line only) AND a truncated in-test-dir dmesg (ends at test start) — but the
    **result CSVs and per-op `qual_metrics/*_bw_result.json` DO survive** under
    `reports_Die_2_Die_Test/local/<w>/`. Grep the CSVs for `Failure`; the top-level `dmesg.log`
    (kernel-format `[Www Mon DD HH:MM:SS YYYY]` timestamps) covers the whole run for HW-error
    correlation.

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
