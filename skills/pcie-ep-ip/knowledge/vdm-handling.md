# VDM (Vendor Defined Message) handling — controller, A1 wrapper RTL, FW

Status: verified (docs + Neo A1 RTL paraphrase + local FW). Recorded 2026-11-11. Scope: A1/Iridium.

## Controller (DWC EP 6.42a)
- TX methods: VMI (`ven_msg_*`, Msg only, no MsgD; fmt=0x1 len=0), direct Msg/MsgD on AXI
  bridge subordinate (64-bit addressing), or iATU MWr->Msg translation. Databook p221-224 (§3.7.1.2).
- RX: by default every error-free Msg is decoded onto SII (`radm_vendor_msg`, `radm_msg_req_id`,
  hdr DW3/4 on `radm_msg_payload`) and NOT forwarded to AXI manager; clear filter mask bit to
  forward. Databook p230 (§3.7.2.4).
- `FILTER_MASK_2_OFF` (PF0_PORT_LOGIC): bit0 `CX_FLT_MASK_VENMSG0_DROP` 0=Type0 dropped WITH UR,
  1=passed; bit1 `CX_FLT_MASK_VENMSG1_DROP` 0=Type1 silently dropped, 1=passed. Ctl ref p3189
  Tbl 3-468. A1 reset 0x3 (pass) per Neo (`PF0_PORT_LOGIC.yml` L462-476).

## A1 wrapper RTL (Neo, ip.pcie.rtl@693ce54, ip.pcie.csr@a892fd1)
- RX Path A (AXI forward -> PIW msg mailbox): `piw_tlp.v` diverts DS writes whose awuser TLP
  type[4:3]==`PIW_TLP_CFG.TLP_MSG_TYPE_4_3.VAL` (reset 0b10=Msg) to `msg_mailbox_handler`
  (`MSG_MBOX_FEATURE_EN=1`). Generic for all Msg, no VDM/MCTP decode. Entry = header sideband
  (fmt, dwlen, hdr DW3/4, type, func) + data into `el_mailbox_if`; SW reads CSR block
  `MSG_MAILBOX` (`PIW_MNG.yml`) at MNG PCIe-cluster base |0xD000. Full mailbox back-pressures
  the AXI write (stalls the link inbound). Overflow -> SEI.
  Bypass `PIW_RR_MAIN_CFG.MSG_MBX.BYPASS_EN` (reset 0): single capture regs `MSG_MBX_AW_*`,
  `MSG_MBX_W_*`, held until FW writes `MSG_MBX_AW_VALID_RST`/`W_VALID_RST`.
  IRQs: `PIW_MAIN_CFG` SPI cause MAILBOX bit5, SEI cause MAILBOX bit3 -> `async_piw_spi/sei_intr`
  out of `pcie_ep_cls`. Final uC destination [unverified].
- RX Path B (SII -> CSRs): `PEP_CTRL_CORE.MSG_RX` (RADM_VENDOR_MSG, _TYPE, REQ_ID, PM_TURNOFF,
  UNLOCK, SLOT_PWR_LIMIT) + `MSG_RX_PAYLOAD` (2x32 = hdr DW3/4). Overwrite regs, no FIFO, no IRQ.
- TX VMI: CSR-driven `PEP_CTRL_CORE.MSG_CTRL_0..3` + `MSG_CTRL_REQ.REQ` (cleared on
  `ven_msg_grant`). TX alt: PIW upstream generator (`TLP_UPS_GEN_*`, awuser type + 8-bit mcode,
  done SPI `TLP_GEN_UPS_DONE_WR`, timeout SEI bit4). DWC Msg conversion of that write [unverified].
- No MCTP-specific RTL; MCTP VDM could reach mng only via the generic msg mailbox.

## FW (ip.pcie.sw.fw.ep)
- Production: only `dwc_ep_ctl_vdm_drop_cfg()` (dwc_ep_ctl.c), called from
  `el_pcie_ep_linkup_cfg()` (el_pcie_ep_link.c). `drop_en=true` writes 0 -> drop. main had
  `false` (pass); user's 2026-11 change to `true` (drop) is intentional.
- SV only: `el_piw_sv_ups_gen_msg()` (el_piw_sv.c, tested with codes 0x7E/0x7F),
  MSG_RX dump in dwc_ep_ctl_sv.c / shell. No mailbox consumer, no VMI TX, no MCTP-over-PCIe in ip/mng.

## A2 spec (EP Spec Rev 2.0.0 §Downstream Message)
Capture+IRQ (back-pressure) or dedicated mailbox (base/size/PI/CI/threshold, full=drop or halt,
entry = header + 64B payload). VMI RX "not supported/TODO".

## Autonomous MNG-fabric write path (Neo follow-up 2026-11-11, + ip.infra.rtl.shared.iridium@9c296e5)
- With VENMSG pass, every forwarded Msg/MsgD AXI write (awuser ttype[4:3]==`TLP_MSG_TYPE_4_3` reset 0x2) is diverted
  to `msg_mailbox_handler`; non-message writes go to BMU/BAR path. Messages don't fall through at reset.
- Mailbox is NOT internal storage: `mailbox_msg_write` (infra shared RTL) autonomously AXI-writes each
  entry to `base + (PI & 2^mb_size)<<msg_size` via `main2lbw_msg_mbx_*` -> `piw_lbw_mng` MNG dns
  arbiter -> MNG fabric. awuser/awprot/awcache = 0. Address from programmed base, never host header.
- No enable bit; gated only by ~overflow and mb_mode==0. Resets: base 0, mb_mode 0 (HW write),
  mb_size_l2 0x10 (64K entries), msg_size_l2 5 (128B), warn thr 0x8000, PI/CI 0, BYPASS_EN 0.
  => unprogrammed, host messages that produce a W beat are written to MNG addr 0..8MB, OKAY bresp to DWC; backpressure
  only after 64K unconsumed entries. One entry per W beat (multi-beat payload = several entries).
- No RR/CfgR/function-spoofing check on message-in leg; whether piw_lbw_mng range regs gate the
  msg_mbx leg / which SL awuser=0 maps to, and what is at MNG addr 0 from PIW: [unverified].
  DWC bridge awaddr / W beat for no-payload Msg: [unverified] (encrypted) -> MsgD VDMs
  (e.g. MCTP) are established; whether no-payload Msg VDMs create an entry is open.
- FW (A1): production never programs MSG_MAILBOX base (only el_piw_sv.c SV code writes
  MAILBOX_BASE_ADDR_LSB); DTS a1_hsi.dtsi maps it as PIW reg-name `mailbox_regs`. So the
  drop config (FILTER_MASK_2=0) is what closes this path.

## Silent drop of ALL VDMs is not possible on A1 (verified 2026-11-11)
- Controller: Type0 can only be dropped WITH UR (ctl ref p3186 UR table: "Vendor MSG Type0 with
  CX_FLT_MASK_VENMSG0_DROP not set -> UR"); `CX_FLT_MASK_MSG_DROP` (SYMBOL_TIMER_FILTER_1 bit29)
  excludes vendor MSGs. Type1 bit1=0 is silent.
- PIW (Neo, msg_mailbox_handler.v / mailbox_core.v @ip.infra.rtl.shared.iridium 9c296e5): no
  discard/disable bit; MB_MODE=1 doesn't gate the aux msg input; overflow stalls (el_tap), never
  drops; BYPASS_EN avoids MNG write but stalls each beat until FW clears *_VALID_RST.
  MSG_MBOX_FEATURE_EN=0 (build-time) would answer SLVERR. Silent stall-free Type0 drop needs RTL.
- Decision (user, PR ip.pcie.sw.fw.ep#476): keep FILTER_MASK_2[1:0]=0 (Type0 UR, Type1 silent).

## Field impact: mailbox overflow wedges the downstream posted path (A1, infra-ai#461 / PH-020)
- [RTL, Neo] CI is never advanced, so after 2^L2_SIZE (64K) messages the next message is held: AW is accepted, W is never taken, and no B is returned. The overflow interrupt is masked at reset.
- [RTL, Neo] The `piw_tlp.v` message fork sits in front of every host downstream write (PIW CSRs and doorbells, MNG/CTRL/MEM). All of them stall. ELBI is a separate path.
- [Databook p.381 Table 6-5 rule 5] The AXI bridge does not let an inbound Cpl pass an earlier inbound P on the same VC, so completions for upstream (AXI subordinate) reads are held behind it.
- [Databook p.410, p.386] On Cpl timeout the bridge returns an AXI error (type from `AMBA_ERROR_RESPONSE_DEFAULT_OFF`). On the MuC M7 that is a precise BusFault.
- Field signature: `range-reg-aw-miss` at MNG 0..0x7fff80 every VDM, then silence, then a HARD FAULT on a host-window (0xB region) read, roughly one completion timeout after the next host-ring access.
- Unverified: the A1 Synopsys `CC_IB_WREQ_PTRK_EN`/`CC_ORDRB_EN`/Cpl-queue mode, which Neo's mirror lacks.

## Downstream VDM vs upstream traffic (Neo, A1 iridium@3afb411, 2026-11-11)
- A mailbox stall blocks the downstream posted path only, back to the controller RX posted queue.
- The upstream path (pep_axi_slv to TX, and the CplD return via radm_trgt1_cpl) is separate in PIW RTL.
- Coupling via DWC ordering: ORDER_RULE_CTRL_OFF.CPL_PASS_P (B+0x1b4, ref p3287) resets to 0 = CPL cannot pass a halted P queue; ORDRB_CONTROL_CPL_OFF.CPL_PASS_P (p3117) also resets to 0 when CC_ORDRB_EN=1. FW writes neither, so a stalled VDM posted queue can hold upstream read completions. Unverified: the A1 CC_ORDRB_EN build value.
- A mailbox base of 0 aliases PSOC MNG 0..8MB (PIO, FW_SRAM, CCMs). The FIU mapping is unverified.
- Scope (Neo): one inbound AXI manager port, and the msg fork sits before BAR routing. A stall blocks every inbound posted write for all BARs and all Msg/MsgD types. Reads (AR), ELBI and CDM config are not blocked by the fork. Msg and MemWr share the AW/W stream (message header in AW user). The fork is unbuffered and in-order, which gives head-of-line blocking.
