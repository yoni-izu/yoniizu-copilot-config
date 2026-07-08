# PIW Transaction Drain Mechanism (direction: inbound only)

- **Recorded:** 2026-06-15
- **Status:** Verified from FW (el_piw driver) + cluster-architecture doc
- **Tags:** drain, piw, inbound, host-to-device, axi master, ruser, buser, sei, timeout, ctrl mng main port, upstream termination

## Scope / Question

Is the PIW drain mechanism only for inbound PCIe traffic, or also for outbound (upstream
device→host)?

## Answer

**Inbound only** (host → device). The drain protects the AXI **master** ports through which
the PCIe EP masters host-originated traffic into the SoC fabric. It is *not* the
device→host/upstream path — that is a separate "upstream access termination" mechanism
(JIRA IPPCIESW-341), and downstream config termination is yet another mechanism (DS_TERM,
see `ds-term.md`).

## Findings

- Drain is implemented in PIW across **three AXI master ports**: `MAIN`, `MNG`, `CTRL`
  (`enum el_pcie_piw_port_id` → MAIN/MNG/CTRL). These are exactly the ports the cluster-arch
  doc lists as "PIW → PEP : AXI master/slave : Host traffic routing" and "Host → PIW : LBW
  (MMIO)" / "Host → PIW : HBW (DMA)". I.e. they carry **host-initiated reads/writes** (MMIO
  CSR access + bulk DMA) that the controller masters into SoC after BMU address translation
  (TLP → SoC).
- Per port the drain tracks both transaction directions of the AXI master: `RD_DRAIN`
  (host read) and `WR_DRAIN` (host write) — `enum` `EL_PCIE_DRAIN_TRANS_READ/WRITE`.
- On drain it fabricates the AXI response back toward the controller master: `*_DRAIN_RESP`,
  `*_DRAIN_RUSER` (read-data channel), `*_DRAIN_BUSER` (write-response channel). RUSER/BUSER
  are slave→master signals, confirming the device side is the master waiting on the SoC slave
  → inbound direction. Without drain, a non-responding SoC slave (target subsystem in reset /
  powered down / error) would hang the controller's master port.
- Config (`el_piw_drain_cfg`): per port sets `DRAIN_CNTR` = timeout cycles
  (`CONFIG_EL_PIW_DRAIN_TO_CYCLES`, reset 0xffff), unmasks the top-level + local drain SEI
  causes, then writes `DRAIN_ENABLE=1`. Gated by `CONFIG_EL_PIW_DRAIN`.

## Captured drain address width (RTL-verified)

The `*_DRAIN_ACTIVATED_0_ADDR_{H,L}_INFO` CSR fields are each 32-bit in the spec
(`VAL_SIZE 0x20`, mask `0xffffffff`), and FW always assembles a 64-bit value
(`drained_address = ((uint64_t)high32 << 32) | low32`). **But how many of those bits are
real depends on the port**, because the H half is only wired on the HBW/memory port:

- **MAIN port (HBW / memory / DMA path)** — captures the **full 64-bit** address:
  ```verilog
  // piw_macro/rtl/piw_hbw.v   (wire [63:0] csr_sts_mem_drain_{wr,rd}_addr)
  ..wr_drain_activated_0_addr_h_info_val_wr_data = csr_sts_mem_drain_wr_addr[63:32];
  ..wr_drain_activated_0_addr_l_info_val_wr_data = csr_sts_mem_drain_wr_addr[31:0];
  ..rd_drain_activated_0_addr_h_info_val_wr_data = csr_sts_mem_drain_rd_addr[63:32];
  ..rd_drain_activated_0_addr_l_info_val_wr_data = csr_sts_mem_drain_rd_addr[31:0];
  ```
- **MNG & CTRL ports (LBW / MMIO CSR path)** — only the **lower 32 bits**; the H register is
  **hardwired to zero** in RTL:
  ```verilog
  // piw_macro/rtl/piw_lbw_mng.v  and  piw_lbw_ctrl.v
  ..interrupt_sei_rd_drain_activated_0_addr_h_info_val_wr_data = '0;
  ..interrupt_sei_wr_drain_activated_0_addr_h_info_val_wr_data = '0;
  ..wr_drain_addr (..._wr_drain_activated_0_addr_l_info_val_wr_data); // L = AXI_MNG/CTRL_ADDR_WDT wide
  ..rd_drain_addr (..._rd_drain_activated_0_addr_l_info_val_wr_data);
  ```
  The LBW captured address width is `AXI_MNG_ADDR_WDT` / `AXI_CTRL_ADDR_WDT` = **32 bits**
  (e.g. CTRL fork mask `32'hffff_e000`, MNG masks `32'h00ff_xxxx`). LBW/MMIO targets live in
  the 32-bit CSR window, so the upper 32 bits are meaningless and tied off.

**Net:** drain address is full 64-bit only on **MAIN/HBW**; on **MNG/CTRL** it is effectively
**32-bit** (upper half always reads 0). FW's `(high32<<32)|low32` is correct for MAIN and
simply yields a value < 4 GiB for MNG/CTRL.
- On a drain event a SEI fires (`PIW_{MAIN,MNG,CTRL}_CFG_DRAIN_INTERRUPT_SEI_CAUSE`, with
  `RD/WR_DRAIN_ACTIVATED` and `RD/WR_DRAIN_UNEXPECTED_RESP` causes) carrying the offending
  address (ADDR_H/L_INFO). Optionally notified to the host driver over mailbox
  (`EL_PCIE_SEI_DRAIN_INTR`, `CONFIG_EL_PIW_DRAIN_NOTIFICATION`).
- Related JIRA: IPPCIESW-241 "Transaction Drain Configuration" (RAS), IPPCIESW-311/-370
  "Connect drain interrupt in ARM FW".

## Not the drain (don't confuse)

- **Outbound / upstream (device→host)** in-flight termination = "Upstream Access Termination
  on Hot Reset" (IPPCIESW-341): on `smlh_req_rst_not`, pending DMAs / upstream writes are
  terminated. Separate logic.
- **Downstream config/CSR termination** = `PEP_CTRL_AUX_DS_TERM_CTRL` (DS_TERM gate at the
  DWC IIP master output). See `ds-term.md`.

## Decoding a captured MNG-port drain address (worked example)

The MNG/CTRL drain address is a **32-bit management CSR-window offset** (BAR0 CFG_MNG
sub-window). To identify the target, decode against the a1 project bases
(`build_artifacts/include/a1/project/project_bases.h`, `MNG_*` entries):

- The **valid management CSR decode is only the low 16 MB** (`mask 0x00ff_xxxx`); the
  populated `MNG_*` register blocks end at window offset `~0x00ffd000`
  (`MNG_MRTR_VSP_DEALGN_EDGE_EAST_0`). The `0x0100_0000`–`0x03ff_ffff` remainder of the
  64 MB CFG_MNG BAR sub-window (`CFG_MNG_SIZE 0x4000000`, from `a1_pcie_map.h`) has **no
  backing CSR slave** → an access there gets no AXI response → PIW MNG drain times out and
  returns **SLVERR** (resp=2) with reason **timeout** (0). (A truly bogus/undecoded address
  can instead give DECERR=3; timeout+SLVERR means the fabric accepted but nothing completed.)
- **Example (real incident, a1, 0000:af:00.0):** two MNG drains —
  read `0x1030020`, write `0x1ffd084`. Both carry the **same spurious high bit
  `0x0100_0000`** (bit 24 = 16 MB). Masking to the 24-bit decode (`& 0x00ffffff`):
  `0x1030020 → 0x030020`, `0x1ffd084 → 0xffd084` = `MNG_MRTR_VSP_DEALGN_EDGE_EAST_0`
  (`0xffd000`) + `0x84` = `VSP_DEALGN_EDGE_VCORE_INTF_CRD_15`. The intended targets are
  real low-16 MB CSRs; the initiator added a constant `+0x0100_0000` base error, pushing
  both into the dead upper window. **Signature: multiple drains sharing an identical high
  offset ⇒ wrong base pointer / window index in the initiator (host driver or agent),
  NOT a hung/power-gated IP.** A dead-IP drain would instead land *on* a populated block
  offset with resp reflecting a stuck slave.
- **Initiator fingerprinting via the register pair:** the two masked targets were
  `SOC_EFUSE_0_63 + 0x20` (read) and `VSP_DEALGN_EDGE_EAST + 0x84 = VCORE_INTF_CRD_15`
  (write). That read-eFuse-then-write-VCore-credit pair is exactly the **VCore binning
  manager** sequence (`fw/infra/drivers/el_vcore_binning_mgr`: `read_vcore_efuse_binning()`
  → `dispatch_credit_binning()` → `el_vcore_binning_program_credits()`). Lesson: decode
  *all* drained addresses together and match them to a known driver's touchpoint sequence —
  the access *pattern* identifies the initiator even when the base is corrupted. No host
  KMD/regcli code touches these CSRs, so a host-driven bring-up/binning agent (not the
  on-die FW driver) is the likely initiator here.

## Sources
- FW: `fw/ip/pcie/drivers/el_piw/el_piw.c` (`el_piw_drain_cfg`, `drain_info_list`,
  `el_piw_port_drain_process_cause` — assembles `(high32<<32)|low32`),
  `el_piw/Kconfig` (`EL_PIW_DRAIN*`),
  `drivers/el_pcie_ep/el_pcie_ep.c` (calls `el_piw_drain_cfg` under `CONFIG_EL_PIW_DRAIN`)
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `piw_macro/rtl/piw_hbw.v` (MAIN, full 64-bit),
  `piw_macro/rtl/piw_lbw_mng.v` + `piw_lbw_ctrl.v` (MNG/CTRL, addr_h tied `'0`, addr_l =
  AXI_MNG/CTRL_ADDR_WDT=32), `piw_macro/rtl/piw_pkg.v` (`PCI_*_DRAIN_*` params)
- Regs: `build_artifacts/include/specs/piw_main_cfg_regs.h`
  (`PIW_MAIN_CFG_DRAIN_INTERRUPT_SEI_{RD,WR}_DRAIN_ACTIVATED_0_ADDR_{H,L}_INFO`, each 32-bit)
- Doc: `fw/ip/pcie/docs/pcie-cluster-architecture.md` (Key Interfaces table: PIW↔PEP AXI
  master/slave, Host→PIW LBW/HBW)
- JIRA: IPPCIESW-241, IPPCIESW-341, IPPCIESW-311/-370
