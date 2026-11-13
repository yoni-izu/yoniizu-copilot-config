# DM FBER control and counter semantics

**Question:** What do the DM controller's FLIT-logging FBER control fields select, and what does the default granularity count?

**Answer:** Control bit 0 enables measurement, bit 1 is the counter-clear strobe, and granularity bits [3:2] set the counted error class. Granularity `00` counts all FEC-corrected bit errors in valid flits. Per-lane corrected-error counters and invalid-flit counts are separate status values.

## Facts

- The FBER control register documents enable at bit 0, clear at bit 1, and granularity at bits [3:2].
- Reset/default granularity `00` counts all FEC-corrected bit errors in valid flits.
- The FBER status reports per-lane corrected errors separately from invalid-flit count.
- These semantics were located in the historical local `DWC_pcie_ctl_reference.pdf`, pages 3659–3664. Its revision was not verified against the current integration, so treat those pages as a historical locator, not a confirmed current source.
- The FW implementation's absolute FBER offsets were not validated against a verified current register map.

Recorded 2026-11-12.
