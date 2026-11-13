# LTSSM Equalization Phase Timeouts

- **Recorded:** 2026-11-12
- **Status:** Verified from A1 controller documentation; Gen6 Recovery timers unverified
- **Tags:** ltssm, recovery, equalization, phase, timeout, gen3, gen4, gen5, gen6

## Scope / Question

Recovery.Equalization phase timeouts for the A1 Synopsys PCIe EP controller
v6.42a-lca01. USP means the endpoint-side upstream port; DSP means its
downstream link partner.

## Findings

For the Gen3/4/5 equalization state diagram, the timers are per port and phase,
not a single 24 ms budget shared by the whole process:

| Phase | USP | DSP | Role |
|---|---:|---:|---|
| 0 | 12 ms | Not entered | USP exchanges initial equalization information |
| 1 | 12 ms | 24 ms | Both exchange equalization capabilities |
| 2 | 24 ms | 32 ms | USP leads; DSP follows |
| 3 | 32 ms | 24 ms | DSP leads; USP follows |

Phase 0/1 timeout transitions go to Recovery.Speed. In phases 2/3 the
24 ms EQ lead timeout also normally goes to Recovery.Speed; the follower has
the longer timer. `GEN3_EQ_PHASE23_EXIT_MODE` bit 4 can instead let the EQ lead
continue to phase 3 (USP) or Recovery.RcvrLock (DSP), including when optimal
settings were not found. The reference says this setting can mark phase
success in Link Status 2 despite not finding optimal settings. The register
is rate-shadowed; reset values are configuration-dependent.

The internal PHY evaluation timer is separate from the phase timer.
`GEN3_EQ_EVAL_2MS_DISABLE` bit 5 controls whether a slow PHY evaluation is
aborted after 2 ms or ignored; `GEN3_EQ_EVAL_2MS_EXTEND` bit 7 extends the
default 2 ms response timeout to 4 ms. Do not treat either timer as the
entire phase limit.

For Gen4 with a detected retimer, the documented phase-2 exit has additional
retimer-related waiting behavior. Do not assume the nominal lead timeout is
the actual completion time in all configurations.

The diagram is in the Gen3/4/5 section. The LTSSM reason table says phase 2/3
timeout values depend on data rate; it does not establish Gen6 Recovery
values. The separate Gen6 *loopback* descriptions have 48/64 ms values, but
do not establish the corresponding normal Recovery timers. Neo confirmed that
the A1 mirror does not contain the Synopsys LTSSM controller core, so its
Gen6 Recovery timer behavior could not be verified from RTL.

## Sources

- Synopsys `DWC_pcie_ctl_ep_databook.pdf`, v6.42a-lca01, pp. 699-702:
  LTSSM transition reasons; pp. 747, 751, 754-757, 762: EQ lead/follower,
  timer diagram, configuration and retimer behavior; p. 790: loopback only.
- Synopsys `DWC_pcie_ctl_reference.pdf`, v6.42a-lca01, pp. 6902-6904:
  `GEN3_EQ_CONTROL_OFF` bits 7, 5 and 4 and rate shadowing.
- Neo, A1 `ip.pcie.rtl` mirror commit
  `693ce5494a4ee51a9b787e4bbaf218950ee00027`: controller core unavailable
  in the mirror; wrapper and state logger do not establish phase timers.
