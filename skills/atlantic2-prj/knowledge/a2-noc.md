# A2 (Atlantic2) — NoC / Fabric + Address Map

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Atlantic2 NoC Specification Rev 1.2.0)
- **A2-vs-A1 delta:** New **INC** (switch) + **remote GPU** address spaces; **GPU interop over
  scale-up for AFD & prefill** (disaggregated LLM inference); TP8/TP16; 512 MB device-config
  space; DieID-based region selection; finer FIU address manip.
- **Tags:** noc, fabric, address map, npa, fiu, inc, gpu, afd, prefill, remote device, mem fabric,
  mrtr, prtr, mng, ctrl, dbg, trc, ect, ts, redundancy, device coordinates, tp8, tp16

## Scope / Question
A2 NoC / fabric structure and the A2-specific address-map additions.

## Findings (A2)
- **A2 NoC spec** = `~/docs/A2/NOC/Atlantic2 NoC Specification Rev 1.2.0.docx` (2691 paras,
  38 tables). Same fabric family as Iridium: Address Map, Redundancy (inner-die + platform),
  Device Coordinates, Scale-Up Connectivity (Rail), Device Routing (inner/outer die), **MEM
  Fabric** (Plane Selection, Middle Router mRTR, PCIe Router pRTR, Virtual Channels, Data
  Merging, VCore connectivity), plus **MNG / CTRL / DBG / TRC / ECT(breakpoint) / TS(timestamp)**
  fabrics, AxUSER, and **FIU** (address decode/hashing, security config, memory isolation,
  rate limiting, RAS, reset isolation, credit handling, channel selection).
- **Address spaces**: Device, Host, **NPA** (Network Physical Address), Management, Control, Debug.
- **NEW A2 address spaces (the big delta):**
  - **INC Device Address space** — 4 regions of **2^56 each (one region per die)**. INC = the
    in-network / switch device on the Switch Trays.
  - **Remote GPU Device Address Space** — a region of **2^62 distributed between GPU devices**;
    GPU memory spanned by 62 bits, 47-bit offset per device; upper 2 bits **[63:62]** separate
    GPU-memory from A2 Device-Memory space (has an "A2/GPU translation" pseudocode).
  - Remote-device memory distinguished by same-compute-tray vs different-tray.
- **GPU interop over scale-up**: "A2 introduces the ability to communicate with remote GPU
  through the scale-up fabric to support **AFD and prefill**" (para 856). **GPU devices connect
  to the Switch Trays for AFD support** (para 417). Collectives (**AllGather / AllReduce**) can be
  received from a **remote device**, the **INC switch**, or a **remote GPU** (para 2342-2347).
- Ties to architect NoC initiatives (a2-architect-initiatives.md): **TP8/TP16 support**, **Device
  Config addr space → 512 MB**, add **INC + Remote Device** address spaces, **DieID** in region
  selection (besides X/Y), FIU addr manip **16KB → 4KB** granularity, RR addr space 25:0→27:0,
  CfgR covers SPI-flash + STM trace, many isolation/termination bug fixes.

## A2 vs A1 (Iridium) delta
- **GPU/INC integration is the defining A2 fabric change**: A2 talks to **GPUs and an INC switch**
  over the scale-up fabric to enable **disaggregated LLM inference (AFD + prefill)** — new INC and
  remote-GPU address spaces + collective ops from GPU/INC. A1/Iridium had no GPU/INC address space.
- Address-map growth (512 MB device config, DieID region selection) and finer FIU granularity;
  otherwise the fabric taxonomy (MEM/MNG/CTRL/DBG/TRC/ECT/TS + FIU) is carried from Iridium.

## Sources
- Doc: `~/docs/A2/NOC/Atlantic2 NoC Specification Rev 1.2.0.docx` — Address Map (INC 628, remote
  GPU 629/856-859, 660-664), GPU/AFD (417, 856), A2/GPU translation (947), collectives from
  remote/INC/GPU (2342-2347). H1/H2 fabric taxonomy as listed above.
- Cross-ref: a2-architect-initiatives.md (IPNOCSW-115..138), a2-scaleup-c2c.md.
- **RTL (confirmed)**: `ip.noc.rtl` `iridium...main` — `AXI_UC_GPU`/`AXI_UC_INC` channels, VSP
  16G/32G vcore groups, Aligner/CCE-table work. See a2-rtl-analysis.md.
