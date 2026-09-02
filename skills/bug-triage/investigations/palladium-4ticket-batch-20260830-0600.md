# Batch triage — MODINFSDKSW-11763 / 12058 / 12117 / 12719

**Date:** 2026-08-30 · **Analyst:** Sleuth · **Status:** investigation complete, nothing posted

All four are `palladium_bugs`, reporter *AI Investigator*, assignee **Yoni Izutskiver**, status
**Backlog**, with unanswered pings from Oded Gabbay (latest 08-26 / 08-24).

## Headline

| Ticket | Current component | Verdict | Real owner |
|---|---|---|---|
| 11763 | ip-pcie-firmware-ep | **Reproduction of IPPSOCSW-956 — re-open condition met** (confirmed) | ip-psoc-kmd |
| 12058 | ip-pcie-firmware-ep | **Misclassified** — FW *boots fine*; not the cold-init family (confirmed) | ip-psoc-kmd (likely, = 11763) |
| 12117 | ip-pcie-firmware-ep | Catch-all bucket for the IPSESW-308 family; needs re-test on the fixed FW | ip-se-firmware / split |
| 12719 | ip-d2d-firmware | **Performance verdict, not a FW defect** (confirmed) | el-qual |

Three of the four carry the wrong component. Two of the four are anchored to a family whose
root cause has since been disproved or fixed.

---

## MODINFSDKSW-11763 — PSOC heartbeat/reset, pldm-edk5-z3a

**Prior read (Yoni, 08-17):** "PSOC heartbeat CQ/EQ sequence race under Palladium timing
(driver-side, not FW). Duplicate of IPPSOCSW-956."

**That read is correct — and the anchor was closed on a condition that has now been violated.**

IPPSOCSW-956 (`ip-psoc-kmd`, Andrey Agranovich) is **Closed/Done, fixVersion
`iridium-sw-0.1-alpha`**, closed 08-23 with:

> "I didn't see this repro since I merged the code. **Let's close it and re-open if it appears
> again.**"

### New evidence — run 33272705912, 2026-08-30 (today), pldm-edk5-z3a
Host `dmesg.log` — *confirmed*:
```
atl 0000:..: atl device found [208b:0001]          <- device PRESENT
PSOC heartbeat sequence mismatch with EQ: 6 vs. 5  <- the exact IPPSOCSW-956 signature
Failed to send PSOC message, opcode 3, rc -5       <- -EIO from the over-strict guard
```
The signature reproduced **3 days after the fix ticket was closed**. Per its own closure
condition, IPPSOCSW-956 should be **re-opened**. Open question for Andrey: is the fixed
`ip-psoc-kmd` artifact actually propagated into `regression-ci.xml`? He flagged on 08-17 that
"the artifact is not yet propagated to the regression manifest" — that may still hold, in which
case the fix is fine and only the manifest pin is stale. **Check the pin before re-opening.**

### New, unrecorded ARM signature — needs its own ticket (unverified cause)
Same station, same run, ARM `uart.log`:
```
[00:00:05.615] <wrn> el_mng_comms: Heartbeat lost - no HB received within 5000 ms
[00:00:18.788] <err> os: ***** HARD FAULT *****  -> Fault escalation
[00:00:18.788] <err> os: ***** MPU FAULT *****   Data Access Violation
[00:00:18.788] <err> os:   MMFAR Address: 0x60c0c000
[00:00:18.788] <err> os:   r3/a4: 0xf0c040c4  r12/ip: 0xf3be0018  r14/lr: 0x6034650b
[00:00:18.788] <err> os:   Faulting instruction address (r15/pc): 0x6034650a
[00:00:18.789] <err> os: >>> ZEPHYR FATAL ERROR 19: Unknown error on CPU 0
[00:00:18.789] <err> os: Current thread: 0x603a2198 (MBOX_RCV_WORKQ)
```
(Wall-clock spans 2 h for 13 s of emulated uptime — normal Palladium time dilation, so this is
one boot: heartbeat lost first, ARM fault second.)

This is a **third distinct ARM fault**, not in the memoir and not matching either known one:

| | Fault | Thread | Outcome |
|---|---|---|---|
| IPSESW-308 | USAGE FAULT, illegal EPSR | `el_se_link_init_tid` | Halting system |
| MODINFSDKSW-12649 | `el_mmu` page translation VA `0xf3b9e000` + `el_dma_sei_isr` | MNG CPU-comms | alive at `mng` shell |
| **this run** | **MPU FAULT, Data Access Violation, MMFAR `0x60c0c000`** | **`MBOX_RCV_WORKQ`** | Zephyr fatal 19 |

*Likely* the ARM-side consequence of the heartbeat loss (mailbox teardown racing an in-flight
receive → stale pointer deref in the receive workqueue). *Unverified* — could equally be an
independent FW defect that *causes* the heartbeat loss. Deciding this needs the PC `0x6034650a`
resolved against the FW ELF for that build. **Do not fold this into IPPSOCSW-956** (a KMD-side
fix cannot fix an ARM MPU fault).

**Recommendation:** verify the KMD pin in `regression-ci.xml`; re-open IPPSOCSW-956 if the pin
already carries the fix. Fix component `ip-pcie-firmware-ep` → `ip-psoc-kmd`. File a **new**
ticket for the `MBOX_RCV_WORKQ` MPU fault against the ARM FW.

---

## MODINFSDKSW-12058 — PCI probe -110, pldm-edk6-z2a

**Prior read (Yoni, 08-17):** "Same PSOC_MNG cold-init timeout as MODINFSDKSW-12117 /
MODINFSDKSW-11764. Palladium timing, not a new defect."

**This is wrong, and the log disproves it — *confirmed*.** Run 31739687559, edk6-z2a `dmesg.log`:
```
23:30:36  atl device found [208b:0001] (rev 23)
23:30:40  Waiting for firmware boot to complete with 240 seconds timeout
23:30:51  Firmware boot completed                   <-- FW BOOTED SUCCESSFULLY
23:31:04  Failed to send PSOC message, opcode 10, rc -110
23:31:04  Failed to set attribute 23 to 0x1036b00, rc=-110
23:31:04  Failed to send PSOC message, opcode 10, rc -5
23:31:04  Failed to set attribute 24 to 0x6a7de292, rc=-5
23:31:04  Failed to send PSOC message, opcode 10, rc -5
23:31:04  Failed to set attribute 5 to 0xaf0000, rc=-5
23:31:04  Failed to init FW channel PSOC_PCIE_0
23:31:04  Failed to send PSOC message, opcode 3, rc -5
```

Two facts kill the current classification:
1. **`Firmware boot completed`.** The 11764 → 12978 → 13013 → **IPSESW-308** family is a
   *cold-init* failure — boot stuck at `component ready state 0x1`, `MBOX_INIT` never acked.
   Here boot finished in 11 s. **The IPSESW-308 fix cannot fix this ticket.**
2. **The channel is `PSOC_PCIE_0`, not `PSOC_MNG_0`.** Different channel from both the
   PSOC_MNG cold-init family and from 11978.

**Actual shape:** post-boot attribute programming. First message times out (`-110`), then
*every* subsequent message returns `-5` (`-EIO`) — one transient timeout followed by permanent
`-EIO`. That is precisely the IPPSOCSW-956 mechanism (over-strict CQ/EQ lockstep guard latching
`-EIO`, work never re-queued). So 12058 is **likely the same family as 11763**, not 12117.

*Likely*, not confirmed: this station's dmesg does **not** carry the
`PSOC heartbeat sequence mismatch` line, so the `-EIO` may originate from a different guard.
Confirm by resolving which KMD path returns `-EIO` for `el_psoc_send_msg` opcode 10.

**Recommendation:** un-link from 12117 / the PSOC_MNG family, re-link to 11763 / IPPSOCSW-956,
component → `ip-psoc-kmd`.

---

## MODINFSDKSW-12117 — PCI probe -110, pldm-edk4-z2a

Currently linked *relates to* 11764 (Closed/Duplicate) and 12058.

**Prior read (Yoni, 08-17):** "Device enumerates but PSOC_MNG cold-init times out (component
ready 0x1, probe -110). **Palladium timing** on FC 2_18_0_2p0_1240."

The stated signature (`component ready 0x1` + PSOC_MNG cold-init) *is* the IPSESW-308 family.
But the "Palladium timing, not a defect" rationale has since been **overturned**: IPSESW-308
root-caused it to a real ARM Zephyr USAGE FAULT (UART device-struct corruption) and is now
**Closed/Done (08-25, Pardha Kesapragada)**.

**So 12117 is very likely already fixed and simply never re-tested.**

**Could not confirm from logs (unverified).** The edk4-z2a artifact for run 31739687559 shows
*no* `atl`/`el_pci` activity at all — that station ran the **host-FW flavor**
(`Booting FwHostCli 1.690.0-29082026-075232`), which per the memoir never exhibits this bug. Not
a valid sample. A targeted re-test is needed rather than more log archaeology.

**Secondary problem — this ticket has become a bucket, not a bug.** Its 14 auto-comments span
**8 runs and 10 stations** (edk0-z2a/z3a, edk1-z3a, edk2-z2a/z3a, edk4-z2a/z3a, edk5-z3a, edk6,
edk7). The auto-filer appends any `-110` to it regardless of stage. Since `-110` is a shared
endpoint with at least three distinct ARM causes, this ticket cannot converge as written.

**Recommendation:** re-test on an FW artifact that carries the IPSESW-308 fix (> `1.684.0`); if
clean, close as duplicate of IPSESW-308. Narrow the summary to the single station + cold-init
stage so the auto-filer stops using it as a catch-all.

---

## MODINFSDKSW-12719 — D2D single-chip test returns False, pldm-edk0-z3a

Zero auto-comments, two unanswered pings from Oded (08-21, 08-24). Component **ip-d2d-firmware**.

Already decoded to sub-test level in a prior session (run 32063981909) — *confirmed*:
- Only **4** sub-tests fail, all `vertical-avg_bw-lmem` (read + write).
- **Inner** die-pairs die1/die2 ≈ **275–284 GB/s** vs fail-threshold **360**.
- **Edge** die-pairs die0/die3 ≈ **400 GB/s** — pass. Horizontal/diagonal/all-gmem pass everywhere.
- **All** `*_error_count` / CE / UE metrics `0 / Success`; no D2D fatal, no SEI in dmesg during
  the test window.

**This is a bandwidth-threshold verdict, not a functional FW defect** — there is no data
corruption of any kind. Either the inner die-pairs have a genuine vertical-lmem BW limit, or the
qual threshold is uniformly 360 where the topology cannot deliver it on inner pairs.

`ip-d2d-firmware` is the wrong component — the sibling ticket 11910 sits on **el-qual**
(Omri Zeneva). Note 11910 was closed *Done* on qual PR #342, which only touched the EDP
`--duration` switch and a plot decode — **it never touched the D2D path**, which was separately
masked by a test reorder. So the D2D assertion has been failing continuously the whole time.

**Recommendation:** component → `el-qual`, reassign to Omri Zeneva / Idan Pazi. The open question
is an architecture one, not a debugging one: *is a ~30 % inner-vs-edge vertical lmem BW asymmetry
expected on this topology?* If yes → per-pair thresholds. If no → real D2D BW investigation.
**Do not** re-triage this per-station; it is deterministic on FC `2_18_0_2p0_1240`.

---

## Cross-cutting observations

1. **The auto-filer is fragmenting ~3 real causes into a dozen tickets** and cross-linking them
   on the shared `-110` dmesg symptom. `-110` has at least three distinct ARM causes
   (IPSESW-308 el_se fault, 12649 MMU/DMA fault, and the new `MBOX_RCV_WORKQ` MPU fault). The
   ARM `uart.log` is the only reliable discriminator — the host dmesg is downstream of all three.
2. **Two anchors closed while their re-file tickets stayed in Backlog.** IPPSOCSW-956 and
   IPSESW-308 both went Done (08-25 / 08-27) but 11763, 12058 and 12117 were never re-tested or
   closed behind them. This is why Oded keeps seeing "no movement".
3. **Component hygiene:** 3 of 4 are on the wrong component, which routes them to the wrong
   owner and keeps them queued to Yoni.

## Drafts prepared, NOT posted
Jira comments for all four + the proposed component/link changes are held for approval.
