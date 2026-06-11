# PR #248 — [IPPCIESW-334] Add handling of warm reset callbacks
- Repo: Elmnt-Internal/ip.pcie.sw.fw.ep
- PR: https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/248
- Head SHA reviewed: 5fc1c63165c01f6ff3b3a1906b1ca04ead2d9ba6
- Base merge-base: 020c7df60c1defb219bdacaf074b09a39e37fc32
- Drafted: 2026-06-11 12:30
- CI: green · mergeable: CONFLICTING · existing reviews/threads: none

## Recommended verdict: comment (one concurrency gap worth resolving before merge)
Solid redesign that removes an implicit timing hack; one real lost-wakeup window in the
back-to-back-reset path and a HW-timing question on the removed core_rst_n delay.

> STATUS 2026-06-11: findings below PARKED (not posted). Pending separate analysis of the
> `el_pcie_ep_init` CONFIG-guard structure vs documented EP boot responsibilities.

## Findings

### [SUGGESTION] drivers/el_pcie_ep/el_pcie_ep_reset.c — `el_pcie_ep_reset_try_link_init` lost-wakeup window
The `LINK_INIT_IN_PROGRESS` test-and-set acts as a try-lock, and the epoch loop correctly
re-runs `bring_up` when a reset lands *during* `bring_up`. But there is a window between the
worker breaking out of the `do/while` on `!is_ready()` and the final
`atomic_clear_bit(LINK_INIT_IN_PROGRESS)`:

- Worker is finishing reset N's init; reset N+1's PRE_CFG (`reset_begin`) bumped the epoch and
  cleared the bits, so the loop re-checks `is_ready()` → false → `break`.
- Reset N+1's POST_CFG (`piw_deasserted`) or the PERSTn ISR then completes readiness and calls
  `try_link_init`, but the worker still holds `LINK_INIT_IN_PROGRESS`, so it hits the
  `atomic_test_and_set_bit(...)` guard and returns 0 (bounced).
- The worker then clears `LINK_INIT_IN_PROGRESS`. Nobody retries → the link for reset N+1 is
  never re-initialized → host left waiting.

This is exactly the back-to-back-warm-reset case the epoch machinery is built to handle, so
the gap is worth closing. Minimal fix: after `atomic_clear_bit(LINK_INIT_IN_PROGRESS)`,
re-check `el_pcie_ep_reset_is_ready(data) && !atomic_test_bit(DONE)` and re-enter if true;
or carry a "work pending" flag that events set and the worker re-tests before releasing the
in-progress bit. (Concurrency / lost-wakeup; the rest of the state machine is sound.)

### [QUESTION] drivers/el_pcie_ep/el_pcie_ep_irq.c — removed `k_busy_wait(10000)` core_rst_n settle
The old ISR waited ~10 ms "to allow core_rst_n to deassert after the second PERSTn
deassertion" before `bring_up`. The new design instead gates `bring_up` on PERSTn + PIW-reset-
released (POST_CFG). What now guarantees the POST_CFG (PIW deassert) callback fires only
*after* core_rst_n has deasserted? If the reset manager doesn't order POST_CFG after
core_rst_n, `bring_up` (DBI / LTSSM programming) could run while the controller core is still
in reset — i.e. the implicit timing dependency would have moved rather than been removed.
Please confirm the ordering (or that POST_CFG is defined to post-date core_rst_n release).

### [QUESTION] Cross-PR interaction with #249 (CRS/DRS) + CONFLICTING state
PR is `CONFLICTING` against main and touches the same reset surface (`el_pcie_ep_reset.c`,
`el_pcie_ep_internal.h`, the init flow) that #249 restructures (CRS assert/clear across hot
reset). After rebase, please confirm the CRS/DRS hold-off and this warm-reset callback flow
compose as intended on the shared reset path (e.g. CRS is asserted for the warm-reset re-init
window too, not just hot reset).

### [SUGGESTION] drivers/el_pcie_ep/el_pcie_ep_reset.c — "link init" naming is too narrow
The state machine gates a single call, `el_pcie_ep_bring_up()`, which does PIW pre-link cfg →
(linkup, only if `LINKUP_CFG`) → BAR map → drain → expose CDM DBI. So the "link init" naming
(`LINK_INIT_IN_PROGRESS`, `LINK_INIT_DONE`, `try_link_init`, `prepare_boot_link_init`,
`mark_link_init_done`) names the whole thing after one optional sub-step — with `LINKUP_CFG=n`
the machine still runs the rest. Rename to PCIe-EP-reset-scoped terms, e.g. `RESET_DONE` /
`REINIT_IN_PROGRESS` / `try_reinit` / `prepare_boot_reinit` / `mark_reset_done`. The existing
`el_pcie_ep_reset_` prefix already scopes these to *PCIe EP* reset handling (not a device-wide
reset), so "reset/reinit done" reads correctly as "PCIe EP reset handling done" — consistent with
the file's other `reset_*` names (`reset_begin`, `reset_state`, `reset_epoch`). Avoid "bring-up"
in the state names: it connotes cold-boot/start-from-scratch, whereas the machine's primary job
is re-init after a warm reset where the device was already up. (Keep the `reset_*` bookkeeping
verbs distinct from the action; `reinit`/`recfg` for the gated action helps separate the two.)

### [QUESTION] drivers/el_pcie_ep/el_pcie_ep_reset.c — does POST_CFG actually imply PIW was reset?
`el_pcie_ep_reset_piw_deasserted` unconditionally sets `PIW_DEASSERTED` on every warm-reset
POST_CFG callback, treating "warm reset POST_CFG fired" as "PIW reset was released." Confirm that
warm reset always resets PIW on this die, and that the reset manager only invokes the warm-reset
POST_CFG callback when the PIW component was actually in the reset set. If some warm-reset flavors
(or component-masked resets) leave PIW untouched yet still fire POST_CFG, the machine would assert
`PIW_DEASSERTED` and trigger a `bring_up` that reprograms a controller the host may still be using.
The callback only filters on `type == EL_RESET_TYPE_WARM`, not on whether PIW was in the reset
domain — so the PIW-reset inference is implicit. Worth a comment documenting the assumption, or a
guard if the reset manager exposes the component set.

### [SUGGESTION] drivers/el_pcie_ep/el_pcie_ep_reset.c — document the reset state machine
This is a genuinely non-trivial concurrency design (4 state bits, an epoch counter, a try-lock,
and a re-run loop spanning ISR context + reset-manager callback context + boot init). It deserves
a doc block so the next maintainer doesn't have to reverse-engineer the invariants. Add either a
file-header comment or a `docs/` entry covering: (a) the four `reset_state` bits and who sets/clears
each, (b) the role of `reset_epoch` (stale-init detection across a reset that lands mid-`bring_up`),
(c) the two readiness signals (PERSTn + PIW-released) and the three entry points that converge on
`try_link_init` (boot, PERSTn ISR, POST_CFG callback), (d) the `LINK_INIT_IN_PROGRESS` try-lock
contract and which contexts can race. Pairs with the lost-wakeup finding below — documenting the
invariants will make that window obvious.

### [PRAISE] Per-die reset callback registration via derived die id
(`el_dev_utils_get_die_id_from_address`) and registers the reset callback per die, rather than
hardcoding die 0. Correct for multi-die and avoids the classic "die 0 == local" latent bug.

### [SUGGESTION] drivers/el_pcie_ep/el_pcie_ep_internal.h + el_pcie_ep_reset.c — redundant `dev` back-pointer in `struct el_pcie_ep_data`
`struct el_pcie_ep_data` stores `const struct device *dev`, but `data` *is* `dev->data` — a
self-referential back-pointer into the container it was reached from. It exists only to recover
`dev` inside the reset callback, because registration passes `data` as the `void *user_data`:
`el_reset_register_callback(cb, data, die_id)` → callback does `data = user_data; dev = data->dev`.
Cleaner: register the **device** as the callback context and pull data from it.
- `el_reset_register_callback(el_pcie_ep_reset_callback, (void *)dev, die_id);`
- callback: `const struct device *dev = user_data; struct el_pcie_ep_data *data = DEV_DATA(dev);`
Then **delete the `dev` field** and the `data->dev = dev;` line in `el_pcie_ep_reset_state_init`,
leaving the struct as just `reset_state` + `reset_epoch` (the only true per-device state). Keeps
the device→data direction one-way (Zephyr-idiomatic) and shrinks the struct. Verified the field
has exactly two uses (set in `reset_state_init`, read in `reset_callback`) — both removed by this.

### [SUGGESTION] drivers/el_pcie_ep/el_pcie_ep.c + Kconfig — CONFIG-guard structure of `el_pcie_ep_init`
The boot path is gated by 7 interacting Kconfigs (`MINIMAL_INIT`, `RESET`, `LINK_INIT`,
`LINKUP_CFG`, `BAR_MAP`, `HANDLE_PERSTN_DEASSERT_IRQ`, `PHY_FW`) spread across three files, and
the single ordered EP boot contract (PIW pre-link → link train → BAR/iATU → expose CDM DBI →
disable SR-IOV → MSI-X security, all before host enumeration) is fragmented. Concrete issues:

1. **Unconstrained `MINIMAL_INIT` × `RESET` matrix.** `EL_PCIE_EP_RESET` is `default y if
   !MINIMAL_INIT` but has **no `depends on !EL_PCIE_EP_MINIMAL_INIT`**. A config can set both;
   then `RESET=y` advertises reset handling while the `#ifndef MINIMAL_INIT` wrapper silently
   skips `el_pcie_ep_reset_init()` (callback never registers), the boot-link gate, *and*
   `disable_sriov` / `msix_sec_levl_chk` — a silent, build-clean contradiction. **Fix:** add
   `depends on !EL_PCIE_EP_MINIMAL_INIT` to `EL_PCIE_EP_RESET`.
2. **Declare/use guard mismatch for `init_link_at_boot`** — declared under
   `#if !MINIMAL_INIT && defined(RESET)`, used under `#ifndef MINIMAL_INIT`→`#if defined(RESET)`.
   Same set, two spellings; drifts when one side is edited.
3. **In-body `#if defined(CONFIG_EL_PCIE_EP_RESET)` islands.** The internal header already
   provides no-op stubs for the `!HANDLE_PERSTN` case — do the same for `!RESET`
   (`static inline` no-op `reset_init` / `prepare_boot_link_init` / `mark_link_init_done`).
   Then `el_pcie_ep_init` drops both `#if` islands and reads as straight-line C
   (memoir: prefer stubs / `CALL_IF_ENABLED` over in-body `#if`).
4. **(Optional, structural)** lift the ordered sequence into one `el_pcie_ep_boot_sequence(dev)`
   helper so cold boot and the #249 warm-reset re-init path share the *same* contract instead of
   re-expressing the ordering (and risking drift). Ties into the #249 interaction question above.

Items 1 and 3 have clear correctness/maintainability payoff; 2 is a quick consistency fix; 4 is
the larger refactor that would also de-risk the CRS/DRS warm-reset window.

### [PRAISE] Two-signal readiness gate replaces the busy-wait
Replacing "bring up on any PERSTn edge + 10 ms busy-wait" with an explicit
PERSTn + PIW-reset-released gate driven by reset-manager PRE/POST callbacks, plus epoch-based
rejection of stale inits, is a clean and much more deterministic design (modulo the
core_rst_n ordering question above).

## Dropped / considered
- **`reset_epoch` never reset / could wrap** — 2^31 warm resets; not realistic. Dropped.
- **Two separate `atomic_test_bit` reads in `is_ready()` are non-atomic** — fine here; each bit
  is set independently and the worker re-checks under the epoch guard. Dropped.
- **`set_perstn_irq_mask` now called after `config_irq` (order flipped)** — connecting the
  handler before unmasking is the safer order; intentional. Dropped.
- **Callback returns `bring_up` rc to the reset manager on POST_CFG** — plausibly intended
  (surfaces re-init failure); not a defect without knowing el_reset's handling. Left as part
  of the timing question, not a separate finding.
- **`reset_init` moved before `configure_interrupts`** — registering the callback + warm-reset
  masking earlier is harmless during PRE_KERNEL_1. Dropped.
