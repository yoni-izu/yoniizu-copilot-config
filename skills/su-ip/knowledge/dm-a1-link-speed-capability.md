# A1 DM maximum, target and Supported Link Speeds Vector

Recorded 2026-11-15 from the implementation handoff's Neo inquiry; no local or
GitHub RTL was read. Scope: A1 only, not an assertion about A2 controller internals.

## CSR-backed facts

Source: `Elmnt-Internal/ip.se.csr.iridium`,
commit `26b653cffed8dccf3a038e9a976e07b057b27795`.

| Declaration | Citation under `yml/dm_macro/` | Meaning |
| --- | --- | --- |
| DSP maximum | `dsp/DM_DSP_PF0_PCIE_CAP.yml:297-303` | DBI-writable with `DBI_RO_WR_EN`, sticky, reset Gen6 |
| USP maximum | `usp/DM_USP_PF0_PCIE_CAP.yml:325-331` | Corresponding USP maximum declaration |
| DSP vector | `dsp/DM_DSP_PF0_PCIE_CAP.yml:1075-1081` | Software-read-only, reset `0x3f`; default described in terms of maximum |
| USP vector | `usp/DM_USP_PF0_PCIE_CAP.yml:742-748` | Corresponding USP vector declaration |
| DSP target | `dsp/DM_DSP_PF0_PCIE_CAP.yml:1148` | DBI-writable, sticky; target must belong to supported vector |

## What remains unverified

Neo could not inspect the vendor controller internals. Wrapper evidence at
`ip.se.rtl` commit `60ddeee6bfaddc539b0f01c325ec361dab66651b` does not prove that
the vector changes live when firmware changes maximum. The reset/default
description is not proof of dynamic coupling, and there is no cited DBI-write
permission for the vector. A suggested explicit vector write was not grounded
by the cited evidence and must not be used as an established programming sequence.

Retain the established maximum-field programming/readback while making approved
configuration changes. Do not write the vector or require equality as a new
bring-up failure condition without vendor/hardware evidence. Readback mocks and
negotiated speed cannot prove vector narrowing. Cold-reset silicon validation
must inspect maximum, vector and target, with both RP/DSP and EP/USP views.
