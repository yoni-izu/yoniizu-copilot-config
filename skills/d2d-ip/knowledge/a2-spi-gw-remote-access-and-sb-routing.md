# A2 SPI-GW broadcast (write-only, FIFO 4), remote access by die-ID address bits, MNG_TO_SB sideband routing (reset 1, order timeout→credits→MNG_TO_SB), no cross-die mailbox

- **Recorded:** 2026-11-11
- **Status:** Neo RTL-verified
- **Tags:** a2, d2d, spi-gw, broadcast, primary_broadcast_en, remote access, die id, mng_to_sb, mng_axi_sb_cr, sideband, vdm, credits, mailbox, doorbell, enumeration

## Scope / Question
How FW reaches other dies on A2: broadcast, remote access, MNG over sideband; is there cross-die mailbox hardware?

## Findings

- **SPI-GW broadcast** is write-only; a broadcast read raises an illegal-read interrupt. `PRIMARY_BROADCAST_EN` resets to 1. Response FIFO depth 4. FW needs a single gateway owner and should drain with `OS_CNT` / `CTRL_FLUSH`.
- **Remote access** routes on die-ID address bits (32 MB per die); there is no enable bit.
- **MNG over sideband**: `MNG_AXI_SB_CR` in D2D_CMN — `MNG_TO_SB` reset 1, timeout reset 0xF00, credits reset 8. Hardware samples MNG_TO_SB per flit and forks to sideband (credit-limited) or mainband. The setting is global and must be **identical on both dies**; program timeout → credits → MNG_TO_SB. Sideband sends are not gated on mainband link-up, so the sideband is the default pre-link path. ROM/SRAM boot clears MNG_TO_SB (switch to mainband).
- VDM credit checks: `AW_ADAPTER_IMP_SPEC_RDI_REG` REMOTE_VDM_CRD_CHK_EN (reset 0), LOCAL_VDM_CRD_CHK_EN (reset 1); whether FW must set them is [unverified].
- **No cross-die mailbox, doorbell or enumeration hardware.** The die ID is a strap (`async_die_id`); PSOC mailboxes are per-die. Discovery is FW-only (and not POR per the user).

## A2 vs A1

A1 has the same MNG-over-sideband mechanism; A2 differences are not in this path.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- RTL refs (via Neo): D2D_CMN.yml:328-352; d2d_pkt_enc.v:149,306-345; d2d_mb_to_sb.v:104-139; d2d_init_die0_die1.lua:58-85; psoc_ic_align_addr_to_multdie.v.
- A2 spec Rev 2.0.0 ¶814-815 (MNG via sideband VDM).
