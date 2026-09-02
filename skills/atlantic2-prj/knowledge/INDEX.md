# Atlantic2 (A2) Project Knowledge Base — Index

This is the **expert memory** for the `atlantic2-prj` skill. Every time the skill researches an
A2 topic (Overview/PRM, NoC spec, Rev2 IP specs, project source, RTL diffs, Jira, or the
architect initiatives) and verifies an answer, it records the findings here — emphasizing the
**A2-vs-A1 (Iridium) delta** — so the next query on the same subject is fast.

> **A2 (Atlantic2) is the NEXT project**, successor to **A1 (Iridium)**. Its RTL lives on the
> **`main`** branch of each IP RTL repo; **everything on `main` since the A1 freeze tag is A2
> work**. Rev2.x docs are A2; Rev1.x docs are the A1 baseline.

## How to use this file

1. **Before researching**, scan the table below and full-text search `knowledge/` for relevant
   entries. If a matching topic exists, read its detail file first.
2. **After verifying** a non-trivial finding, record it: create/update `knowledge/<slug>.md`
   using the template below, add/refresh a table row (newest at top), and always cite sources.
3. **If contradicted later**, correct the topic file — keep the KB accurate, not append-only.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| **A2 HSI FW Jira work-breakdown structure (planning template)** | [a2-fw-plan-structure.md](a2-fw-plan-structure.md) | jira, planning, structure, hierarchy, initiative, epic, story, attribute, prjatlsw, prjirdmsw, feature matrix, categories, components, sw-only, product separation, driver convergence, template | Verified from Jira + local FW tree | 2026-08-30 |
| **A2 HSI FW planning — anchors, gaps, redundant tickets** | [a2-fw-planning-gaps.md](a2-fw-planning-gaps.md) | jira, planning, firmware, hsi, initiatives, epics, attributes, gaps, duplicates, redundant, ipsesw, ippciesw, ipd2dsw, pldm | Verified from Jira + RTL | 2026-08-30 |
| **A2 vs A1 executive delta summary (START HERE)** | [a2-vs-a1-summary.md](a2-vs-a1-summary.md) | a2, a1, iridium, delta, summary, changes, gen7, ucie, d2d, pcie, scale-up, gpu, inc, afd, prefill, dual-mode, planning | Verified | 2026-08-30 |
| **A2 RTL analysis (iridium→main delta)** | [a2-rtl-analysis.md](a2-rtl-analysis.md) | rtl, iridium, main, gen7, efec, c2c, aggregator, dual-mode, dmp, n3 phy, piw, pep, pmmu, ucie, gpu, inc, vsp, aligner, cce, ltssm logger | Verified from RTL | 2026-08-30 |
| A2 scale-up architecture (M2M / INC / GPU disagg) | [a2-scaleup-arch.md](a2-scaleup-arch.md) | scale-up, m2m, moe, disaggregation, gpu, hbm, worker, expert, rank, inc, scorpio, commlib, vllm, collective, allreduce, multicast, mrt, flag fifo, tp8, tp16, rail, sled, commgroup | Verified from spec | 2026-08-30 |
| A2 NoC / fabric + address map (INC + GPU) | [a2-noc.md](a2-noc.md) | noc, fabric, address map, npa, fiu, inc, gpu, afd, prefill, remote device, mem fabric, tp8, tp16 | Verified from spec | 2026-08-30 |
| A2 Scale-Up / C2C (PCIe-compliant + dual-mode) | [a2-scaleup-c2c.md](a2-scaleup-c2c.md) | su, scale-up, c2c, pcie compliant, dual-mode, rp, ep, gen7, flit, dragonfly, gpu, inc, ordering, aggregation, tp8, tp16, failover | Verified from spec | 2026-08-30 |
| A2 PCIe EP sub-system (Gen7) | [a2-pcie-ep.md](a2-pcie-ep.md) | pcie, pcie ep, gen7, pam4, flit, fec, piw, elbi, bmu, ide, tee, ptm, msi-x, reset isolation, single ep | Verified from spec | 2026-08-30 |
| A2 D2D sub-system (5nm + new 3nm PHY) | [a2-d2d.md](a2-d2d.md) | d2d, ucie, phy, 5nm, 3nm, n5, n3, arescore, gammacore, chiplet, die-to-die | Verified from spec | 2026-08-30 |
| A2 architect HW initiatives (Confluence) | [a2-architect-initiatives.md](a2-architect-initiatives.md) | initiatives, architect, c2c, scale-up, se, pcie, noc, fiu, inc, gpu, pcie compliant, ordering, aggregation, multicast, fec, ptm, address map, tp8, tp16 | Verified from Confluence | 2026-08-30 |
| A2 HSI Jira landscape | [a2-jira-landscape.md](a2-jira-landscape.md) | jira, hsi, atlantic2, tickets, initiatives, attributes, pldm, palladium, backlog | Verified from Jira | 2026-08-30 |
| A2 Overview — what it is + feature set | [a2-overview.md](a2-overview.md) | a2, atlantic2, overview, feature set, dies, ring, sled, vme, vcore, cce, ucie, pcie gen7, scale-up, firmware agents, cortex-m7 | Verified from spec | 2026-08-30 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from spec | Verified from source | Verified from RTL diff | Verified from Jira | Partial / needs follow-up
- **A2-vs-A1 delta:** <one-line summary of what changed vs Iridium>
- **RTL branch/tag:** main (A2) since <a1-freeze-tag> | <a1-freeze-tag> (A1 baseline) | n/a
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what this entry covers>

## Findings (A2)
<the verified A2 facts — concise, durable, reusable>

## A2 vs A1 (Iridium) delta
<what specifically changed vs the A1 baseline>

## Sources
- Doc: `<Rev2 doc>.docx` — para/table <n> (section "<heading>"); A1 baseline `<Rev1 doc>.docx`
- RTL: `Elmnt-Internal/ip.<ip>...` — `git diff <a1-freeze-tag>..main -- <path>` (commit <sha>)
- Jira: <KEY> (ASIC Project == Atlantic2)
- Confluence: <page id/title>
```
