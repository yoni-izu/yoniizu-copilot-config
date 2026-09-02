# A2 (Atlantic2) vs A1 (Iridium) — Executive Delta Summary

- **Recorded:** 2026-08-30
- **Status:** Verified from spec + Jira + Confluence (analysis roll-up)
- **Tags:** a2, atlantic2, a1, iridium, delta, summary, changes, gen7, ucie, d2d, pcie, scale-up,
  c2c, gpu, inc, afd, prefill, dual-mode, firmware, planning

## What A2 is (one paragraph)
A2 (Atlantic2) is Element Labs' **next** multi-die LLM **inference** accelerator, successor to
**A1 (Iridium)**. **4 compute dies** (each 1 GB SRAM + VME + 32 RISC-V VCores + CCE) on a
**bidirectional ring**; chips grouped into **sleds** behind **centralized switches**. **One shared
PCIe EP** to the host. Three ARM **Cortex-M7** FW agents (Boot&Reset, Management, Power/Thermal).
Much of the non-connectivity architecture is **carried from Iridium** (the A2 Overview doc is
literally derived from the Iridium doc — figure captions still say "Iridium").

## The headline A2-vs-A1 deltas (what actually changed)

| Area | A1 (Iridium) | A2 (Atlantic2) |
|------|--------------|----------------|
| **PCIe EP** | Gen6-class | **Gen7 ×4** (128 GT/s PAM4), 64 GB/s/dir, **Enhanced custom FEC** + increased voltage swing, 1 µs-RTT credits, 256B FLIT |
| **Scale-Up / C2C** | EP-oriented, 1st gen | **2nd-gen C2C**; **PCIe-Compliant Mode** on a **Dual-Mode (RP/EP)** controller → **3rd-party PCIe / GPU interop**; Gen7; FLIT+NFM; Base Spec 7.0; Switched+P2P; Dragonfly; ordering models + aggregation buffer; lane failover; multi-link |
| **D2D** | UCIe, **5nm** PHY | **Kept AS-IS** for A2 (architect Maxim Leshin) — spec Rev2==Rev1, no D2D initiatives; still 12× UCIe 2.0, 768 GB/s. FW updated **only if the new AlphaWave (AW) IP needs a different config**. (D2D spec unchanged A1→A2) |
| **NoC / Fabric** | Device/Host/Mgmt/Ctrl/Dbg spaces | **+ INC (switch) address space** (4× 2^56, per die) **+ Remote GPU space** (2^62); **GPU interop over scale-up for AFD + prefill** (disaggregated LLM inference); TP8/TP16; 512 MB device-config; DieID region selection; finer (4KB) FIU addr manip |
| **Compute** | VME/VCore/CCE | Same taxonomy; CCE evolving (L1-Chip/L2-Die/L3-Sled ordering, redundant compute) |

## The biggest architectural theme
**A2 opens up to GPUs.** Via the **PCIe-Compliant scale-up mode** + new **INC** and **remote-GPU**
NoC address spaces, A2 can do collectives (AllGather/AllReduce) with **GPUs and an INC switch**
over the scale-up fabric — targeting **disaggregated LLM inference (AFD + prefill)**. This is the
single largest new capability vs Iridium.

> **Deep dive (A2_Arch_Enhancments_v1.3):** the scale-up domain is a unified **16K-Rank** fabric
> mixing **A2 (experts, MoE) + GPUs (workers/HBM devices, attention)** for **disaggregated MoE
> serving**. Key HW: **many-to-many (M2M)** worker→expert dataflow (data-then-flag, RO=1 data +
> ordered flag), **16 flag-FIFOs** + **MRT (Memory Region Tracking)** notifying VCores;
> **In-Network Compute (INC)** collectives on the scale-up switch (AllReduce/ReduceScatter/
> AllGather, managed by **Atlantic CommLib** per **commGroup**); **A2↔HBM vLLM resource
> negotiation**; **dynamic multicast** via a 128 KB BAR (256 entries, Scorpio-3-style). See
> `a2-scaleup-arch.md`.

## HSI FW planning status (as of 2026-08-30)
- **Nascent.** 50 HSI tickets tagged `ASIC Project == Atlantic2`: 5 Story, 1 Epic (CI infra),
  44 Attribute — **all Backlog**, overwhelmingly **PCIe / Palladium ("PLDM") emulation
  enablement**. **No HSI initiatives/epics** structured yet (unlike Iridium's full hierarchy).
- Architect A2 HW-initiatives (Confluence 1362952569) are mostly **SE/C2C, PCIe, NoC** items.
  User's expectation was "likely no FW work", but **many have real FW work** (e.g. FW reads MPS
  and configures controller; FW handles PME_TO_Ack low-power state moves; PTM needs SW). A few are
  explicitly HW-internal (e.g. Flag aggregation).

## Open follow-ups (not yet done — need repo/doc access)
- **RTL diffs**: DONE for the HSI IPs — see `a2-rtl-analysis.md` (A1=`iridium`, A2=`main`;
  `gh api .../compare/iridium...main`). Confirmed Gen7 (EP `element_labs_ep16sx4_gen7` + SU DM
  `element_labs_dm16sx2_gen7`), EFEC, C2C aggregators, **3nm PHY on the SU DMP**, D2D PHY stubs,
  NoC `AXI_UC_GPU`/`AXI_UC_INC` channels. Still TODO: `ip.psoc.rtl` (544) + `ip.infra.rtl` (529).
- **N3 (3nm) D2D PHY datasheet** — not yet in `~/docs/D2D/`; add + analyze when available.
- **PSoC Rev2.0.1** — analyzed → see `psoc-ip` skill (3× Cortex-M7 MuC/PTuC/RuC + TuC, no PCIe uC;
  new crypto SHA-384/256/512 + ECC P-384; HSM = Caliptra). RTL `ip.psoc.rtl` (544) still TODO.
- **Compression Specification** (referenced by D2D Rev2) — not yet located.

## Sources
- a2-overview.md, a2-jira-landscape.md, a2-architect-initiatives.md, a2-d2d.md, a2-pcie-ep.md,
  a2-scaleup-c2c.md, a2-noc.md (this knowledge base). Docs under `~/docs/A2/`, `~/docs/D2D/`,
  `~/docs/PCIe EP/`, `~/docs/C2C/`. Jira cloudId c155e965-159d-4564-a0a0-16eb85204b7f.
  Confluence page 1362952569.
