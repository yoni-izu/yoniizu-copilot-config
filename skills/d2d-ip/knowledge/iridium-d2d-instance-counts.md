# Iridium per-die D2D instance counts (horizontal vs vertical)

- **Recorded:** 2026-06-12
- **Status:** Verified from RTL/address-map (FW DTS) + spec
- **Tags:** iridium, hd2d, vd2d, horizontal, vertical, instance count, topology, 2x2 grid, lanes, bandwidth, routing, cce, all-gather

## Scope / Question
"Why does the horizontal side of the die have more D2D blocks than the vertical?"
(Asked as horizontal=4 / vertical=2 — that premise is **inverted**.)

## Findings
- **The premise is backwards.** The *vertical* axis has more D2D bandwidth/instances than horizontal, not the reverse.
- **Physical/active per die (ground truth from FW DTS):** **2 hD2D + 4 vD2D** active.
  - The register/address map reserves **4 hD2D slots** (2 west + 2 east). Only one side is
    bonded/active per die based on its position in the 2x2 die grid:
    - Die[0]/Die[3]: one horizontal side active; Die[1]/Die[2]: the other side
      (PRM says Die[0/3]=west, Die[1/2]=east; SoC spec states it the opposite way — doc
      inconsistency, but the rule is "only one horizontal side active per die").
    - DTS confirms: D0 → HD2D_2,3 ; D1 → HD2D_0,1 ; D2 → HD2D_0,1 ; D3 → HD2D_2,3.
    - vD2D: all of VD2D_0..3 active on every die.
- **Per-plane view (4 orthogonal planes per die):** each plane gets 1 vertical D2D (32 lanes)
  + 1 horizontal D2D (16 lanes, only the active side connected). NoC spec para 271–274.
- **Lane-width asymmetry is the real driver:** vertical D2D uses full **32 lanes**; horizontal
  hD2D uses only **16 lanes**. So even where instance counts look similar, vertical carries 2x.
- **Bandwidth per die:** vertical ≈ **512 GB/s** (4×128) vs horizontal ≈ **256 GB/s**
  (4×128/2, only 16 of 32 bits used per hD2D). Each full D2D instance = 32b@32Gbps = 128 GB/s.
- **Root cause = routing + collectives:** inner-die routing is **X-first then Y** (requests X→Y,
  responses Y→X). Diagonal traffic goes horizontal then vertical, so the **Y/vertical axis is the
  aggregation/convergence axis** for collective ops (All-Gather / All-Reduce via CCE L1 across the
  4 dies). The all-gather BW analysis requires **vertical BW = 2× horizontal BW**
  (horizontal req 192 GB/s vs vertical req 2×192 GB/s).
- **Note on spec inconsistency:** §~270–291 frames it as 4 vertical / 4 horizontal instances per
  die (one per plane); §~860–875 frames it as "8x vertical / 4x horizontal." Both agree vertical
  has ~2x the horizontal capacity; the DTS (active instances) shows 4 vD2D + 2 hD2D per die.

## Sources
- RTL/address map → FW DTS: `projects/iridium/main/fw/proj_infra/soc/dts/a1_hsi.dtsi`
  (`elmnt,d2d-wrapper` nodes: per die 2× `*_HD2D_*` + 4× `*_VD2D_*`), and
  `proj_infra/soc/dts/a1_fiu.dtsi` (HD2D_0..3 / VD2D_0..3 FIU master bases).
  DTS is generated from the `ip-d2d-sw-csr-ci` CSR/address-map artifact.
- Spec: `Iridium NoC Specification.docx` — para 215–219 (topology, X-then-Y routing),
  271–274 (per-plane: vertical 32 lanes / horizontal 16 lanes, one side active),
  289–291 (BW: vert 512 vs horiz 256 GB/s), 855–875 (routing + all-gather BW: vert = 2× horiz).
- Spec: `Iridium PRM.docx` — para 987–1000 (D2D isolation: "Two west hD2D, Two east hD2D";
  Die[0/3] west, Die[1/2] east; `PSOC_MISC_POR.HD2D_ISO` bits).
- Spec: `Iridium SoC spec.docx` — para 192 (hD2D west/east active by die position).
