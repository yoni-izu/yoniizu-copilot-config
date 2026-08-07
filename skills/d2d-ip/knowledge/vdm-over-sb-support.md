# VDM over SB Support (Vendor Defined Messages over Sideband)

- **Recorded:** 2026-07-15
- **Status:** Verified from spec + AlphaWave CSR headers (RTL-generated)
- **Tags:** vdm, vmsg, vendor defined message, sideband, sb, ucie.sb, fw download, mng fabric, mainband debug, generation fsm, reception fsm, message code 0xff, axi to vdm, credits, checksum, hardware mode, software mode, alphawave, aw, snps caveat

## Scope / Question
What is the D2D "VDM over SB support" feature — Vendor Defined Messages over the UCIe sideband interface. Also: is it present in the AlphaWave controller (which Iridium actually uses), or SNPS-only as the spec claims?

## Controller-vendor correction (IMPORTANT)
Spec Rev 2.0.0 says this feature is "applicable only when both sides use the **SNPS** controller" (para 716). **This caveat is stale for Iridium.** The shipping D2D IP uses the **AlphaWave** controller + PHY — every generated CSR block is `aw_*` (e.g. `aw_sb0/sb1`, `aw_adapter_imp_spec_rdi`, `aw_phy_imp_spec`, `aw_phy_d2d`, `aw_firmware`). No SNPS/Synopsys reference exists in the headers. **VDM-over-SB IS implemented in the AlphaWave CSRs:**
- `aw_sb0/aw_sb1` sideband blocks implement a dedicated **VMSG** (vendor message = VDM) path, separate from normal MSG: TX/RX VMSG sideband FIFOs with OVF/UNF + header/data parity error interrupts (`TXSB_FIFO_ERR_VMSG_SFIFO_OVF/UNF`, `RXSB_FIFO_ERR_STAT_VMSG_HDR/DAT_PARITY_ERR`), reception status/interrupts `RXSB_VMSG_AVAIL` / `RXSB_VMSG_RDREADY`, TX status `VMSG_WR_OK`, and `RX_VMSG_RDREADY_CLR`. `txsb_cfg7` = "Trigger to enable SB message transfer" (SW-mode trigger).
- `aw_adapter_imp_spec_rdi` implements VDM credit checking: `ADAPTER_E2E_CREDITS_SUPPORT.REMOTE_VDM_CRD_CHK_EN` / `LOCAL_VDM_CRD_CHK_EN` (matches spec's "maintain credits, default threshold 4").
- `aw_phy_imp_spec` has `REG_EXT_VDM_CTRL_EN` (external VDM control) and `REG_MMPL_VDM_CTRL_EN` with `LOCAL_VDM_CRD_CHK_EN` / `LOCAL_MSG_CRD_CHK_EN`.
Conclusion: the feature is real and vendor-agnostic in our silicon; the "SNPS-only" line reflects the spec being written against a generic Synopsys assumption and does not apply to the AlphaWave-based Iridium D2D.

## Findings

**What it is** — A UCIe-controller enhancement listed as "Sideband vendor defined message support: allows user defined messages over sideband interface." Spec tags it SNPS-only, but see the correction above — it is present in the AlphaWave controller.

**Why it exists (Sideband Communication)** — Lets a die talk to the remote die over the low-bandwidth **sideband (SB)** path independent of the mainband. Two primary uses:
- **Providing initial firmware** (FW download to the secondary die) — this is what rev 1.1.0 explicitly added ("add VDM support over UCIe.SB for FW download").
- **Debugging the remote die when the mainband interface is not functional** (debug the mainband path when there's a problem).

**Transport basis** — Built on UCIe **Vendor Defined Messages (VDM)** carried over the UCIe SideBand interface. SB messages = 64-bit header + 32b/64b data. UCIe permits 3 packet categories: register accesses (CFG/MMIO R/W), messages *without* data (Link Management or Vendor Defined Packets), and messages *with* data (Parameter Exchange, Link Training, or Vendor Defined carrying 64b). Every packet carries a 5-bit opcode, 3-bit srcid, 3-bit dstid.

**Two generation modes:**
- **Hardware mode** — HW captures MNG-fabric traffic and ships it over SB as VDM, handled by a dedicated FSM on both sides.
  - Uses message **code 0xFF**; message **subcode** selects type (AXI.AW / AR / B / R).
  - Every AXI request is converted into **two VDMs** carrying all attributes (AxADDR, AxUSER, AxLEN, AxSIZE, WDATA, WSTRB, RDATA, xRESP, etc.).
- **Software mode** — SW builds the SB message via indirect registers and triggers it; HW then sends it to the remote side.

**Protocol Layer Communication contract** — HW maps the MNG-fabric transaction through a UCIe VDM and decodes it at the receiver. HW monitors control + data **checksum**; on failure it reports the error to the **primary die** by writing a dedicated CSR. When using the RDI I/F (pl/lp_cfg_*), HW maintains **credits** with a default threshold of **4 (configurable)**. Supports primary→secondary die communication and message-with-data VDM.

**FSM instantiation** — Each side of the link instantiates **both** a Generation FSM and a Reception FSM.

## Sources
- Spec: `D2D Specification Rev 2.0.0.docx` — feature list para 715–716; SB message format para 456–468; Protocol Layer Communication para 813–825; Sideband Communication para 1154–1170; rev history table T0 R15 (v1.1.0 "add VDM support over UCIe.SB for FW download").
- AlphaWave CSR headers (from `ip-d2d-sw-csr-ci` artifact, main 1.40.0): `aw_sb0_reg_regs.h` / `aw_sb1_reg_regs.h` (VMSG TX/RX FIFO, `SB_SFIFO_INT1/2`, `TXSB_STALL_RDREG1.VMSG_WR_OK`, `txsb_cfg7` trigger, `RXTXSB_CFG4.RX_VMSG_RDREADY_CLR`); `aw_adapter_imp_spec_rdi_reg_regs.h` (`ADAPTER_E2E_CREDITS_SUPPORT.{REMOTE,LOCAL}_VDM_CRD_CHK_EN`); `aw_phy_imp_spec_reg_regs.h` (`REG_EXT_VDM_CTRL_EN`, `REG_MMPL_VDM_CTRL_EN`).
