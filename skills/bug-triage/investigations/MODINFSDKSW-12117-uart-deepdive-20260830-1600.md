# Debug summary — MODINFSDKSW-12117 (PCI probe -110, "device did not become operational")

**Date:** 2026-08-30 · **Analyst:** Sleuth · **Status:** in progress — ARM UART logs pulled, root cause narrowed, nothing posted

## One-line

12117 is **not one bug** — it is the auto-filer's `-110` bucket. The ARM `uart.log`s prove the
host `MBOX_INIT -110` is a *downstream* symptom of the PSOC/MNG ARM firmware crashing at runtime
(`MBOX_RCV_WORKQ` MPU fault), preceded by a `Heartbeat lost` — **not** the IPSESW-308 cold-init
`el_se` fault it is currently anchored to.

## Evidence — ARM UART logs pulled per run

| Run | Date | Station | ARM uart.log | Signature |
|---|---|---|---|---|
| 33272705912 | 08-30 | edk4-z3a | present (fault) | **MBOX_RCV_WORKQ MPU fault** (see below) |
| 32296694761 | 08-20 | edk4-z3a | present (clean) | ARM booted FLA→PrimeBoot→**Management DONE**, no fault; FW **1.682.0** |
| 32412352255 | 08-21 | edk5-z3a | **empty** | unclassifiable |
| 32180243384 | 08-18 | edk5-z3a | **empty** | unclassifiable |
| 31531929875 | 08-12 | edk7-z3a | **empty** | unclassifiable |

Empty `uart.log` runs are unclassifiable per the memoir — not a match to anything, discard.

## The decisive run — 33272705912 (edk4-z3a, 08-30): all three host signatures in ONE run

Host `dmesg.log`, five consecutive probe attempts on the same device/image:

1. **23:29 & 01:39 — heartbeat race (IPPSOCSW-956, KMD):**
   `Firmware boot completed` → `PSOC heartbeat sequence mismatch with EQ: 18 vs. 17` (then `10 vs. 9`)
   → `PSOC heartbeat failed with error -5`.
2. **02:11 — ARM crashes (the real defect):** ARM `uart.log`:
   ```
   <wrn> el_mng_comms: Heartbeat lost - no HB received within 5000 ms
   ***** HARD FAULT ***** Fault escalation
   ***** MPU FAULT ***** Data Access Violation
     MMFAR Address: 0x60c0c000
     r3/a4: 0xf0c040c4  r12/ip: 0xf3be0018  r14/lr: 0x6034650b
     Faulting instruction address (r15/pc): 0x6034650a
   >>> ZEPHYR FATAL ERROR 19: Unknown error on CPU 0
   Current thread: 0x603a2198 (MBOX_RCV_WORKQ)
   ```
   Host at 02:17: `msg id 124 opcode 4 (PSOC_SOF...)` timeout `-110`, then `MMU_CONTEXT_DESTROY -5`,
   hard-reset `-5`.
3. **02:48 & 02:56 — every reprobe after the ARM is dead:**
   `Firmware boot completed` → `Timeout ... msg id 0 opcode 511 (MBOX_INIT)` on `PSOC_MNG_0` →
   `Failed to init FW channel PSOC_MNG_0` → `probe ... error -110`.

**Key insight:** once the ARM takes the `MBOX_RCV_WORKQ` MPU fault at 02:11, the ARM FW is dead, so
every subsequent host probe fails at `MBOX_INIT` with `-110`. On the host alone this is
**indistinguishable** from the IPSESW-308 cold-init fault — the ARM `uart.log` is the only
discriminator. The heartbeat mismatches earlier are the 956 KMD race; the "Heartbeat lost" on the
ARM side at 02:11 is what precedes the MPU fault.

## Fault fingerprint (for ELF resolution — next step)

- Thread **`MBOX_RCV_WORKQ`**, Zephyr fatal 19, MPU/Data-Access-Violation.
- **MMFAR (bad data address) `0x60c0c000`**, **PC `0x6034650a`**, LR `0x6034650b`, r12 `0xf3be0018`,
  r3 `0xf0c040c4`, xpsr `0x21000000`.
- Trigger sequence: `el_mng_comms: Heartbeat lost - no HB received within 5000 ms` → HARD→MPU fault.
- Hypothesis (*unverified*): the mailbox-receive workqueue dereferences a stale/torn pointer while the
  MNG heartbeat-loss path is tearing the channel down — a use-after-free / teardown race in the
  receive WQ, not the `el_se` UART-struct corruption of IPSESW-308.

This is the **third distinct ARM cause** behind the shared `-110`, alongside:
- IPSESW-308 — `el_se` USAGE FAULT, thread `el_se_link_init_tid`, "Halting system".
- MODINFSDKSW-12649 — `el_mmu` page-translation + `el_dma_sei_isr`, MNG CPU-comms, alive at shell.

## Next steps

1. **Resolve PC `0x6034650a` + MMFAR `0x60c0c000`** against the FW ELF for the 33272705912 build
   (need the exact `mng`/PSOC manifest for that run — the 08-20 sibling was `1.682.0`, below the
   IPSESW-308 fix `>1.684.0`, so this is likely also a pre-fix image → re-test on a newer FC first).
2. Confirm whether the MPU fault is *caused by* the heartbeat-loss teardown (→ owner MNG/PSOC FW
   `el_mng_comms` + mailbox RX WQ) or independent.
3. **Split the ticket:** keep 12117 for the `MBOX_RCV_WORKQ` MPU fault only; re-file/route the
   heartbeat `-5` occurrences to IPPSOCSW-956 and the clean-boot host `-110` (08-20, 1.682.0) as a
   re-test-on-new-FC item. Narrow the summary to one station + stage so the auto-filer stops bucketing.

## Cross-check correction

My earlier batch note attributed the `MBOX_RCV_WORKQ` MPU fault to **11763**; it is actually run
**33272705912**, which the auto-filer cross-posted and which the store attributes to **12117**.
11763's own runs are 31636646684 (edk5-z3a, cold-init `MBOX_INIT/PSOC_MNG_0 -110`) and 32063981909
(edk4-z3a, heartbeat mismatch `EQ 29 vs 28 -5`). The MPU-fault signature belongs to the 12117 bucket.
