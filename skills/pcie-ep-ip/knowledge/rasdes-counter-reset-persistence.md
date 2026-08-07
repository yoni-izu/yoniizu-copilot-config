# RAS DES event counters are STICKY — they survive warm reset, and FW clears them anyway

- **Recorded:** 2026-08-07
- **Status:** Verified from RTL repo (uArch doc + wrapper RTL) + Iridium SoC spec + FW
- **Tags:** ras des, event counter, sticky, non-sticky, reset domain, warm reset, perst,
  button_rst_n, power_up_rst_n, app_mask_sticky_rst, ec_restart, ec_clear, ec_enable,
  post_bring_up_cfg, wd reset, persistence, telemetry continuity

## Scope / Question
Do the DWC RAS DES event counters survive a reset? Which Iridium resets actually reset the PCIe
EP controller? And is FW's `dwc_ep_ctl_ec_restart()` on every bring-up safe with respect to
counter continuity for manageability attributes?

## The short version (sufficient for most arguments)
**PEP is reset only by PoR and Warm.** For every other reset (WD, FLR, Hot, Platform, Hard, Soft,
Debug) the controller is untouched and its counters are simply still there, still counting — no
stickiness reasoning required. So FW clearing on bring-up is either redundant (controller was
reset) or destructive (it wasn't). The sticky-domain detail below only refines the Warm case; do
not go digging in RTL to make this point.

## Findings

### RAS DES lives in the controller's STICKY reset domain
From the Element wrapper micro-architecture doc in the RTL repo (note 6, "Sticky vs. non-sticky
resets"):

> "The native core and PHY logical blocks have separate sticky and non-sticky reset domains.
> Sticky registers survive warm reset (PERST# deassertion); non-sticky registers are cleared.
> This distinction is important for debug registers **(RAS/DES)** that must persist across warm
> resets."

So RAS DES state is cleared only by `power_up_rst_n` (cold / power-up), **not** by
`button_rst_n` (warm / PERST#). The persistence is deliberate design intent, not incidental.

The `app_mask_sticky_rst` input — the hook that would let the application force sticky registers
to reset — is tied to `'0` in `pep_macro/rtl/pep_wrap.v` (commented `//TODO`), so the sticky
behavior is live and not overridden.

### Which Iridium resets reset PEP at all
Per `Iridium SoC spec.docx` Table 4-2 (see `iridium-prj/knowledge/reset-domains.md`), **PCIe EP
is reset only by PoR and Warm.** Not by Platform, WD, FLR, Hot, Hard, Soft, or Debug.

Combined:

| Reset type | PEP reset? | RAS DES counters in HW |
|---|---|---|
| PoR / cold | yes | cleared (`power_up_rst_n`) |
| Warm (PERSTn) | yes | **survive** — sticky domain |
| WD | no | **survive** — controller untouched |
| FLR / Hot / Platform | no | **survive** — controller untouched |

Only a cold/PoR reset zeroes the counters.

### Two non-reset ways the counts still go away
Persistence is about *resets*; it is not an unconditional durability guarantee. The databook adds
two caveats (§29.3.3.1, note after Table 29-17):

- **L2 exit:** "All RASDES analysis results are cleared when the controller exits from L2."
- **Power gating:** RASDES results are undefined if analysis is active during power gating; the
  PMC does not save them, and removing power from the `cdm_rasdes_ec_ram*` /
  `cdm_rasdes_tba_ram*` RAMs loses the results.

Neither changes the conclusion below — FW clearing on bring-up is still wrong — but any consumer
treating these counters as strictly monotonic must tolerate an occasional drop to zero.

### FW discards them on every bring-up
`el_pcie_ep_post_bring_up_cfg()` calls `dwc_ep_ctl_ec_restart()`, which is
`ec_clear()` (writes `EVENT_COUNTER_CLEAR = EC_CLEAR_ALL`, then back to `EC_CLEAR_NONE`) followed
by `ec_enable()` (`EVENT_COUNTER_ENABLE = EC_ENABLE_GLOBAL`). The clear half is redundant after a
cold reset and **destructive in every other reset case**.

Worse, `el_pcie_ep_post_bring_up_cfg()` is invoked **unconditionally** at the end of
`el_pcie_ep_init()` — outside the `if (run_post_reset_at_boot)` guard — so it also runs on boots
where bring-up was skipped entirely. That is precisely the WD case: MuC reboots, PIW is reset,
**PEP is not**, and FW wipes a live controller's accumulated counters.

### Consequence for manageability
RAS DES counters back the per-link DLL/PHY manageability attributes
(`PcieBadTlpErrorCount`, `PcieBadDllpErrorCount`, `PcieReceiverErrorCount`,
`PcieReplayNumberRolloverCount`, `PcieRetryTlpCount`, `PcieNakSent/ReceivedCount`,
`PcieLcrcErrorCount`). If FW aggregates RAS DES deltas into monotonic attributes, an
unannounced reset-to-zero corrupts the accumulation. The hardware already solved persistence;
FW is undoing it.

### Correct policy — subtractive, no caching required
Because the counters are sticky, **no FW-side cache is needed to survive resets.** The fix is to
stop clearing:

```c
rc = dwc_ep_ctl_ec_enable(dev);   /* not ec_restart() */
```

After a cold reset the counters are already zero, so enable alone is correct; after
warm/WD/FLR/hot they keep accumulating. `ec_clear()` remains available as an explicit operator
action (SV shell). This also removes any need for FW to branch on reset type for counter
handling.

If "fresh counts at every bring-up" is ever the deliberate policy, note that it contradicts the
sticky-by-design hardware and must be documented as such.

## Sources
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `pep_macro/misc/ELEMENT_LABS_EP_SUBSYS_WRAP_Micro_Architecture.md`
  note 6 ("Sticky vs. non-sticky resets"); reset-input table (`button_rst_n` = warm/button,
  `power_up_rst_n` = power-up/cold).
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `pep_macro/rtl/pep_wrap.v` (`app_mask_sticky_rst ('0) //TODO`).
- Spec: `~/docs/Iridium/Iridium SoC spec.docx` Table 4-2 "Reset Domains" — PEP reset by PoR + Warm only.
- FW: `ip.pcie.sw.fw.ep` → `drivers/el_pcie_ep/el_pcie_ep.c`
  (`el_pcie_ep_post_bring_up_cfg`, `el_pcie_ep_init` unconditional call site),
  `drivers/snps_ep_ctl/dwc_ep_ras_des.c` (`dwc_ep_ctl_ec_clear/ec_enable/ec_restart`),
  `drivers/el_pcie_ep/el_pcie_ep_reset.c` (`el_pcie_ep_reset_try_post_reset`).
- Related KB: `iridium-prj/knowledge/reset-domains.md`,
  `pcie-ep-ip/knowledge/rasdes-vs-telemetry-metrics.md`,
  `ras-manageability/knowledge/rasdes-lane-classes-vs-attributes.md`,
  `ras-manageability/knowledge/pcie-aer-fw-counters.md`.
