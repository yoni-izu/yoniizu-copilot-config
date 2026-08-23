# BATCH — Palladium "device did not become operational" family (FC `2_18_0_2p0_1240`)

_Batch investigation — 2026-08-16 — scope: MODINFSDKSW-12003, 12117, 12058, 11764, 11763_

All five are auto-filed (`AI Investigator`), label `palladium_bugs`, component `ip-pcie-firmware-ep`, on
the **same FC image `2_18_0_2p0_1240`**, all end in `ERROR: device did not become operational` / 0 tests /
UNHEALTHY, and Oded has pinged for status on all of them. The regression-analysis skill fragmented them
per-station/per-run. **They are not one bug** — the *stage* at which bring-up dies differs.

## Triage — three groups (ADJUSTED 2026-08-16 after re-review)

Re-review corrected the earlier B/C split. **Decisive evidence: 11764 flips between the "component ready
0x1" signature AND the "MBOX_INIT / PSOC_MNG_0 init" signature on the *same* station/runs** (see its 08-06
02:14 comment on run #31051481383: `Failure type: infra:fw-init-timeout-mbox-init` but the excerpt is
`Firmware boot timed-out. Component ready state at time-out: 0x1`). Those two are therefore **one failure
seen from two angles** (host-side component-ready vs mailbox-side channel init), not two groups. The real
second axis is **never-came-up (cold boot)** vs **came-up-then-hung (runtime)**.

| Ticket | Station | Device on bus? | Precise signature | Group | Suspected owner |
|---|---|---|---|---|---|
| **12003** | edk0-z3a | **NO** — `Detected Simulator device`, `no 208b found` | never enumerated | **A · bus-absent** | station/emulation infra + `ip-pcie-kmd` |
| **12117** | edk4-z2a | YES `[208b] rev 0x23` | `component ready 0x1` → probe **-110** (host view) | **B · cold-boot never-op** | PSOC_MNG init (hyp.) / `ip-pcie-firmware-ep` |
| **12058** | edk6-z2a | YES `[208b] rev 0x23` | `component ready 0x1` → **-110**; latest run `Failed to send PSOC message opcode 10 rc -110` | **B · cold-boot never-op** | PSOC_MNG init (hyp.) |
| **11764** | edk0-z3a | YES `[208b] rev 0x55` | `MBOX_INIT (op 511)` 360s timeout → `Failed to init FW channel PSOC_MNG_0` → **-110**  ⇄ also `component ready 0x1` on other runs | **B · cold-boot never-op** (mailbox view) | `ip-psoc-firmware` |
| **11763** | edk5-z3a | YES `[208b]` | `PSOC_HEARTBEAT (op 2, msg 308)` timeout → heartbeat **-110** → soft/hard reset fail (`-5`/`-16`) → probe **-5** | **C · runtime hang + reset-recovery fail** | `ip-psoc-firmware` |

### Group A — bus-absent (emulation infra, NOT FW) — *confirmed*
12003 only. Device never presented a PCIe endpoint; a same-run sibling on the identical image enumerated
`208b` fine ⇒ per-station Palladium bring-up, not a FW/image bug. (See `MODINFSDKSW-12003-20260807.md`.)
**Not part of the FW root-cause.**

### Group B — cold-boot: enumerates but never becomes operational (`-110`) — *likely one root cause*
12117, 12058, 11764. Device enumerates and `atl` probes, FW boot does not reach operational within 500s.
Two co-occurring surface views of the **same** failure (proven by 11764 showing both):
  - *host view* — `Firmware boot timed-out. Component ready state at time-out: 0x1` → -110 (12117, 12058)
  - *mailbox view* — `MBOX_INIT (op 511)` no-ack → `Failed to init FW channel PSOC_MNG_0` → -110 (11764);
    12058's latest `Failed to send PSOC message opcode 10 rc -110` is the same.
  **Leading hypothesis:** the PSOC management channel never initializes, so main-FW component-ready stays at
  `0x1` and the host times out. Anchor-adjacent to Known Error **11548**; PSOC root → **IPPSOCSW-956**.

### Group C — runtime PSOC heartbeat hang + failed reset recovery (`-5`) — *distinct mode*
11763. Different arc: the device **was** operational (`PSOC_HEARTBEAT` at msg id 308 = ~308 msgs already
exchanged), then PSOC stopped acking → heartbeat -110 → `elac_device_reset_upon_release` soft reset -5 and
hard reset -16 both fail → re-probe -5. This is a **stability/hang + broken recovery path**, not a cold
bring-up failure. Shares the PSOC-FW owner (`ip-psoc-firmware` / IPPSOCSW-956) but the reset-recovery
failure (-5/-16) is an extra, separately-trackable defect.

## Central hypothesis (bridges B and C) — *SUPERSEDED by deep dive below*
~~`Component ready state 0x1` is the downstream symptom of PSOC not coming up; B and C collapse into one~~.
Deep dive found B and C are **different phases** (cold-init vs steady-state heartbeat), not one bug. Kept
for history; see DEEP DIVE.

## DEEP DIVE — findings (2026-08-16)

### Group C — 11763 → **IPPSOCSW-956** — *CONFIRMED against code*
`IPPSOCSW-956` ("el-pci PSOC heartbeat spuriously fails with -EIO under FW load (CQ/EQ seq lockstep)",
component **`ip-psoc-kmd`**, status **In Review**) is a **KMD driver bug**, not product FW/silicon:
- Each heartbeat yields two FW responses on two paths: the **synchronous CQ reply** (`hb_seq++`) and the
  **async EQ event** (`hb_eq_seq++`). A pre-send guard requires strict lockstep.
- Verified in the local `main` KMD tree:
  - `ip/psoc/el_psoc_fw_if.c:51` — `if (psoc_info->hb_seq != psoc_info->hb_eq_seq) { … return -EIO; }`
  - `elp_main.c:314 elp_heartbeat_work()` — on any heartbeat error does `dev_err(...); return;` **without
    re-scheduling** the delayed work (success path re-queues via `schedule_delayed_work`). So one transient
    `-EIO` **permanently** stops driver heartbeats → FW `hb_timer` expires → FW declares heartbeat lost →
    escalates to reset. → matches 11763 (`PSOC_HEARTBEAT` timeout `-110` → reset fail `-5`/`-16`).
- **Trigger = Palladium ARM-FW slowness:** the CQ reply for seq N lands (advancing `hb_seq`) before the EQ
  event for seq N is processed (`hb_eq_seq` lagging) → mismatch, even though the CQ handshake (authoritative
  liveness) succeeded. Guard over-strict + work-item doesn't self-heal.
- **Fix not yet in `main`** — both bug halves still present locally ⇒ IPPSOCSW-956 fix unmerged (In Review).
  Sibling: `PRJIRDMSW-4816` (psoc_heartbeat_seq_mismatch integration failure, `ip-pcie-kmd`).

### Group B — 12117, 12058, 11764 — cold-init PSOC_MNG handshake timeout — *likely, mechanism unverified*
Distinct phase from C: `MBOX_INIT` (opcode 511, **msg id 0** = the very first message) and the boot-status
poll happen at **cold init, before heartbeat**, so this is **not** the heartbeat race.
- KMD path: `el_psoc_wait_for_boot_ready()` polls a boot-status reg for `EL_FW_BOOT_STS_DONE` (=`0x2`);
  the ticket's `component ready state 0x1` = boot stuck **below DONE** (partial). Then
  `el_psoc_fw_channel_init()` → `fw_channel_init(dev,"PSOC_MNG",…)` sends `MBOX_INIT`; PSOC never acks in
  360 s → `Failed to init FW channel PSOC_MNG_0` → probe `-110`. (Exact 500 s / "component ready state"
  strings come from the KMD rev baked into FC `2_18_0_2p0_1240`, which differs from the local `main` tree.)
- **No dedicated anchor ticket exists** for this cold-init facet (unlike C's IPPSOCSW-956). Leading cause:
  same **Palladium ARM-FW slowness** class — PSOC uC doesn't reach boot-done / ack the first mailbox inside
  the (already large) timeout. *Unverified:* PSOC-FW cold-boot hang vs timeout-too-short vs a distinct KMD
  init bug — needs the **PSOC FW-side boot/console log** (UART) from an affected run; the GH runner log only
  shows the host KMD view. Possibly-related: `PRJIRDMSW-4831` (PSOC FW `full_boot_flow` test fails,
  `ip-psoc-firmware`), `IPPCIESW-557` (PrimeBoot→MNG handoff warm-reset).

### Batch-level synthesis
The family's FW-facing failures (**B + C**) are predominantly **PSOC-management-path timing artifacts
exposed by Palladium's slow ARM firmware**, concentrated in the **KMD driver**, not device/silicon defects:
- **C (steady-state):** CQ/EQ heartbeat lockstep race permanently kills heartbeats → reset (IPPSOCSW-956,
  fix In Review, KMD).
- **B (cold-init):** PSOC_MNG boot/`MBOX_INIT` handshake doesn't complete in time (no anchor yet).
- **A (12003):** unrelated — emulation/station bring-up (device never on the bus).
Reframe for the manager: these are **not** "the chip/FW won't boot" bugs; they are driver-timing +
emulator-speed issues (one fix already in review), which is why they only bite on Palladium and keep
recurring on the frozen FC image `2_18_0_2p0_1240`.

### Next steps to close out B (only remaining unknown)
1. Pull the **PSOC FW-side boot log** (UART/console via `pldm` skill) on an affected station (e.g. edk4-z2a
   for 12117) during a repro → see if the PSOC uC hangs vs is merely slow.
2. Diff the KMD rev in FC `2_18_0_2p0_1240` vs `main` for boot/`MBOX_INIT` timeout constants; assess a
   timeout bump (cf. the "HB interval → 10 s" idea floated in IPPSOCSW-956).
3. If a PSOC-FW boot hang is confirmed, file/link a Group-B anchor under `ip-psoc-firmware`.

## Recurrence / heat
Still hitting as of 2026-08-14/15 on the *same* FC tag across many stations (12117 latest: edk1/edk2-z3a).
No FC image newer than `2_18_0_2p0_1240` has been rolled ⇒ every group-B/C re-file keeps reproducing.
Oded pinged each ticket (12117 08-09/08-13; 11764 08-05/08-09; 11763 08-05/08-09; 12003 08-07/08-10);
IPPSOCSW-956 itself pinged 08-13 (fix In Review, unmerged).

## Actions requiring approval (none taken)
- Nothing posted to Jira. Linking/close/status-replies are drafted only, pending approval.
- Proposed linking: **C** (11763) → duplicate of **IPPSOCSW-956**; **B** (12117/12058/11764) → relates-to
  each other + the PSOC cold-init track (anchor TBD); **A** (12003) separate (emulation infra).
