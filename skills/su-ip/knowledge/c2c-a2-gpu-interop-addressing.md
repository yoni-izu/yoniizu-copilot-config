# A2 GPU ↔ A2 interop: transport and address awareness

- **Recorded:** 2026-11-15
- **Status:** Spec-verified (A2 C2C Markdown, cover Rev2.1.1); RTL deltas from prior Neo notes; GPU-side BAR discovery is NOT specified
- **Tags:** a2, gpu, pcie-compliant mode, inter-sled, bar, 48-bit bar, npa, dva, device translation table, enumeration, switch, mc table update, producer consumer

## Findings

- GPU traffic uses **PCIe-Compliant Mode** only (standard MWr/MRd TLPs, standard credits, no AXI sideband). Inter-sled = compliant only; intra-sled A2↔A2 may be optimized. §7, §7.1.
- Topology: GPU and A2 sit behind a **scale-up PCIe switch** whose embedded CPU enumerates all devices and assigns BARs (§4). P2P/switchless: RP enumerates EP, or FW programs EP config via DBI (preferred) (§7.3.7).
- **Inbound (GPU→A2):** each inter-sled SU port exposes one **48-bit BAR** covering the whole destination sled: ChipID[45:42] (TP16, width topology-dependent), DieID[41:40], OpType[39:36], Offset[35:0] (§7.1.6.1). C2C Rx extracts Chip/Die, rebuilds the native A2 address, sets SledID=0xFF (local) (§7.1.6.5). So the GPU only needs the BAR base of an A2 port plus the bit layout.
- **Outbound (A2→GPU):** DVA[63:62]=2'b10 marks GPU; DVA[61:54] indexes a 256-entry Device Translation Table {NPA base, mask, dest BDF}; 0xFF = local sled. GPU out = base[63:48] | in & mask, low bits pass through (§7.1.6.4, .4.2, pseudo-code .4.3.4). That table holds the GPU's BAR as seen on the fabric — programmed by A2 SW.
- Reserved inbound windows (Load/Store OpType 0, base CSR): E2E credit, EOC, **GPU MC table update** (BaseAddr+9MB, 256×512B, Addr[16:9]=entry), EOB (§7.3.2.13, §7.3.3.3).
- Producer/consumer GPU↔A2 ordering: multi-plane flag sync, flags classified at Rx (§7.1.5).
- **History of the A2→GPU translation:** in-doc revision log: 11/5/2026 "add non A2 devices" (GPU translation introduced), 7/6/2026 non-A2 devices raised 32→128, 22/6 A2 BAR space updated, 06/08 OpType moved to Addr[39:36]. The Markdown has a single git commit (0cafe877, 2026-09-02 import), so it carries no finer history. The candidate DOCX (`raw-specs/PCIe_C2C_Rev_2.2.0.docx`) has **unaccepted tracked changes** by Ajay Upadhyaya: 2026-07-23 split into 3 tables (A2 2048 entries idx [61:51], GPU 1024 idx [61:52], INC 8 idx [53:51]; entry 0x7FF = local); 2026-08-06 GPU pseudo-code widened [63:32]→[63:48] plus mask on [47:0]. The Markdown keeps the older 256-entry DVA[61:54] text, and the DOCX is internally inconsistent: its prose still says index DVA[61:54] and its GPU formula still uses [63:32]. RTL (Neo) has one shared 2048-entry A2/GPU table, which matches neither.
- **Gap:** the spec does not define how the GPU/its software learns A2 BAR bases or how A2 learns GPU BARs; implied to come from switch enumeration + system/fabric software. A1 analogue: BMC OEM data-load supplied peer BAR bases/BDFs (see same-index-256gb-bar-propagation.md). A2 mechanism unverified.
- **RTL deltas (Neo, prior notes):** one shared 2048-entry A2/GPU NPA table (not 256 / separate GPU table); DM BAR reset mask 38-bit vs spec 48-bit; NPA table MNG reachability risk. See c2c-a2-npa-table-address-translation.md.

## Sources

- `Elmnt-Internal/project.atlantic2.hwarch.specs@15bd28840a84cc265db7b8c3de745775be800b2d`, `specs/pcie-c2c-Rev2.1.1/pcie-c2c.md`: §4 (L255-290), §7/§7.1 (L560-640), §7.1.5 (L868), §7.1.6.1-7.1.6.5 (L1145-1600), §7.3.2.13 (L3370), §7.3.3.3 (L3560), §7.3.7 (L3848).
