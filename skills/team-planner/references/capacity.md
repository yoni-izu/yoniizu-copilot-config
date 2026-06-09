# Capacity Model

## Sprint Focus-Days Formula

```
Sprint focus-days =
  Σ(engineer available days × focus factor)
  - PTO / holidays
  - fixed meeting load (planning, standups, reviews, retros)
  - lead management / review / cross-functional load
  - planned lab support
  - planned debug support
  - external-review support (PRs, design reviews)
  - interrupt reserve
```

## Planning Rule

```
Planned sprint scope ≤ min(
  focus-days available,
  historical completed points × availability factor × confidence factor
)
```

Commit ~70-80% of focus-days to planned feature work. Keep the remainder for debug, lab, review, and fast risk retirement.

## Example: 5-Person Team, 10-Workday Sprint

```
Nominal capacity
  5 engineers × 10 days = 50 engineer-days

Subtract
  PTO / holidays:                          ~3 days
  meetings / planning / review:            ~5 days
  tech lead mgmt + cross-functional load:  ~4 days
  planned lab coverage:                    ~5 days
  bring-up / debug interrupt reserve:      ~6 days
  support for validation / host / RTL:     ~3 days

Estimated focus-days = 50 - 3 - 5 - 4 - 5 - 6 - 3 = 24 focus-days
```

These numbers are a starting template. Tune from actual data: blocked external days, lab wait days, escaped lab-only defects, unplanned debug days.

## T-Shirt Sizing Guide

| Size | Scope | Dependencies |
|---|---|---|
| XS | Known Zephyr config, small bug fix, unit test | None |
| S | Driver or FW change with stable CSR/API; testable in native_sim / QEMU | None or minimal |
| M | IP integration with current CSR map; needs emulator, FPGA, validation script, or host-driver coordination | 1-2 external |
| L | Cross-team contract change; requires RTL / DV / validation / host agreement; lab or emulation window needed | Multiple external |
| XL | Unknown silicon behavior, new interface mode, unstable spec, missing proxy; **must be split into spike + evidence goal before sprint commitment** | Unknown |

## Blocked External Day

A day (or meaningful fraction) where the engineer cannot advance planned work because the next dependency is outside FW control.

Required fields when logging:
- **Blocked by:** RTL \| silicon \| lab \| board \| host driver \| validation \| release \| product
- **Start time**
- **Owner**
- **Next action**
- **Fallback work**

Blocked days should be tracked as a capacity drain / dependency metric, **not** treated as low engineer productivity.

## Key Rule

> "Velocity is a weather forecast, not a production quota."

Do not use velocity to compare engineers or teams. Use it to calibrate forecasts and detect systemic changes.
