# Image List by PCIe Rev ID

Image name (substring, `*..*`) with its PCIe rev ID in decimal and hex. Use this to map a
running image to its rev ID, or to pick the `pd_load` image for a given topology.

Naming conventions:
- `<N>chip(s)` — number of chips. `<M>die(s)` — dies per chip.
- `_h` / `_v` — horizontal / vertical die arrangement.
- `_w_gmem` — with global memory. `_aw_ucie` — with UCIe. `_switch` — with switch.

| Image name | ID (Dec) | ID (Hex) |
|---|---|---|
| `*1chip_1die*` | 34 | 22 |
| `*1chip_1die_w_gmem*` | 35 | 23 |
| `*1chip_2dies_h*` | 50 | 32 |
| `*1chip_2dies_h_w_gmem*` | 51 | 33 |
| `*1chip_2dies_h_aw_ucie*` | 52 | 34 |
| `*1chip_2dies_h_w_gmem_aw_ucie*` | 53 | 35 |
| `*1chip_2dies_v*` | 66 | 42 |
| `*1chip_2dies_v_w_gmem*` | 67 | 43 |
| `*1chip_2dies_v_aw_ucie*` | 68 | 44 |
| `*1chip_2dies_v__w_gmem_aw_ucie*` | 69 | 45 |
| `*1chip_4dies*` | 82 | 52 |
| `*1chip_4dies_w_gmem*` | 83 | 53 |
| `*1chip_4dies_aw_ucie*` | 84 | 54 |
| `*1chip_4dies_w_gmem_aw_ucie*` | 85 | 55 |
| `*2chips_1die*` | 98 | 62 |
| `*2chips_1die_w_gmem*` | 99 | 63 |
| `*2chips_1die_switch*` | 102 | 66 |
| `*2chips_1die_w_gmem_switch*` | 103 | 67 |
| `*2chips_2dies_h*` | 114 | 72 |
| `*2chips_2dies_h_w_gmem*` | 115 | 73 |
| `*2chips_2dies_h_aw_ucie*` | 116 | 74 |
| `*2chips_2dies_h_w_gmem_aw_ucie*` | 117 | 75 |
| `*2chips_2dies_v*` | 124 | 7C |
| `*2chips_2dies_v_w_gmem*` | 125 | 7D |
| `*2chips_4dies*` | 146 | 92 |
| `*2chips_4dies_w_gmem*` | 147 | 93 |
| `*2chips_4dies_aw_ucie*` | 148 | 94 |
| `*2chips_4dies_w_gmem_aw_ucie*` | 149 | 95 |
| `*4chips_1die*` | 162 | A2 |
| `*4chips_1die_w_gmem*` | 163 | A3 |
| `*4chips_1die_switch*` | 174 | AE |
| `*4chips_1die_w_gmem_switch*` | 175 | AF |
| `*4chips_2dies_h*` | 164 | A4 |
| `*4chips_2dies_h_w_gmem*` | 165 | A5 |
| `*4chips_2dies_h_aw_ucie*` | 166 | A6 |
| `*4chips_2dies_h_w_gmem_aw_ucie*` | 167 | A7 |
| `*4chips_2dies_v*` | 168 | A8 |
| `*4chips_2dies_v_w_gmem*` | 169 | A9 |
| `*4chips_4dies*` | 170 | AA |
| `*4chips_4dies_w_gmem*` | 171 | AB |
| `*4chips_4dies_aw_ucie*` | 172 | AC |
| `*4chips_4dies_w_gmem_aw_ucie*` | 173 | AD |
| `*8chips_1die*` | 178 | B2 |
| `*8chips_1die_w_gmem*` | 179 | B3 |
| `*8chips_1die_switch*` | 186 | BA |
| `*8chips_1die_w_gmem_switch*` | 187 | BB |
| `*8chips_2dies_h*` | 180 | B4 |
| `*8chips_2dies_h_w_gmem*` | 181 | B5 |
| `*8chips_2dies_h_aw_ucie*` | 182 | B6 |
| `*8chips_2dies_h_w_gmem_aw_ucie*` | 183 | B7 |
| `*8chips_2dies_v*` | 184 | B8 |
| `*8chips_2dies_v_w_gmem*` | 185 | B9 |
