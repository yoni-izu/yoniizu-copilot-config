# Same-index inter-sled NPA/BDF and BAR-size contract

- **Recorded:** 2026-10-15
- **Status:** Verified from A1 Manageability v0.6 and FW
- **Tags:** same_index, npa, bdf, bar, oemdataload, sled, moduleid

## Findings

A1 Manageability section 6.1.2.3 permits inter-sled communication only between
chips of the same ModuleId. Each chip receives per-device/die NPA and BDF
tables: each NPA entry is a 64-bit destination BAR base, each BDF entry a
16-bit requester identifier. MC and FW transport the appropriate tables;
the external connectivity/table producer owns their content.

FW OEM data load dispatches NPA/BDF sections to `soc_load_npa_table` and
`soc_load_bdf_table`. The SOC provider copies bases/BDFs to SE-owned per-die
buffers, commits on init-bitmap completion, and signals MW readiness.
SE derives hardware NPA entries as `bar >> 38`. These payloads carry no
BAR-size setting; BAR aperture is selected separately from the DTS topology
for switch-attached SU ports.

Changing `bar-size-gb` alone does not update table content, external switch
allocation, or TX/RX selector policy. For the 256-GiB same-index proposal,
see the SU investigation, including the Iridium RTL OR semantics and
per-port versus shared-topology selector caveat:
[SU BAR propagation](../../su-ip/knowledge/same-index-256gb-bar-propagation.md).

## Sources

- `A1 Manageability Specification.docx`, effective final-markup XML:
  section 6.1.2.3 (paragraphs 3652-3686 including table paragraphs);
  same-index ModuleId rule 3665; entry types 3673-3674; data-load mapping 3684-3685.
- `ip.mng.sw.fw/services/el_oob/src/el_oob_oem_dataload.c`: dispatch table 51,
  dispatch call 282.
- `ip.mng.sw.fw/subsystems/soc/soc_providers.c`: staging 1104,
  commit/readiness 1155.
- `ip.se.sw.fw/drivers/scale_engine/el_se_lut.c`: HW entry derivation 295.
- `ip.se.sw.fw/drivers/scale_engine/el_se.c`: BAR policy 249,
  dynamic BAR rebasing 1754.
- Source checkout: `/home/yoniizu/projects/iridium/a1_power_on_0p5p152/fw`.
