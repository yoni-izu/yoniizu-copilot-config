# A2 PCIe interrupt counts, stubbed sources, and version/tag pitfalls

- **Recorded:** 2026-11-12
- **Status:** Verified from RTL/CSR (Neo, a2 r01/r04)
- **Tags:** a2, interrupts, cause count, pep, piw, ppw, sei, stub, todo, version, 6.60a, 1.06b, fullchip tag, a2_rev2p0, a2.csr, ip.pcie.csr

## Findings
- **Interrupt causes:** PEP **60** in total (44 of them are AUX only), PIW **99**, PPW **0**, giving **159** in total. The older "161 / PIW 117" figure is wrong.
- **Stubbed sources.** The PPW SEI is tied to 0. The PIW SEI/ECO/SERR/DERR/viral inputs are stub-tied in `pcie_ep_cls.v` around lines 4995–5010; which build drives them is unverified. Do not plan FW handlers as if those sources fire.
- **Isolation indications:** there are 5 instances (MSTR, ELBI, SLV, DBI and MNG).
- **RTL TODOs:** 121 in `.v` files. The "123" figure counted a `.orig` backup.
- **Versions and tags:**
  - The controller is 6.60a-lu05 and the PHY is 1.06b. The A2 uarch doc cites the 6.52a databook, which is stale.
  - **There is no `atlantic2_fullchip_1_4_0` tag in a2.csr** (that repo has no tags at all). Pin by the `a2_rev2p0` branch or by SHA.
  - The PEP/PIW/PPW CSRs live only in `ip.pcie.csr`. `a2.csr` holds the PHY maps, the memory YAMLs and the interrupt CSV.
  - `babel_piw_rom.yml` still names a Gen6 hex, which is stale.
- **Comparison pitfall.** A1 `main` of `ip.pcie.rtl` already carries the A2 port. Compare A2 against `iridium_rev2p4_be`; diffing against A1 `main` hides the deltas.
- **Die-ID** is in addr[26:25] with a 32 MB stride. The remote-CSR gating is unknown.

## Chip wiring, PLIC and clear order (Neo r06/r08, 2026-11-12)
- **FC lines:**
  - Line numbers: DERR 192, DPE 208, ECO 224, SEI 240, SERR 256, SPI 272.
  - Each line has a 16-bit slot, but only bit 0 is used.
- **PPW lines:**
  - Only `PPW0_SEI` (288) is wired.
  - PPW DERR/SERR are not wired in A2. In A1 they were at 480/496.
- **PLIC:**
  - Each CM7 has its own PLIC.
  - In w2w mode, IRQ = wire/16: 192→12 … 272→17, PPW SEI→18.
- **Clear order:**
  - `WIRE_STS` is edge-latched W1C.
  - FW must clear in this order: clear `WIRE_STS` → clear the cause tree bottom-up → re-read `MASKED_INTR`.
  - A W1C on a parent is swallowed while a child cause is still high.
- **PHY interrupt:**
  - The PHY interrupt has no cause block (`ppw_wrap.v:551` TODO).
  - It exits only on PPW SEI 288, so FW must decode the cause from PHY register space.
- **Messages:**
  - RX messages are polled via `PEP_CTRL_CORE.MSG_RX` at +0x5c; the payload is at 0x60/0x64/0x68.
  - Vendor and unlock messages raise no cause and are overwritten by the next message.
  - The only FW TX message is PME (AUX APP_CTRL bit 7).
  - PIW `MSG_MAILBOX` (+0xd000) has its own SPI line; its semantics are unknown.
- **PME_TO_Ack:**
  - On the `RADM_PM_TURNOFF` cause, FW sets AUX APP_CTRL 0x2c bit 4. If FW never sets it, the host stalls entering L2/L3.
  - **Conflict:** r08 says the Gen7 subsystem port is tied off (`..._gen7_axi4_subsys_wrap.v:549`). Confirm with the RTL owner.
- **D3cold and AUX power:**
  - D3cold is advertised (`PME_SUPPORT` = 0x19).
  - `SYS_AUX_PWR_DET` is AUX 0x34 bit 5. Leave it at 0.
- **Power causes:**
  - Power break comes from a GPIO/PSOC input and raises SPI causes 7 and 8.
  - `EMERGENCY_PWR_RED` is SPI causes 9/10.
  - `PWR_BUDGET_SEL` is SPI cause 11.
  - The power budget registers are AUX 0xA8, 0xAC (×16) and 0xEC.
- **Other new causes:**
  - `EFEC_INTR` (cause 15).
  - PTM: timeout, context-valid-fall and clock-updated (causes 16–18).
  - `ISO_WD_CNT`.
  - AR/AW `ISO_INDICATION`.
- **Error register offsets:**
  - PIW_MAIN_CFG: DPE 0xf8, SPI 0x108, SEI 0x120, SEI_2 0x130, ECO 0x140, SERR 0x150, DERR 0x160.
  - AUX: SEI 0x148, SPI 0x158, SERR 0x168, DERR 0x178.

## Sources
- RTL/CSR via Neo: ip.pcie.rtl 77b45f10, ip.pcie.csr 6904415b, a2.rtl 875d8516, eip_pcie 1b48d8dc, a2.csr 2a12a536.
