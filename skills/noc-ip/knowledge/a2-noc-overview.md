# A2 NoC — Overview, Fabrics, Address Map (incl. INC/GPU)

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Atlantic2 NoC Specification Rev 1.4.0 / 1.2.0)
- **RTL branch/tag:** n/a (spec); RTL delta TODO (`ip.noc.rtl` iridium..main)
- **Tags:** noc, fabric, address map, npa, dpa, fiu, inc, gpu, afd, prefill, mem fabric, mrtr,
  prtr, virtual channels, mng, ctrl, dbg, trc, ect, ts, axuser, routing, device coordinates,
  tp8, tp16, region selection, dieid, cfgr, rr

## Scope / Question
A2 NoC/fabric structure, the fabrics, and the address-map (including the A2-new INC and GPU
spaces). Owner: Maxim Leshin.

## Findings
- **Fabrics**: one **MEM** data fabric (Plane Selection, **Middle Router mRTR**, **PCIe Router
  pRTR**, Virtual Channels, Data Merging, VCore connectivity) + sideband fabrics **MNG**
  (management; hashing function; VME decode), **CTRL** (control), **DBG** (debug), **TRC**
  (trace), **ECT** (breakpoint), **TS** (timestamp). Endpoints attach via the **FIU**.
- **Address spaces**: Device, Host, **NPA** (Network Physical Address), Management, Control,
  Debug. **DPA** widened to 46-bit (rev 1.1.0). LMEM 256 MB; D2D window 256 KB; ASID 5-bit.
- **NEW A2 address spaces**:
  - **INC Device Address space** — 4 regions of **2^56 each (one per die)**. INC = in-network
    compute / switch device on the Switch Trays.
  - **Remote GPU Device Address Space** — region of **2^62** distributed between GPU devices;
    GPU memory spanned by 62 bits, 47-bit offset per device; upper bits **[63:62]** separate
    GPU-memory from A2 Device-Memory (has an "A2/GPU translation" pseudocode).
- **GPU interop over scale-up**: "A2 introduces the ability to communicate with remote GPU
  through the scale-up fabric to support **AFD and prefill**"; GPU devices connect to Switch
  Trays for AFD; collectives (**AllGather/AllReduce**) can be received from a remote device, the
  **INC switch**, or a **remote GPU**.
- **Routing**: device coordinates (ChanID moved left in later revs), inner/outer die routing, a
  documented **NoC routing algorithm**; **region selection adds DieID** comparison (besides X/Y).
- **FIU** capabilities: address decode/hashing, security config, **memory isolation**, **traffic
  rate limiting** (RL ranges), debug/telemetry counters (telemetry counters later removed from
  MNG/CTRL), **RAS**, **reset isolation**, credit handling, **channel selection** (select D2D
  instance by requestor id), **limit on outstanding transactions** (rev Nov-2025), OAM
  connectivity. Architect asks: FIU address-manip granularity **16KB → 4KB**.
- **AxUSER / AxUESR**: PF/VF reduced to 1b + **DestID** added on request; **ASID 5-bit**; **MX
  enable bit** on AxUSER; **MXFP** carried on the RUSER bus (A2 vector datatypes).
- **Scale-up connectivity** (fabric level): rail connectivity; scale-up **redundant I/F moved to
  Die[3]**; inter-tray scale-up combined. TP8/TP16 supported.
- **CCE descriptor** (via MEM fabric) evolving in A2: added Distribute bit, reserved bits,
  `Last` field, reduced tag on AWUSER to 7b, CCE.DUP→GMEM.

## A2 vs A1 (Iridium) delta
- **GPU/INC integration is the defining A2 fabric change** (new INC + remote-GPU address spaces,
  collectives from GPU/INC) — did not exist in A1. Plus address-map growth (512 MB device config
  per architect init), DieID region selection, finer FIU granularity, DPA 46b, and CCE-descriptor
  changes. Fabric taxonomy (MEM/MNG/CTRL/DBG/TRC/ECT/TS + FIU) carried from Iridium.

## Sources
- Doc: `~/docs/NOC/Atlantic2 NoC Specification Rev 1.4.0.docx` (2724 paras, 39 tables; owner
  Maxim Leshin) and `~/docs/A2/NOC/...Rev 1.2.0.docx`. Address Map: INC (~para 628), remote GPU
  (~629, 856-859, 660-664), GPU/AFD (417, 856), A2/GPU translation (947), collectives (2342-2347).
  Revision History table (evolution through Dec 2025).
- Cross-ref (project scope): `atlantic2-prj` → a2-noc.md, a2-scaleup-arch.md.
- **RTL (confirmed)**: `ip.noc.rtl` `iridium...main` (535 commits) — new **`AXI_UC_GPU`/`AXI_UC_INC`**
  channels; **VSP** 16G/32G dual-vcore-group; **Aligner/CCE-table** work (CG0 ranks, multicast
  distribute, credits/chip); CCE-desc detect via `addr.noc.op_type`; MRTR module_id encoding. See
  `atlantic2-prj` → a2-rtl-analysis.md.
