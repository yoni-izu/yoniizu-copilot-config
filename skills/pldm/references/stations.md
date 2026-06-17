# Palladium Station Inventory

VNC desktop = the Palladium GUI / control terminal you connect to. EDK host = the SoC host
server (SSH). IPMI/mgmt = baseboard mgmt controller for power control.

- **VNC / EDK host login**: personal user credentials.
- **IPMI login**: user `elmnt_user`, password `elmnt123!`.
- SSH form: `ssh user@<edk-host> -X`
- IPMI form: `ipmitool -I lanplus -H <mgmt-host-or-ip> -U elmnt_user -P 'elmnt123!' power <cycle|status|on|off>`

## Iridium — PLDM Z2 (`vnc-emul-iridium01`, 192.168.110.142)

| Station | EDK | VNC desktop | EDK host (SSH) | EDK host IP | IPMI/mgmt host | IPMI IP |
|---------|-----|-------------|----------------|-------------|----------------|---------|
| Z2a_1 | EDK0 | `:1` | pldm-edk0-z2a | 192.168.110.123 | mgmt-edk0-z2a | 192.168.110.125 |
| Z2a_2 | EDK1 | `:2` | pldm-edk1-z2a | 192.168.110.134 | mgmt-edk1-z2a | 192.168.110.129 |
| Z2a_3 | EDK2 | `:3` | pldm-edk2-z2a | 192.168.110.159 | mgmt-edk2-z2a | 192.168.110.148 |
| Z2a_4 | EDK3 | `:4` | pldm-edk3-z2a | 192.168.110.152 | mgmt-edk3-z2a | 192.168.110.149 |
| Z2a_5 | EDK4 | `:5` | pldm-edk4-z2a | 192.168.110.184 | mgmt-edk4-z2a | 192.168.110.174 |
| Z2a_6 | EDK5 | `:6` | pldm-edk5-z2a | 192.168.110.169 | mgmt-edk5-z2a | 192.168.110.179 |
| Z2a_7 | EDK6 | `:7` | pldm-edk6-z2a | 192.168.110.190 | mgmt-edk6-z2a | 192.168.110.193 |
| Z2a_8 | EDK7 | `:8` | pldm-edk7-z2a | 192.168.110.195 | mgmt-edk7-z2a | 192.168.110.204 |

Wave-loading VNC desktops (Z2): `vnc-emul-iridium01:9` … `:16` (192.168.110.142:9 … :16).

## Iridium — PLDM Z3 (`vnc-emul-iridium02`, 192.168.110.79)

| Station | EDK | VNC desktop | EDK host (SSH) | EDK host IP | IPMI/mgmt host | IPMI IP |
|---------|-----|-------------|----------------|-------------|----------------|---------|
| Z3a_1 | EDK0 | `:1` | pldm-edk0-z3a | 192.168.110.72  | mgmt-edk0-z3a | 192.168.110.83 |
| Z3a_2 | EDK1 | `:2` | pldm-edk1-z3a | 192.168.110.205 | mgmt-edk1-z3a | 192.168.110.245 |
| Z3a_3 | EDK2 | `:3` | pldm-edk2-z3a | 192.168.110.244 | mgmt-edk2-z3a | 192.168.110.85 |
| Z3a_4 | EDK3 | `:4` | pldm-edk3-z3a | 192.168.110.214 | mgmt-edk3-z3a | 192.168.110.84 |
| Z3a_5 | EDK4 | `:5` | pldm-edk4-z3a | 192.168.110.201 | mgmt-edk4-z3a | 192.168.110.86 |
| Z3a_6 | EDK5 | `:6` | pldm-edk5-z3a | 192.168.110.249 | mgmt-edk5-z3a | 192.168.110.81 |

Wave-loading VNC desktops (Z3): `vnc-emul-iridium02:9` … `:16` (192.168.110.79:9 … :16).

## Lotus

| Station | VNC desktop | VNC IP |
|---------|-------------|--------|
| Z2a_1 (KFS) | `vnc-emul-lotus01:1` | 192.168.110.165:1 |

## JTAG (Lauterbach) availability

Only EDK hosts **edk0, edk2, edk4** (in both PLDM Z2 and Z3) have the Trace32 debugger attached.
