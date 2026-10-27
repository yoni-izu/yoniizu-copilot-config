# LTSSM Logger management contract and driver compliance

- **Recorded:** 2026-10-20
- **Status:** Rechecked against 2026-10-19 18:10 replacement spec; latest reviewed EP #442 and SE #247 heads satisfy the count-query and keep-most-recent read restrictions; platform reset guarantees remain unverified
- **Tags:** ltssm logger, consumption, invalidation, read pointer, unread, count query, statefilter, threshold, efuse, middleware

## Contract

Device Manageability Specification.docx, OemPcieLtssmLogger (0x50), replacement received 2026-10-19 18:10, displayed tables 9-58 through 9-71. SHA-256: `98afcaf0d4b0f3fcc73b9bfba57635d500dc16befbde459efb871892dd0e3d06`. These replace the earlier table references and conclusions:

- Table 9-68 now explicitly says hardware entries cannot be invalidated and the read position exists only in firmware. Software consumption is compliant; withdraw the earlier hardware-invalidation finding.
- Table 9-64 and count-query prose require no entry-data fetch and no read-pointer movement. Start/Stop/Configure also leave the cursor unchanged. This requirement was NOT relaxed.
- Read-next returns raw state u8, reason u8, timestamp u32, moreAvailable u8; no driver-struct memcpy.
- One cursor per port; empty count succeeds with zero, exhausted read maps to OEM_LTSSM_NO_VALID_ENTRIES.
- No ownership required; all operations work link up/down.
- Device/PCIe reset must discard entries/configuration and rewind read position.
- Tables 9-58/9-61 now define stateFilter as a loop-suppression set, default 0, not a capture inclusion filter. Threshold range is 1..0xFFFFFFFF, default 15; zero MUST be rejected. Withdraw both prior Configure-contract findings.
- Table 9-68 explicitly requires Read next entry to return OEM_LTSSM_BUSY while running keep-most-recent. The latest reviewed EP #442 and SE #247 heads enforce this under their logger locks, before reading/consuming an entry; middleware must preserve that error mapping.
- Tables 9-58/9-66 promise running at power-up and after controller/device reset with default configuration; link down and LTSSM Hot Reset preserve logger state. The inspected headers do not establish reset values, so these remain hardware/platform verification questions, not proven code failures.
- Tables 9-70/9-71: FEATURE_DISABLE[2] DIS_LTSSM_LOG is sampled once at boot. Gate PLDM before request-field validation/driver access and omit from discovery; do not disable hardware/in-band diagnostic access.

## Earlier implementation snapshot (before latest SE re-review)

Earlier 2026-10-20 incremental check: SE #247 `487074f0d9af2970862cf23d30e7fc39dd060925`,
EP #442 `35997898328eec65db3da751b7d1af23e90ba1d1`. Relative to the snapshots
below, only comments/documentation and status-variable declaration placement changed.
Both count-query and keep-most-recent read findings remain. Current helper anchors:
EP status_read:2415, get_status:2464, read_next:2524; SE status_read:1540,
en:1589, set_config:1716, get_status:1771, read_next:1817.
Specification fingerprint unchanged. This is source-only verification, not runtime evidence.

EP #442 bf8a83bd; SE #247 a1abc587:

- Both read_next paths advance software cursor/timestamp only; entry reads select RD viewport and read timestamp/data. This now matches the revised consumption mechanism.
- Both get_status paths call a helper that rereads the last consumed slot and may rewind the cursor on mismatch: violates the observational-query requirement.
- SE Start and Configure also invoke this helper with NULL start_idx; SE does not skip cursor processing in that case. EP does skip it. Even an SE EBUSY result can follow a cursor rewind.
- Both reject zero threshold and program the mask as suppression, not filtering; both now match the revised Configure definition.
- Both read_next paths can return an entry while keep-most-recent is running; neither the EP syscall wrapper nor parent forwarding path adds the newly explicit busy gate.
- SE a1abc587 exempts all nine logger wrappers from ownership, retaining die-index checks.
- Both transition tables contain the same 88 rows with unique keys. Earlier 77/87-row drift claims were incorrect for these snapshots.
- Timestamp equality is not a reset generation identifier. Hardware-reset/refill with matching timestamp can retain a stale cursor; platform reset-domain behavior remains to be established.
- The inspected local PLDM OEM dispatch currently has only 0x01/0x02/0x20/0x30. Driver availability is not end-to-end command 0x50 support.

## Latest SE re-review (2026-10-20)

SE #247 head `ef0d32b4fa17206eabb4cb14e2699be916e1ae99` was rechecked against the
previously reviewed head `487074f0d9af2970862cf23d30e7fc39dd060925`:

- `dm_ltssm_logger_get_status()` calls the status helper and reads configuration registers only; it does not fetch an entry or change the software cursor.
- Start and Configure do not invoke cursor-staleness recovery. `read_next()` checks for an active keep-most-recent capture under `ltssm_logger_lock` and returns `-EBUSY` before entry access.
- SE continues to reject a zero threshold and programs the mask according to the loop-suppression contract.

Source: `ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c`, `dm_ltssm_logger_status_read()`,
`dm_ltssm_logger_en()`, `dm_ltssm_logger_set_config()`,
`dm_ltssm_logger_get_status()`, and `dm_ltssm_logger_read_next()`.

## Latest EP re-review (2026-10-20)

EP #442 head `d1e9d3cea61ead6783882fbbd3a947d230a2ec28` was rechecked against
the previously reviewed head `35997898328eec65db3da751b7d1af23e90ba1d1`:

- `ltssm_logger_status_read()` reads control/write-pointer state and computes the
  software unread count without fetching entry data or mutating the cursor.
- `dwc_ep_ctl_ltssm_logger_read_next()` returns `-EBUSY` for a running
  keep-most-recent capture while holding `ltssm_logger_lock`, before cursor
  recovery or entry access.
- When capture is stopped, the driver checks whether the last consumed slot was
  overwritten and records a stale-cursor flag. It preserves the numeric
  `rd_pos`; the next consuming read restarts at the oldest retained entry if
  needed. Reset-generation guarantees remain unverified.
- The transition decoder was moved into the shell-only source file; a repository
  search found no other caller of the helper.

Source: EP `drivers/snps_ep_ctl/dwc_ep_ctl.c`, `ltssm_logger_status_read()`,
`ltssm_logger_cursor_is_stale()`, `dwc_ep_ctl_ltssm_logger_read_next()`, and
`drivers/snps_ep_ctl/dwc_ep_ctl_shell.c`, `g_ltssm_transitions[]`.

## Sources

- Spec: replacement Device Manageability Specification.docx, section OemPcieLtssmLogger, tables 9-58..9-71; effective-final-markup body blocks 2702..2847. Key blocks: Configure 2740/2745; count 2750; pointer 2766; persistence 2785; mapping 2820; fuse 2846. Blocks are XML-body indices, not python-docx paragraph numbers.
- Spec: A1 Manageability Specification.docx has no LTSSM logger occurrence in the extracted effective final markup; requested working spec remains the audit source.
- EP: drivers/snps_ep_ctl/dwc_ep_ctl.c, entry_read:2320, status_read:2336, cursor_is_stale:2398, get_status:2418, read_next:2478; include/dwc_ep_ctl_api.h.
- SE: drivers/snps_dm_ctl/dwc_dm_ctl.c, entry_read:1526, status_read:1552, en:1601, set_config:1730, get_status:1787, read_next:1833; include/dwc_dm_ctl_api.h at a1abc587.
- PLDM: ip/mng/modules/el_pmci/service/pldm/pldm_oem.c:72, handler_query/fill_cmds; modules/el_mctp/el_pldm.c:1240.
- EP reset: drivers/el_pcie_ep/el_pcie_ep_reset.c:173, parent bring_up/post_bring_up_cfg.

## Review lesson

Read normative implementation-mapping notes as requirements too, even if their API names are stale. Separate physical invalidation, logical software consumption, and the no-entry-fetch requirement; none proves the others. Re-read a replacement specification before carrying findings forward: the 18:10 revision intentionally changes three previously blocking contracts.
