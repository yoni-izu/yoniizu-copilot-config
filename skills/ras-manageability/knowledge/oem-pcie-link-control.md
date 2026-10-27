# OemPcieLinkControl contract

- **Recorded:** 2026-10-25
- **Status:** Verified from current internal working spec; not an A1 customer-support declaration
- **Tags:** oempcielinkcontrol, link control, ownership, scale-up, quiesce

## Findings

The former `OemLinkControl` is now `OemPcieLinkControl (0x51)`, section
9.5.3.1.3.9. Down/up require MC ownership and return immediately on
acceptance; Get status is read-only and ownership-free. Requests are
idempotent in the requested settled state. Concurrent transitions return
`OEM_LINK_BUSY`; down unconditionally rejects active device-side traffic
with `OEM_LINK_TRAFFIC_ACTIVE`. Ownership alone does not stop workloads.

Status reports held-down/training/up/down-not-held/transitioning, LTSSM,
negotiated width/speed, and `heldByMc`. Ownership release leaves link state
unchanged. Chip reset clears the held state. The spec treats bring-down as
destructive and requires configuration to be re-established after bring-up.
The command spans PCIe EP and SU; a Root-Port-only SE bit-toggle primitive
does not implement that entire contract.

## Additional contract details (verified 2026-10-25)

- Request: vendorIANA, commandVersion=1, subCommand 0x01 down / 0x02 up /
  0x03 get status; subsystem 0=PCIe EP, 1=Scale-up; instance EP 0-1,
  SU 0-31 (same encoding as OemMcOwnership).
- Link down on a link that is "down, not held" latches it to held and
  returns SUCCESS. Link up on an up link returns SUCCESS without disturbing it.
- No device-side transition timeout; MC owns the deadline (~100 ms worst case).
  The spec describes the held state as ltssmState = Detect.Quiet, reached via
  Recovery (up to 24 ms). The SE primitive classifies held as LTSSM
  Disabled; reconcile before PLDM wiring.
- After SU bring-up, device management FW must re-establish port config,
  including address translation. EP requires host re-enumeration.
- Disable: when disabled in device config, the command is omitted from Type
  0x3F discovery and returns ERROR_UNSUPPORTED_PLDM_CMD. Internal eFuse
  FEATURE_DISABLE[1] = DIS_PCIE_LINK_CTRL; latch at boot; gate all
  sub-commands before parsing; PLDM path only (in-band/FW unaffected).
- Spec inconsistencies: table 9-25 still lists OEM_LINK_CTRL_DISABLED (0x99)
  even though the text defines no OEM disabled code; eFuse requirement 2
  says "all five sub-commands", but only three exist.
- Privileged (Admin), OOB SPI only. Pair with OemPcieLtssmLogger to
  distinguish commanded vs fault L0 exits. OemPciePhyInit requires
  linkState=Down held (OEM_PHY_LINK_NOT_HELD).

## Sources

- `Device Manageability Specification.docx`, section 9.5.3.1.3.9,
  tables 9-72 through 9-80. Effective XML paragraph indices (all body
  descendants, inserted text included, deleted text excluded):
  6084-6118, 6130-6171, 6182-6201, 6207-6287, 6293-6319.
- Hardware mechanism: `su-ip/knowledge/oem-pcie-link-control.md`;
  DWC reference PDF page 2176.
