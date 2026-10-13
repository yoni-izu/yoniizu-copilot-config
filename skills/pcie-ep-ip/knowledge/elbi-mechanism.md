# ELBI — mechanism, and how the config-space offsets are determined

- **Status:** Verified from databook + register reference + RTL + generated CSR headers + EP spec + FW
- **Recorded:** 2026-10-13
- **Tags:** elbi, external local bus interface, lbc, lbc_ext, ext_lbc, cii, cic, configuration
  intercept, config_limit_reg, target_above_config_limit_reg, cfg_tlp_bypass_en_reg,
  misc_control_1_off, pep_cic_handler, pep_elbi_handler, cii_elbi_indr_offset, indirect fsm,
  0xff0, 0xff4, 0xff8, 0xffc, config address data command status, doe, cii_lbc_halt,
  cii_lbc_override_en, elbi offsets, elbi enable

---

## 1. Two different "ELBI" mechanisms exist — know which one you are looking at

Synopsys offers **two** ways to put application-owned registers into the 4 KB PCIe
configuration space. Element's A1/Iridium PEP uses the **second** one.

| | (a) Legacy "ELBI redirect" | (b) CIC / CII intercept |
|---|---|---|
| Databook | §3.6.3.2, p.178–180 (Table 3-12, Fig 3-35) | §3.6.3.3, p.180–182 (Fig 3-36/3-37) |
| How offsets are chosen | **One contiguous window at the TOP of config space**, defined by a *limit*: `addr > 4 × CONFIG_LIMIT_REG` | **Arbitrary individual DW offsets**, compared against the intercepted header address |
| Controller IF | LBC external bus: `lbc_ext_cs[NF-1:0]`, `lbc_ext_addr`, `lbc_ext_dout`, `lbc_ext_wr`, `ext_lbc_din`, `ext_lbc_ack` | CII: `lbc_cii_hv/dv`, `lbc_cii_hdr_*`, `lbc_cii_data`, `cii_lbc_halt/override_en/override_data/cpl_status` |
| Who completes the request | the **application** (controller waits for `ext_lbc_ack`) | the **controller** (CDM); the app only *halts* and *overrides* the data |
| Element A1 | **`pep_elbi_handler.v` — NOT instantiated** (dead/legacy) | **`pep_cic_handler.v` — instantiated** in `pcie_iip_device_wrap.v:3446` |

> Databook explicitly: the legacy redirect *"is superseded by Configuration Intercept
> Controller (CIC)"* (p.178).

---

## 2. (a) How the legacy ELBI window is determined — `MISC_CONTROL_1_OFF`

`MISC_CONTROL_1_OFF` @ **DBI base + 0x1bc** (reference manual §3.2.44.44, p.3290–3294):

| Bits | Field | Meaning |
|---|---|---|
| 17:8 | `CONFIG_LIMIT_REG` | **DWORD** address limit. CFG addr **<** limit → CDM; **>** limit → routed per `TARGET_ABOVE_CONFIG_LIMIT_REG`. Reset = **0x3FF DWORDs = 0xFFF bytes** ⇒ controller consumes **all** CFG by default. Sticky. |
| 19:18 | `TARGET_ABOVE_CONFIG_LIMIT_REG` | `0x1` = **ELBI**, `0x2` = **TRGT1** (default). Sticky. |
| 7 | `CFG_TLP_BYPASS_EN_REG` | `1` = route **all** CFG TLPs per `TARGET_ABOVE_*`, ignoring `CONFIG_LIMIT_REG`. Ignored while `app_req_retry_en` is asserted. |

Key consequences (databook Table 3-12 footnote c, p.180):
- The ELBI region is **not a set of arbitrary offsets** — it is everything **above**
  `4 × CONFIG_LIMIT_REG`, i.e. a single contiguous block ending at 0xFFF.
- `CONFIG_LIMIT_REG` must be set **below** its default to have any effect, and *"normally you
  would never set it to less than 0x340 DWORDs (= 0xD00 bytes)"* — the top of the Synopsys Port
  Logic register space.
- The accessed PF is signalled on `lbc_ext_cs[NF-1:0]`; VFs on
  `lbc_ext_vfunc_active`/`lbc_ext_vfunc_num`; `lbc_ext_bar_num[2:0] = 3'b111` marks a **CFG**
  (as opposed to BAR-matched MEM/IO) ELBI access.
- `CX_FLT_MASK_UR_FUNC_MISMATCH` may only be set when `CONFIG_LIMIT_REG=0 &&
  TARGET_ABOVE_CONFIG_LIMIT_REG=2`, or `CFG_TLP_BYPASS_EN_REG=1 && TARGET_ABOVE=2`
  (reference manual p.3186).

**A1 state:** FW never programs these — `CONFIG_LIMIT_REG` / `TARGET_ABOVE_CONFIG_LIMIT_REG`
appear only in the bring-up **dump** list (`ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl_bringup_fields.c`,
`misc_control[]`). Left at defaults ⇒ the legacy redirect is **inactive**.

---

## 3. (b) How the Element ELBI offsets are determined — CSR-programmable, CII-compared

`pep_cic_handler.v` hardcodes **nothing** for the live decode. It compares the intercepted
config header address against **four CSR fields**:

```verilog
assign lbc_cii_elbi_indr_offset_hit = (lbc_cii_hdr_addr == cfg_elbi_indr_addr_addr)
                                    | (lbc_cii_hdr_addr == cfg_elbi_indr_data_addr)
                                    | (lbc_cii_hdr_addr == cfg_elbi_indr_cmd_addr)
                                    | (lbc_cii_hdr_addr == cfg_elbi_indr_sts_addr);
assign elbi_indr_access = cfg_elbi_indr_enable & (!lbc_cii_hdr_vfunc_active)
                        & lbc_cii_hv & lbc_cii_elbi_indr_offset_hit;
```

`lbc_cii_hdr_addr` is **12 bits** = the DW-granular byte offset inside the 4 KB config space,
so each ELBI control can be placed at **any** offset in 0x000–0xFFF, independently.

### The CSRs (`csr_pep_ctrl_aux`, generated header `pep_ctrl_aux_regs.h`)

| Register | Offset (PEP_AUX +) | Fields | Reset |
|---|---|---|---|
| `PEP_CTRL_AUX_CII_ELBI_INDR_OFFSET_0` | **0x10** | `ADDR_OFFSET` [11:0], `DATA_OFFSET` [23:12] | **0xff4ff0** → ADDR=**0xFF0**, DATA=**0xFF4** |
| `PEP_CTRL_AUX_CII_ELBI_INDR_OFFSET_1` | **0x14** | `CMD_OFFSET` [11:0], `STS_OFFSET` [23:12] | **0xffcff8** → CMD=**0xFF8**, STS=**0xFFC** |

Both are `SW_ACCESS=0x2` (RW) with `*_HW_ACCESS=0x0` ⇒ **pure SW-programmable, HW never writes
them**. So **the well-known 0xFF0/0xFF4/0xFF8/0xFFC are only the RESET VALUES, not wired
constants** — SW can relocate all four.

> Contrast: the dead `pep_elbi_handler.v` *does* hardcode them as
> `localparam ELBI_{ADDR,DATA,CMD,STS}_OFFSET = 32'hFF0/FF4/FF8/FFC`. If you grep RTL for the
> constants you land on the unused module; the live decode is the CSR one.

### Enable / control — `PEP_CTRL_AUX_CII_CTRL` @ PEP_AUX + **0x8**, reset **0xa**

| Bit | Field | Reset |
|---|---|---|
| 0 | `CII_ENABLE` (generic CII intercept/notify) | **0** |
| 1 | `CFG_ELBI_INDR_ENABLE` | **1** |
| 2 | `CFG_ELBI_INDR_STS_CLR` | 0 |
| 3 | `CFG_CII_LBC_HALT_HIGH_EN` | **1** |
| 4 | `CFG_CII_LBC_HALT_CLR` (HW-clearing) | 0 |
| 5 | `CFG_CII_LBC_HALT` | 0 |
| 6 | `CFG_CII_LBC_OVERRIDE_EN` | 0 |
| 9:7 | `CFG_CII_CPL_STATUS` | 0 |

⇒ **ELBI indirect is live out of reset with zero FW programming.** The generic CII
notification path is off at reset; FW turns it on only transiently for BDF capture
(`dwc_ep_ctl_enable_bdf_capture()` / `dwc_ep_ctl_capture_bdf_once()` → `INTR_SPI_CAUSE.CII_ACCESS`
bit 4, data from `PEP_CTRL_AUX_CII_IF_2`), then clears `CII_ENABLE` again.

---

## 4. The indirect-FSM protocol (EP Spec Rev 2.0.0 §7.10, RTL-confirmed)

Host drives, over **plain config read/writes** to the four offsets:

1. CfgWr `ADDR_OFFSET` ← 32-bit SoC address
2. (write only) CfgWr `DATA_OFFSET` ← write data
3. CfgWr `CMD_OFFSET` ← **0 = read, 1 = write** → launches the ELBI AXI transaction
4. Poll CfgRd `STS_OFFSET` until bit 0 (**done**) = 1 — the read also self-clears it
5. (read only) CfgRd `DATA_OFFSET` ← returns the SoC read data

RTL notes:
- Command decode is **exact-match on the data payload**: `lbc_cii_data == 32'b0` ⇒ read,
  `== 32'b1` ⇒ write. Any other value launches nothing.
- Status register is **3 bits** in the CIC version: `{bresp/rresp[1:0], done}`
  (`elbi_indr_data_mx = ... {29'b0, elbi_indr_sts}`), so the AXI response code is visible —
  FW's host-side backend only consumes bit 0.
- Transactions are fixed **32-bit** (`awsize/arsize = 3'b010`, `wlast=1`), **no bursts**.
- **PF only** — `!lbc_cii_hdr_vfunc_active` is in the hit term, so VF config accesses to the
  same offsets are *not* treated as ELBI.
- Priority & completion:
  ```verilog
  cii_lbc_halt        = elbi_indr_access ? 0 : cii_access ? cii_halt : 0;
  cii_lbc_override_en = elbi_indr_access ? 1 : cii_access ? cfg_cii_lbc_override_en : 0;
  cii_lbc_override_data = elbi_indr_access ? elbi_indr_data_mx : ...;
  ```
  An ELBI access **always** overrides the CDM read data (so the host sees the mailbox value,
  not whatever CDM sits at 0xFF0..0xFFC) and is **never halted** — the config read completes
  immediately; the status-poll loop *is* the handshake. `elbi_indr_access` also suppresses
  `cfg_lbc_cii_set`, so an ELBI access does **not** raise the CII capture interrupt.
- ELBI AXI egress is `pcil_pep2piw_elbi_axi_*` → PIW, via `pep_axi_mstr`/`pep_axi_handler`
  (CDC `muxd_aux_clk_g` → `pcil_clk`, FIU isolation instance `noc_rst_iso_axi_mst_elbi`).

**FW side (host/native-sim backend)** — `fw/infra/interfaces/el_pcie_if/el_pcie_elbi_backend.c`
hardcodes `ELBI_{ADDR,DATA,CMD,STATUS}_REG_OFF = 0xff0/0xff4/0xff8/0xffc`,
`ELBI_STATUS_DONE_MSK = 0x1`, `ELBI_MAX_STS_POLLS = 30`, registered as the **indirect** backend
of the HW-bus model (`EL_HW_BUS_METHOD_INDIRECT`, `EL_HW_BUS_ADDR_NON_CANONICAL`) under
`CONFIG_EL_PCIE_IF_ELBI`. It assumes the CSR reset offsets — relocating them in HW would break
it silently.

---

## 5. What the EP spec intends vs. what A1 implements

EP Spec Rev 2.0.0 §7.10 pseudo-code:

```
if (ELBI_ADDR >= INDIRECT_BASE && INDIRECT_EN)  -> indirect FSM
else if (ELBI_ADDR > DOE_BASE && DOE_EN)        -> DOE mailbox (PSOC SRAM)
else                                             -> capture + interrupt to FW
```
*"Note: base address and enable should come from CSR."*

A1 implements **only the indirect-FSM branch** (and as four exact-match offsets, not a base
+ range). DOE is still a `//TODO - DOE section? see ELBI section in spec (7.10.1)` in
`pep_elbi_handler.v`. The spec also lists the ELBI use-cases: *extend the PCIe capability
linked list in SW* and *handle DOE in the uC*, both of which depend on the
"else → interrupt" branch.

### Legacy `ext_lbc` leftovers in the A1 CSR (caution)

`csr_pep_ctrl_aux` still exposes the legacy LBC path:
- `PEP_CTRL_AUX_EXT_LBC` @ **0x114** — `CS` b0 (HW-written), `ACK` b1 (HW-written),
  `ACCESS_EVENT` b2 (sticky event)
- `PEP_CTRL_AUX_EXT_LBC_DIN` @ **0x118** — 32-bit read data **supplied by FW**

but in `pcie_iip_device_wrap.v` the ack is generated **automatically**:
```verilog
assign lbc_ext_cs_rise_pulse = ~lbc_ext_cs_cs & lbc_ext_cs;
assign ext_lbc_ack_ns = lbc_ext_cs_rise_pulse ? 1'b1 : lbc_ext_cs_fall_pulse ? 1'b0 : ext_lbc_ack;
```
i.e. acked one cycle after chip-select rises, **without waiting for FW**, while
`lbc_ext_addr` / `lbc_ext_dout` are in the explicit unused-signal waiver list in both
`pep_wrap.v` (`// subsys -> iip (no consumer yet)`) and `pcie_iip_device_wrap.v`.
⇒ Even if someone enabled the legacy redirect via `CONFIG_LIMIT_REG` /
`TARGET_ABOVE_CONFIG_LIMIT_REG=ELBI`, A1 would return the **stale** `EXT_LBC_DIN` CSR value and
FW could not service the access in time, and would not know *which* address was hit. Treat the
legacy ELBI path as **not usable on A1**.

---

## Citations

- `DWC_pcie_ctl_ep_databook.pdf` (v6.42a-lca01) — §3.6.1 p.169–172 (CDM vs ELBI split, config
  space layout Fig 3-30/3-31), §3.6.2 Fig 3-33/3-34 p.176–177 (LBC context & switch),
  §3.6.3.2 p.178–180 (Table 3-12, Fig 3-35, footnotes c/d), §3.6.3.3 p.180–182 (CIC/CII,
  Fig 3-36 `CX_CONFIG_INTERCEPT_ENABLE=1`, Fig 3-37 CII timing), §3.6.3.4 p.183–184 (MEM/IO ELBI)
- `DWC_pcie_ctl_reference.pdf` — §3.2.44.44 `MISC_CONTROL_1_OFF` p.3290–3294;
  `SYMBOL_TIMER_FILTER_1_OFF` p.3186 (`CX_FLT_MASK_UR_FUNC_MISMATCH` constraint)
- `PCIe EP Specification Rev 2.0.0.docx` — §7.10 External Local Bus Interface
  (paras 1106–1156: mapping, decode pseudo-code, indirect FSM 0xFF0/4/8/C, DOE, ELBI interrupt,
  CDM access)
- RTL `rtl/pcie/pep_macro/rtl/`:
  - `pep_cic_handler.v` — live CII↔ELBI bridge (offset compare, FSM, override/halt muxing)
  - `pep_elbi_handler.v` — legacy LBC-external ELBI, **not instantiated**
  - `pcie_iip_device_wrap.v` — `pep_cic_handler` instance (l.3446), `ext_lbc_*` CSR wiring
    (l.2932–2940), auto-ack (l.3065–3067), unused-signal waiver (l.3789–3790)
  - `pep_wrap.v` l.2417–2425 — `lbc_ext_*` "no consumer yet"
  - `pep_axi_mstr.v` — ELBI AXI master + `muxd_aux_csr2elbi_term_*`
  - `misc/PCIE_IIP_DEVICE_WRAP_Micro_Architecture.md` §8.2 (CII-to-ELBI bridge), §4.2.7
    (`noc_rst_iso_axi_mst_elbi`)
- Generated CSR headers `fw/build_artifacts/include/{v1,v2/...}/prototype/pep_ctrl_aux_regs.h`
  + `_attr.h` — `CII_CTRL` 0x8 (rst 0xa), `CII_ELBI_INDR_OFFSET_0` 0x10 (rst 0xff4ff0),
  `CII_ELBI_INDR_OFFSET_1` 0x14 (rst 0xffcff8), `EXT_LBC` 0x114, `EXT_LBC_DIN` 0x118,
  `INTR_SPI_CAUSE.CII_ACCESS` b4
- FW: `fw/infra/interfaces/el_pcie_if/el_pcie_elbi_backend.c`;
  `fw/ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c` (`enable_bdf_capture`, `capture_bdf_once`);
  `dwc_ep_ctl_bringup_fields.c` (`cii[]`, `misc_control[]`)

## Related

- [ds-term.md](ds-term.md) — `PF_SW_ELBI_TERM` / `PF_UNCERR_ELBI_TERM_MASK` gate this exact ELBI
  master port (the host trapdoor)
- [axi-term-status-sticky.md](axi-term-status-sticky.md) — `ELBI_TERM_CAP_STATUS` @ PEP_AUX 0x15c
- [rasdp-error-mode-vs-aer-internal.md](rasdp-error-mode-vs-aer-internal.md) — RASDP error mode
  suspends wire access to CDM/ELBI registers
