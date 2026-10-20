# DM Controller Bus/Device Number Status

- **Recorded:** 2026-10-19
- **Status:** Verified from DM databook + generated register headers; mode-specific readback caveat
- **Tags:** dm, pcie, bdf, bus number, device number, requester id, enumeration

## Findings

The DM controller has a `PL_APP_BUS_DEV_NUM_STATUS_OFF` register at DBI offset
`0xB10`. The generated headers identify the device-number field at bits `[7:3]`
and bus-number field at `[15:8]`; it does not include function number.

The DM databook §3.1.1 (v6.42a-lca01, p. 134) says that for a Downstream Port
the application supplies bus/device via `app_bus_num` and `app_dev_num`, and
the status register reflects the application-configured values. For Upstream
Ports, the controller captures bus/device from configuration writes. Therefore
use this status register as the B/D readback for an application-configured
Downstream Port; do not assume the register is authoritative for an enumerated
Upstream/Endpoint port without confirming the specific integration's readback
semantics. The reliable assignment record for an Endpoint remains the upstream
enumerator's PCIe device tree/configuration view. Function number is not present
in this status register (PF0 is function 0).

This is distinct from the C2C `REQ_ID_TO_PEER_IDX_TABLE`/SE CAM: that table
stores requester IDs of peers for inbound routing; it is not the local port's
assigned BDF.

## AUX captured-identity readback (verified 2026-10-20)

Iridium exposes the DWC `cfg_pbus_num` (8 bits), `cfg_pbus_dev_num` (5 bits),
`cfg_bus_master_en`, and `cfg_mem_space_en` outputs through AUX `CFG_INFO_1`.
`dm_wrap.v` connects the controller outputs to `dm_pcie_iip_device_wrap.v`,
which continuously updates the corresponding AUX fields (write enables tied
to one, lines 950-973). Thus AUX provides an integration-confirmed endpoint
bus/device readback path, distinct from the DBI status-register caveat above.

Generated SE v2 1_0_7_1p0_503 header: CFG_INFO_1 is AUX+0x14; bus is
bits 21:14, device 26:22, BME bit 0, MSE bit 5. Function number is not
present. These are current status values, not a persistent enumeration
history record: bus zero with both enables clear does not prove "never
enumerated." Downstream Port identity still follows the application-driven
rule in databook section 3.1.1, page 134.

Sources: `Elmnt-Internal/ip.se.rtl`, `iridium` branch,
`dm_macro/rtl/dm_wrap.v` (controller outputs 845-879; wrapper inputs 1300-1332),
`dm_macro/rtl/dm_pcie_iip_device_wrap.v` (950-973);
SE generated `dm_ctrl_aux_regs.h`, CFG_INFO_1 definitions.
No hardware validation.

## Sources

- `DWC_pcie_ctl_dm_databook.pdf`, §3.1.1 “Bus and Device Number”, p. 134.
- FW generated register header
  `build_artifacts/include/ips/se/v2/1_0_7_1p0_503/prototype/dm_usp_dwc_pcie_usp_regs.h`:
  `DM_USP_DWC_PCIE_USP_DM_USP_PF0_PORT_LOGIC_PL_APP_BUS_DEV_NUM_STATUS_OFF`
  (`0xB10`) and its `RC_DSW_DEV_NUM` / `RC_DSW_BUS_NUM` fields.
- `build_artifacts/include/ips/se/v2/1_0_7_1p0_503/prototype/dm_ctrl_aux_regs.h`:
  application bus/device fields in `DM_CTRL_AUX_APP_CTRL`.
