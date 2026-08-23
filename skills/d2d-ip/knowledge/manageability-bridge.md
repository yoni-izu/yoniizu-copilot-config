# Manageability Bridge (D2D wrapper manageability glue)

- **Recorded:** 2026-08-16
- **Status:** Verified from spec
- **Tags:** manageability bridge, management port, mtp, management transport packet, ucie manageability, memory access protocol, test and debug protocol, legality checks, access control, class id, rac, wac, security clearance group, traffic class, tc, mainband, sideband, vdm, compression engine, indirect mtp, doorbell, chiplet id, entity id, ipd2dsw-70

## Scope / Question
What is the "Manageability Bridge" feature (Jira IPD2DSW-70, epic under Initiative
PRJIRDMSW-3190 "HSI Messaging & Mailbox", component ip-d2d-firmware). The ticket points
to spec "chapter 8.5"; in Rev 2.0.0 the section is numbered **8.8 Manageability Bridge**
(ToC also lists 8.5 Arbitration Scheme — the ticket's "8.5" is a stale draft number).

## Findings
Purpose: the **D2D wrapper** adds glue logic that maps the chiplet's internal **management
engines / fabrics** (which speak over multiple internal fabrics) onto the single UCIe
**Management Port**, complying with the UCIe manageability model (Management Transport
Packet / MTP over the Management Network). Management link can be carried over UCIe
**sideband** or **mainband**; a chiplet must support at least one sideband Management Port.

Two directions:

**Inbound (chiplet is MTP destination):**
- **Legality checks** (HW-validated, each disable-able, all disabled by default):
  Management Network Destination ID vs Chiplet ID (if valid bit set), Entity ID (must be a
  used entity), Protocol ID (Memory Access=3'h1, Test&Debug=3'h2), Packet Integrity (PIPP),
  UCIe version==2'b00, reserved cleared, ≤ Max Packet Size (MPS), length==actual DW count,
  Security Clearance Group cleared in response, capability structures accessed as single
  DWORDs. Malformed packet → capture info, set error status bit, raise interrupt, discard.
- **Traffic Class**: response TC must match request TC.
- **Memory Access Protocol checks**: valid opcode, length vs DW, zero-length w/ nonzero
  Last-DW-BE, DW-only programming model → malformed returns "Packet Error", no processing.
- **Ignore Prohibited Access**: requestor can ask to convert a prohibited-asset error
  response into a successful (no-access) completion, so ranges can be probed error-free.
- **Ordering**: config option to send completions in request-arrival order.
- **Transaction Buffering**: HW buffers received requests (depth per UCIe Memory Access
  Protocol Capability Structure).
- **Access Control**: SoC assets mapped to security **Class IDs** enforced via RAC/WAC
  registers; up to 32 configurable address-range→Class-ID config-mapping entries. Only a
  security director using **Security Clearance Group0** may configure security params.

**Outbound (chiplet is initiator):**
- **Traffic Class mapping** of internal fabrics: FW download→TC2 (bulk), Interrupts→TC0,
  Error reporting→TC0, Power/thermal→TC1, Config accesses→TC0, Trace→TC4, Debug→TC0,
  Breakpoint→TC1. Config-fabric mapping uses 8 configurable 64b address regions.
- **Port Type**: Chiplet Capability holds two routing entries (mainband + sideband); each
  has a "VC Select" defining which TCs route via that port.
- **Tag**: HW assigns a unique tag per Memory-Access request to match out-of-order
  responses; splits packet in two if it exceeds CMPS.
- **Request Retry**: responder may return retry status → HW keeps a replay buffer of
  outstanding requests.
- **Security Clearance Group**: CFG/Trace fabric take SL from AxUESR; DBG/ECT fabric have
  no SL so wrapper attaches a configurable per-fabric value (can also override CFG/TRC SL).
- **Source/Destination ID**: Source ID from Chiplet Capability. Dest (Chiplet+Entity ID)
  from configurable address bits: Config fabric AxADDR[N+15:N]; Breakpoint (ECT) configurable
  dest ID; Debug (DBG) DAP 31-bit address decoded into chiplet-internal addr + Entity ID +
  Chiplet ID (configurable field layout).
- **Indirect MTP Generation**: uC configures all MTP fields over CFG fabric + doorbell;
  completion reported back to uC via interrupt.

**Sideband communication** (VDM over UCIe sideband) — used to deliver initial FW and to
debug the remote die when mainband is down. Two modes: **Hardware mode** (HW captures MNG
fabric and sends as VDMs, dedicated Generation+Reception FSM on each side, message code
0xFF, subcode selects AXI AW/AR/B/R, each AXI req → two VDMs) and **Software mode** (SW
builds the SB message via indirect registers and triggers send). Each side instantiates
both Generation and Reception FSM. (See also `vdm-over-sb-support.md`.)

**Compression Engine**: wrapper instantiates a compression engine to compress MEM data and
raise throughput; static config can disable it to cut latency (WUSER bit flags compressed).

**Manageability interrupts / stats**: HW provides statistic counters (generated UCIe FLITs,
idle cycles) and manageability-specific interrupts (e.g. "Sideband response ready").

## Jira context
- IPD2DSW-70 "Manageability Bridge" — **Epic**, status **Backlog**, assignee Igal Krivoshey,
  component **ip-d2d-firmware**, parent Initiative **PRJIRDMSW-3190 "HSI Messaging & Mailbox"**.
- Removed from milestones iridium-system-sw 6.0, 6.5, and 7.0 (deferred repeatedly).

## Sources
- Spec: `D2D Specification Rev 2.0.0.docx` — §8.8 Manageability Bridge, paras 1032–1233
  (inbound 1039–1097, outbound 1099–1151, sideband 1154–1170, compression 1172–1179,
  interrupts/stats 1221–1233); UCIe manageability model paras 525–539.
