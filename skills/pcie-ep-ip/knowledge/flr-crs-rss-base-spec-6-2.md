# FLR and configuration RRS timing (PCIe Base Spec 6.2)

In Base Spec 6.2 terminology, CRS is the Request Retry Status (RRS) Completion Status.

- §6.6.2, pp. 770-771: the Function must complete FLR within 100 ms. While FLR is in progress, it may silently discard arriving Requests after updating flow-control credits. Function-specific initialization may take longer; after the 100 ms limit, a Configuration Request must receive RRS if the Function is not yet ready. After returning any Configuration Completion status other than RRS, the Function must not return RRS again until another reset.
- §2.3.1, p. 248: FLR is a valid reset condition for RRS responses. RRS is not permitted after the Function has indicated Configuration-Ready or successfully completed a Configuration Request, absent another valid reset. If Readiness Time Reporting is implemented, RRS must stop after the relevant reported time. The §2.3.1.1 implementation note, p. 251, says the Root Complex generally reissues Configuration Requests terminated with RRS until it gets a status other than RRS.
- §7.9.16, pp. 1343-1346: Readiness Time Reporting is optional. When present, it reports FLR Time; the Function must be Configuration-Ready after that interval. The encoded FLR Time is bounded by A1Eh (about 1.006 s).
- §6.22.2, p. 851: FRS is optional; when implemented, it must be used after FLR to indicate that the Function is Configuration-Ready. After that FRS, RRS is prohibited until another readiness event.
- The spec defines RRS as a Completion Status, not all-ones data. All-ones observed by software is host/platform-visible failure data, not an endpoint response that §6.6.2 authorizes for a duration.

Therefore the 100 ms FLR completion limit is not a 100 ms limit on configuration unavailability: post-FLR initialization can continue with RRS. The cited clauses specify no universal upper bound for that RRS interval when neither a readiness-time deadline nor a readiness notification applies. This does not authorize a dark gap: before ready, return RRS; after successful completion/FRS, stay accessible until a valid reset.

Source: `NCB-PCI_Express_Base_6.2-2024-01-25.pdf`, `/home/yoniizu/docs/PCIe EP/`, Revision 6.2.
