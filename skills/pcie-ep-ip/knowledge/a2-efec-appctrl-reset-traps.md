# A2 eFEC blocks, the two APP_CTRL registers, and reset-value traps

- **Recorded:** 2026-11-12
- **Status:** Verified from RTL (Neo, a2 r03/r04)
- **Tags:** a2, efec, astera, sco2_rplh_efec, app_ctrl, sw_ready, l1sub_disable, margining_ready, pme_to_ack, drs_ready, ltssm_en, link_dn_ctrl, cii_enable, cic, elbi, bar_addr_mapping, dbi_ro_wr_en, vid, did

## Findings
- **eFEC is two separate blocks.**
  - `EFEC_CTRL` at AUX+0x120 holds EN, MODE (t3/t4/t5), CRC32 and DVLOW_QUAL (which resets to 1).
  - The Astera block `SCO2_RPLH_EFEC` is at AUX+0x1B8.
  - The config and interrupt enables reset to 0, so FW must program the config, the thresholds and the enables.
- **APP_CTRL is split across two registers.**
  - **AUX+0x2C:**
    - `L1SUB_DISABLE` and `MARGINING_READY` reset to 1.
    - **FW must set `SW_READY`.**
    - The `PME_TO_ACK` bit is tied to 0 (dead).
  - **CORE+0x44:** `INIT_RST`, `DRS_READY` and the retry enables.
  - CORE also holds `LANE_FLIP` at 0x4C and the LTSSM logger at 0x84–0xA4.
- **LTSSM gating.** `APP_LTSSM_EN` is at 0x40 and `LINK_DN_CTRL` at 0x50. HW auto-clears `LTSSM_EN`, so **FW must re-arm it after every core or hot reset**.
- **Link speed.** There is no speed-change interrupt, and the current-rate port is unconnected. Read Link Status through DBI.
- **"ELBI v2" does not exist.** Only the CIC path is live:
  - `CII_ENABLE` resets to 0, so FW must set it.
  - VFs are not supported through the CIC.
- **`BAR_ADDR_MAPPING` is dead RTL.** Leave its EN bit at 0, even though the CSR drop still ships it.
- **VID/DID.** VID is 0x208b and DID is 0x0020. Change the DID only under `DBI_RO_WR_EN` (0x1BC bit 0).
- **Device-info SRAM** is 8 KB. Bits 0/1 control read/write of the top 16 B.
- **Dead PTM/tlut outputs.** The PTM timeout and context interrupts may never fire.

## Sources
- RTL via Neo: `ip.pcie.rtl` pep_macro AUX/CORE CSRs, eip_pcie wrapper (a2 mirror).

> **Open conflict (Neo r09):** `piw_tlp_dns_bar_addr_mapping.v` was cited as live, with `BAR_ADDR_MAPPING_EN` resetting to 0. See [a2-msix-gw-multicast-cap-por.md](a2-msix-gw-multicast-cap-por.md). Treat "dead" as unconfirmed until it is resolved.
