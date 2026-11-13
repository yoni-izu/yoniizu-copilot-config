# A2 SE reset: per-C2C PERST_N, per-link resets, hot reset and link-down

- **Recorded:** 2026-11-11
- **Status:** Verified via Neo (neo-ask --chip a2); generated csr_c2c/dmp_ctrl/dm_ctrl_core not mirrored, so C2C/DMP/DM CSR offsets and reset values are unverified
- **Tags:** a2, reset, perst_n, per link reset, hot reset, link down, app_ltssm_en, delay_link_dn_rst, non-sticky, quiesce, c2c_idle_status, early reset notify

## Scope / Question

How are PERST, hot reset and link-down handled on A2?

## Findings

- PERST_N moved from the DM to the C2C (e7dd7fe). `C2C.PERST_N.VAL` per C2C drives the paired DM `async_perst_n`. It is SW-owned (the PSOC input was removed) and resets to 0 (asserted). The offset is likely 0x35C rather than 0x360, but is unverified. Leader-first order is not enforced by HW.
- Resets are per link (c3489b7); "DM0 reset kills DM1" is wrong. Residual coupling: the even C2C/DM0 reset also resets the DMP glue, so DMP CSR and CCM access die with it.
- The hot-reset pin was removed (9700554). Detect hot reset via the DM SPI cause (smlh/link_req_rst fall/rise) → C2C SPI → fc_interrupt.
- `DELAY_LINK_DN_RST`/`DELAY_LINK_TRAINING` auto-clear `APP_LTSSM_EN`, so FW must re-arm it. DM glue CSRs survive a hot reset; DBI non-sticky config must be re-applied. `EXT_HOT_RST_IND_EN/_CNT` are dead.
- Link-down has no automatic recovery; SW recovers via SPI. `surprise_down_err` is unconnected. There is no FLR in the integration RTL.
- There is no RTL quiesce sequence. `C2C_IDLE_STATUS` has 3 bits (remote/local credit-mgr empty, DMT empty), but Neo's follow-up trace found they do not prove all transactions and responses are drained (for example, a DMT entry can be empty before a lookup response completes). An early reset-notify vld/ack handshake exists on c2c_macro; acknowledgement is not proof of drain completion and can represent reset notification/isolation or timeout. Agree a cross-IP quiesce/isolation protocol, timeout and forced-isolation behavior with reset owners.
- Whether the PHY CCM survives a reset is unknown; assume a reload.

## Sources

- Neo (`~/bin/neo-ask --chip a2`) review of the A2 SE plan, questions q01–q13, 2026-11-10/11. Revisions: ip.se.rtl 33cbdc1 (mirror 2026-11-09), project.atlantic2.rtl 875d851, project.atlantic2.csr 2a12a536, ip.infra.rtl.shared.atlantic2 9bc4f713, eip_pcie 1b48d8dc, ip.infra.csr e16cbf5.
- Raw answers: session `neo_se_review/answers/q01–q13.md`.
- Follow-up: Neo A2 report on `C2C_IDLE_STATUS` and early-reset-notify semantics, mirror snapshot 2026-11-09; full integration remains unverified.
