# A2 (Atlantic2) — What It Is + High-Level Feature Set

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Atlantic2 Overview.docx)
- **A2-vs-A1 delta:** D2D → UCIe 2.0 (12x); PCIe EP → **Gen7**; Scale-up → **PCIe Gen7 / PCIe
  "compliant" (dual-mode) mode**; otherwise a delta on the Iridium (A1) architecture (the
  overview doc is derived from Iridium — several figure captions still read "Iridium").
- **RTL branch/tag:** n/a (spec)
- **Tags:** a2, atlantic2, overview, what is a2, feature set, dies, ring, sled, vme, vcore, cce,
  ucie, pcie gen7, scale-up, firmware agents, cortex-m7

## Scope / Question
Top-level description of A2: what the chip is, its structure, and the headline feature set /
sub-systems. Foundation for the whole `atlantic2-prj` skill.

## Findings (A2)

**What A2 is**
- **A2 = inference chip for LLMs** (Large Language Models).
- **Multi-die processor: 4 compute dies**, each with its **own SRAM**. Compute dies are
  interconnected within the chip by a **high-speed bidirectional ring**.
- Multiple A2 chips are grouped into **sleds** and connected to **centralized switches** to form
  the full system.
- Each die has a **VME** (Vector Matrix Engine), a compute cluster, plus **D2D** and **scale-up**
  connectivity.
- Each compute die includes a **PCIe endpoint**, but **only ONE endpoint connects to the host**,
  shared among the dies.

**Compute (per die)**
- **VME**: 4K SRAMs × 32B @ 0.65 GHz = **83.2 TB/s**. Data types: **BF16, MXFP16, MXFP8, MXFP6,
  MXFP4**. Perf: BF16 **166.4 TFLOPs**, FP8 332.8, NVFP4 665.6 (per die).
- **SRAM**: 1 GB/die (4K mem × 8K row × 32B), 83.2 TB/s.
- **32× RISC-V VCores/die** running topology kernels.
- **Scheduler**: manages DMA collective engine + VCores; stream API; sync-object low-latency sync.
- **CCE** (Collective Communication Engine), **DMA**.

**Connectivity**
- **D2D**: **12× UCIe 2.0 Standard Package**, BW/dir = 12 × 16b @ 32 Gbps = **768 GB/s**.
  Compression referenced ("Compression Specification.docx"). Spec: D2D Rev 2.0.0.
- **PCIe EP**: **Gen7 ×4**, 64 GB/s/dir. Single EP. Enhanced (custom) FEC, increased voltage
  swing, fine-tuned credits for 1 µs round-trip. Spec: PCIe EP Rev 2.0.0.
- **Scale-up**: **based on PCIe Gen7**. 16 × 2 lanes @ 128 Gbps per die. Topologies: **TP8**
  (7×4 intra-tray P2P + 1×4 outer-tray via PCIe scale-up switch) and **TP16** (15×2 intra + 1×2
  outer). PCIe **DP (dual-mode)** capabilities. Enhanced FEC, increased voltage swing. Spec:
  PCIe C2C Rev 2.0.0 (newest on disk: Rev 2.1.1).
- **NoC**: multiple non-intrusive low-latency fabrics. Spec: A2 NoC Rev 1.2.0.

**Platform / RAS / Security**
- **Secure boot**: ECC-384 with measurement using **external TPM**.
- **Power/thermal**: dedicated uC + HW accel, **PID** loop; DVFS; AVSBus/SVID/SPI power read.
- **Manageability**: PLDM over MCTP via I2C/I3C/SPI/PCIe; **dedicated PCIe Gen7×4 port**; IB
  support (EL-SMI, EL-ML); optional SPDM. PSoC spec: Rev 2.0.0.
- **Trace**: ARM CoreSight v3 (processor trace + HW module trace: BMON, SPMU, HW events).
- **RAS**: control/data checksum on major buses, **poison & viral**, reliable scale-up network
  (link resiliency + failover), ECC on all memories.
- **MMU**: maps host memory, configurable page sizes (4KB/2MB/1GB).

**Firmware Agents (3 micro-controllers, all ARM Cortex-M7)**
1. **Boot & Reset uC** — secure boot (image load/auth/recovery), signing-key mgmt/revocation/
   anti-rollback, device security init, attestation & measurement (run-time, SPDM responder, TPM
   storage), eFuse lifecycle (key revocation, SVN, EOL), debug-token handling, FW update +
   hitless FW update, security patches, memory BIST/BISR, error injection, reset handling,
   security access protection, thermal sensor config, FW loading.
2. **Management uC** — device init, BMC comms over PLDM (monitoring & control), platform comms
   (PWM/RPM/I2C), error handling & RAS, debug/trace/logging, interrupt handling, **SLM (Silicon
   Lifecycle Management)**, in-field memory/logical BIST.
3. **Power/Thermal Loop uC** — maintain power/thermal loop, PID control, on/off-die thermal
   sensors, card power read (AVSBus/SVID/SPI), throttle device performance, DVFS.

## A2 vs A1 (Iridium) delta
- **D2D**: A2 uses **UCIe 2.0** (12×). New PHY IP versions per user: a 5nm PHY (A1-family, maybe
  minor logic changes) + a **brand-new 3nm** PHY. (confirm PHY details in d2d-ip.)
- **PCIe EP**: **Gen7** (A2) — headline upgrade. Enhanced custom FEC + increased voltage swing.
- **Scale-up/C2C**: **PCIe Gen7** based; adds a PCIe **"Compliant" mode** (per user) = the DP
  dual-mode capabilities noted here.
- Broader architecture (4 dies, ring, sled, VME/VCore/CCE, 3× Cortex-M7 agents, security/RAS)
  appears **carried from Iridium** — the overview is literally derived from the Iridium doc
  (figure captions still say "Iridium"). Treat non-connectivity items as **A1 baseline** until
  confirmed against Rev2 IP specs / RTL diffs.

## Sources
- Doc: `~/docs/A2/Atlantic2 Overview.docx` — "A2 Overview" (para 90-94), "A2 Feature Set"
  (para 108-169), "PCIe Sub-System" (198-212), "Scale-Up Sub-System" (213-226), "D2D Sub-System"
  (231-235), "Peripheral" (238-241), "Firmware Agents" (642-680).
- Referenced specs: PCIe EP Rev 2.0.0, PCIe C2C Rev 2.0.0, D2D Rev 2.0.0, PSOC Rev 2.0.0.
