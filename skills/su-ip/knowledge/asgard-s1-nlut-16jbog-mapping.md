# Asgard S1 nLUT 16-JBOG sheet -> A1 same_index DTS mapping

Source: /home/yoniizu/Asgard_S1_nLUT_16JBOG.xlsx (single sheet "nLUT 16 JBOG", 128 rows = 8 chips x 16 entries).
Verified 2026-10-15: all 128 rows satisfy the header formula exactly:
  DVA = NPA = (JBOG<<41)|(chip<<38), mask 0x3F_FFFFFFFF (256G), entry index = JBOG (0..15),
  BDF = (3 + 2*JBOG + chip%2) << 8, JBOG = rack*8 + sled, same for Die0..3, entries 16..63 unused.

Mapping to FW (ip.se el_se_lut.c, proj_infra a1_hsi.dtsi):
- Index: same_index nlut-select-bits <41..46> -> index = addr[46:41] = JBOG. Matches. HW is direct-indexed,
  not base/mask match; "DVA match base/mask" columns are descriptive only.
- One shadow table per die; index i drives both nLUT NPA entry and CAM peer slot (el_se_lut.c ~288/316) -> matches sheet rows.
- NPA entry = NPA>>38 = (JBOG<<3)|chip. Current A1 DTS fallback has one table per chip
  (`c2c_npa_table_same_index_chip0` through `chip7`), with entry J = `(J << 3) | chip`;
  the topology references the corresponding table for each device. This supersedes the
  earlier single-table fallback note below.
- Bit 57 (DEVICE_MEM_BASE_ADDR=0x0200_0000_0000_0000, entry |0x80000): halves table includes it, same_index table and
  the sheet do not. Open question which is correct on the wire / what BMC OemDataLoad sends.
- BDF depends on chip%2 -> not expressible by one static DTS CAM table (current default = even-chip pattern, 8 entries).
  Needs BMC OemDataLoad BDF table or a parity-aware FW mechanism.
- REMOTE_REQ_ID self index = el_se_lut_topology_get_table_index() = tray_idx*1; tray_idx from el_mng_get_tray_id(),
  which returns 0 on non-POSIX (ip/mng/include/el_mng_device_info.h:54) and has no rack term -> self BDF wrong for
  JBOG != 0 and for rack 1 unless fixed.
- Sheet BAR bits [40:38] == chip index -> satisfies the 256GB same_index fold precondition (see same-index-256gb-bar-propagation.md).

Current source locations: `proj_infra/soc/dts/a1/a1_hsi.dtsi` defines the
`c2c_topo_same_index` table list, the eight Asgard per-chip tables, and the Ocean legacy
tables; `ip/se/drivers/scale_engine/el_se.c` compiles them as boot defaults. The A1
connection-scheme strap now selects the physical connection descriptors and the scheme's
NPA/CAM table references together. O, R, and ELS1 use Ocean defaults; A uses the Asgard
per-chip NPA and parity CAM tables; R then applies its explicit CAM override. TEST omits
scheme tables and keeps the topology fallback. Middleware-provided LUT BAR/BDF data still
takes precedence when supplied before hardware programming.

Before the Aqua fallback change (`proj_infra` commit `e736afa6`, parent state), the
same-index DTS fallback was a synthetic 64-entry NPA table with entry for tray J equal
to `0x80000 + (J << 3)` (repeated through the 64 slots). The `0x80000` set the address
bit-57 base; chip bits were supplied by the same-index selector fold. Its generic 64-entry
CAM mapped peer N to `BDF(N+1, 0, 0)` (bus numbers 1 through 64). Aqua's 2026-10-15 change
replaced those defaults with an 8-entry NPA table `(J << 3) | 1` and CAM `BDF(2J+3,0,0)`;
the later Asgard sheet change (`23e95f65`) replaced that with per-chip NPA and parity CAM
tables. Thus pre-Aqua Ocean fallback did not use the Aqua bus/NPA assignments.
Those historical Ocean values are restored as explicit per-scheme DTS defaults, so the
current shared `same_index` topology no longer forces Aqua's table values onto Ocean.
