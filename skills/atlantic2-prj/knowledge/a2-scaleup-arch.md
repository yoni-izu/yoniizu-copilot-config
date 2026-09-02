# A2 (Atlantic2) — Scale-Up Architecture Enhancements (M2M / INC / GPU disaggregation)

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (A2_Arch_Enhancments_v1.3.docx + A2 Scale-up.docx)
- **A2-vs-A1 delta:** The **big architectural leap of A2** — a unified scale-up domain of up to
  **16K logical devices (Ranks)** mixing **A2 (experts, MoE) + GPUs (workers, attention)** for
  **disaggregated LLM inference**, with **In-Network Compute (INC)** collectives on the scale-up
  switch and a **many-to-many (M2M)** worker↔expert dataflow. None of this existed in A1.
- **Tags:** scale-up, m2m, moe, disaggregation, gpu, hbm device, worker, expert, rank, inc,
  scorpio, commlib, vllm, collective, allreduce, reducescatter, allgather, multicast, mrt,
  flag fifo, tp8, tp16, rail, sled, commgroup, acceleration context

## Scope / Question
The A2 scale-up architecture enhancements: the logical Rank domain, GPU integration, M2M
worker/expert dataflow, INC collective acceleration, and dynamic multicast.

## Findings (A2)

### Logical scale-up domain (Rank abstraction)
- Fabric connects **A2 chips + GPUs across compute sleds** via a **logical device abstraction =
  Rank**. A Rank = one device = either an **A2 chip** or a **GPU endpoint**.
- Up to **16K Ranks** (14-bit encoding). HW translation table maps Rank → **Device Index (8b)** +
  **Chip ID** + **Die ID**; deterministic routing. Up to **256 endpoints per switch** (future).
- Three connectivity domains every transaction may traverse: **Intra-chip (D2D)** → **Intra-sled
  (C2C, PCIe Gen7)** → **Inter-sled (scale-up switch / rail)**.

### Topology (sled / rail)
- Compute sled = **8 or 16 A2** accelerators (**TP8 / TP16**). Each A2 chip = **4 dies**.
- **Intra-sled = mesh per die index**: all Die[i] across chips interconnected → **4 independent
  intra-sled fabrics** (one per die index). TP16: each die ↔ 15 peers via Gen7 **x2** (4×15
  ports/chip). TP8: each die ↔ 7 peers via Gen7 **x4** (4×7 ports/chip).
- **Inter-sled**: each die has one scale-up port. TP16 = Gen7 **x2**; TP8 = Gen7 **2x2**.
- **Rail topology**: each rail = one scale-up switch; **Rail[i] → all Sled[*].Chip[*].Die[i]**
  (different die indices → different rails → simplifies routing). Switch ≈ 320 lanes / 160 devices
  (x2). System: up to 128 sleds × 16 chips × 4 dies → 16K devices.

### GPU integration & addressing
- **A2 → GPU only via MWr (posted write) TLPs** — A2 never issues MRd toward remote A2/GPU.
  **GPU → A2** via A2 BAR space exposed by platform config. **GPU↔GPU** only within the same rail.
- Each GPU: 4-bit BAR address space; A2 host access uses 56-bit canonical address space.

### M2M (many-to-many) worker/expert dataflow — **disaggregated MoE**
- **Workers = HBM devices (GPUs) running attention** (multiple worker groups, different data).
- **Experts = A2 devices running MoE** (a single MoE tensor may be split across several A2s in a
  group). Any worker → any expert; **inter-group comm = memory semantics (posted writes) over the
  PCIe-switch fabric**. Pipeline: worker sends token → expert computes → result written back.
- **Data-then-flag model** (respects PCIe ordering): data sent **relaxed (RO=1)** for throughput,
  then a **flag** sent strongly ordered.
- **A2 HW support**: per-worker private data buffers in A2 global memory (direct-write RO=1,
  multiple pre-allocated per worker); **flag FIFO over memory** (A2 implements **16 FIFOs**), NoC
  guarantees flag pushed only after all prior data (no fabric flush); flag = 4-byte payload with
  RO=0, matched against **16 configurable address regions**.
- **MRT (Memory Region Tracking)**: aggregates all flags (all PCIe ports + memory-fabric
  channels) per work request; on completion pushes a **single flag** to the memory-mapped FIFO,
  captures **worker_id** = `flag_addr[15:2]` (match `flag_addr[35:16]==cfg_flag_base`), and
  **notifies the VCore(s)**. FIFO = power-of-two ring, per-instance counter.

### INC (In-Network Compute) collective acceleration
- Collectives (**AllReduce / ReduceScatter / AllGather**) offloaded to the **scale-up switch** —
  in-network reduction + multicast, less fabric data movement, lower latency.
- Switch exposes programmable **Acceleration Contexts (ACs)** (ports, arithmetic op, operand
  format, buffer map, completion). **Atlantic CommLib** manages INC at **commGroup** level
  (group of ranks in a collective).
- Reduction ops **SUM/MIN/MAX** (select via `math_mode`/MODE_UPDT); data types **FP32/BF16/FP8/
  INT8** (`op_mode`). AR/RS in **ordered mode** for bit-exact results; RS uses fused contexts
  (one sub-context per destination rank). AG uses **multicast mode 0** (memory-less, payload
  agnostic).
- **Buffer sizing**: per-rank = BW×latency = 32 GB/s × 1 µs = **32 KB**; switch line granularity
  16 KB → 2 lines/rank (double-buffered); CommGroup = num_ranks × 32 KB (128-rank → **4 MB**).
- **Dynamic AC/buffer allocation** at runtime from the **VCore kernel** (all-gather barrier +
  reconfig). **A2↔HBM resource sharing**: single AC pool + reduction buffer shared; **vLLM
  negotiation** partitions (N_HBM/N_A2 ACs, B_HBM/B_A2 buffer; HBM region at offset 0, A2 after).

### Dynamic multicasting
- For AFD where the router is on the HBM device. Modeled on **Scorpio 3** switch behavior.
- **GPU updates a multicast entry through A2's 128 KB BAR** (MMIO): A2 exposes a 128 KB PCIe-BAR
  region holding a **dynamic multicast table of 256 entries**. HBM device sends a magic packet to
  a slot; subsequent data packets use the updated entry immediately (no fence/flush).

## A2 vs A1 (Iridium) delta
- **Entirely new in A2**: GPU integration into the scale-up domain (A2=experts / GPU=workers),
  16K-Rank logical addressing, M2M worker/expert dataflow with flag-FIFO + MRT HW, INC
  collective offload to the switch (CommLib/commGroup/ACs), A2↔HBM vLLM resource negotiation, and
  dynamic multicast via 128 KB BAR. A1/Iridium scale-up was A2↔A2 only, no GPU, no INC offload.

## Sources
- Doc: `~/docs/A2/A2_Arch_Enhancments_v1.3.docx` — Purpose/Scope (119-136), Domain Overview
  (148-234), Logical→Physical (236-241), Collective Op Acceleration / INC (417-473), M2M
  Enhancements (478-522).
- Doc: `~/docs/A2/A2 Scale-up.docx` — overlapping subset (domain overview, platform variants,
  routing model, address space, HW assumptions).
- Cross-ref: a2-noc.md (INC/GPU address spaces), a2-scaleup-c2c.md (dual-mode PCIe controller),
  a2-architect-initiatives.md (SE/C2C initiatives).
