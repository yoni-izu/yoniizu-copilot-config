# Asgard S1 nLUT 16-JBOG sheet -> A1 same_index DTS mapping

Source: /home/yoniizu/Asgard_S1_nLUT_16JBOG.xlsx (single sheet "nLUT 16 JBOG", 128 rows = 8 chips x 16 entries).
Verified 2026-10-15: all 128 rows satisfy the header formula exactly:
  DVA = NPA = (JBOG<<41)|(chip<<38), mask 0x3F_FFFFFFFF (256G), entry index = JBOG (0..15),
  BDF = (3 + 2*JBOG + chip%2) << 8, JBOG = rack*8 + sled, same for Die0..3, entries 16..63 unused.

Mapping to FW (ip.se el_se_lut.c, proj_infra a1_hsi.dtsi):
- Index: same_index nlut-select-bits <41..46> -> index = addr[46:41] = JBOG. Matches. HW is direct-indexed,
  not base/mask match; "DVA match base/mask" columns are descriptive only.
- One shadow table per die; index i drives both nLUT NPA entry and CAM peer slot (el_se_lut.c ~288/316) -> matches sheet rows.
- NPA entry = NPA>>38 = (JBOG<<3)|chip. TX fold NPA[40:38]=table[2:0]|DPA[40:38] with DPA chip=own chip => a
  chip-agnostic table with chip bits 0 is equivalent to the per-chip sheet. Current DTS fallback c2c_npa_table_same_index
  uses chip bits =1 (entry=(tray<<3)|1) -> would fold even chips onto odd chips; inconsistent with sheet.
- Bit 57 (DEVICE_MEM_BASE_ADDR=0x0200_0000_0000_0000, entry |0x80000): halves table includes it, same_index table and
  the sheet do not. Open question which is correct on the wire / what BMC OemDataLoad sends.
- BDF depends on chip%2 -> not expressible by one static DTS CAM table (current default = even-chip pattern, 8 entries).
  Needs BMC OemDataLoad BDF table or a parity-aware FW mechanism.
- REMOTE_REQ_ID self index = el_se_lut_topology_get_table_index() = tray_idx*1; tray_idx from el_mng_get_tray_id(),
  which returns 0 on non-POSIX (ip/mng/include/el_mng_device_info.h:54) and has no rack term -> self BDF wrong for
  JBOG != 0 and for rack 1 unless fixed.
- Sheet BAR bits [40:38] == chip index -> satisfies the 256GB same_index fold precondition (see same-index-256gb-bar-propagation.md).
