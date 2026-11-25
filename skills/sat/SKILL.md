---
name: sat
description: "Use the SAT API and CLI to inspect lab setups, allocations, workflow runs, and power status. Use when: SAT, lab machine inventory, setup availability, SAT reservations, workflow status, SAT API, or the SAT web portal."
argument-hint: "SAT question or API task — inventory | allocations | workflows | power | console integration"
---

# SAT — Setup Automation Tool

Use the tracked read-only client `sat` to query SAT v1. The client is shared by
`mctl` and `machine-pilot`; read the API and examples below before using it.

## Authentication and transport

- Create a **read-only** personal API token in SAT's User settings for
  inventory/status queries. The SAT UI shows it once. API tokens are personal:
  SAT applies the owner's current permissions and may return 404 for setups the
  owner cannot see.
- Set `SAT_TOKEN` in the calling process, or store the token in
  `~/.copilot-private/sat/token` with mode `0600`. Keep the token out of command
  arguments, shell history, tracked files, logs, chat, and screenshots. Do not
  ask the user to paste a token into chat.
- The default server is `https://sat.elmnt.local`. TLS certificate verification
  is always enabled; do not use `curl -k` or disable verification. `SAT_URL`
  can override the origin for an approved test environment; plain HTTP is
  accepted only for loopback tests. If the internal CA is not in the system
  trust store, configure `SSL_CERT_FILE` to an approved CA bundle.
- SAT currently presents a **self-signed** certificate. Without involving
  system trust, pin it for SAT only at `~/.copilot-private/sat/ca.pem` (or
  `SAT_CA_FILE`). When present, the client trusts only that file for SAT
  requests; a missing explicit `SAT_CA_FILE` fails closed. Before relying on a
  newly pinned certificate, compare its SHA-256 fingerprint
  (`openssl x509 -in ~/.copilot-private/sat/ca.pem -noout -fingerprint -sha256`)
  with the one the browser shows for `sat.elmnt.local`. When SAT renews its
  certificate, requests fail verification until the pin is replaced and
  rechecked.
- API tokens can be read-only or read/write and expire within 90 days. The
  client in this repository is intentionally **GET-only**. Do not use a
  read/write token to bypass this limit or make writes with a generic caller.
  Reservation changes, workflow/pipeline launches, and power operations remain
  in the SAT UI until their complete schemas and explicit confirmation behavior
  are verified.

## Read-only commands

```bash
sat me
sat devices
sat devices --param state=alloc-available --limit 20
sat devices --limit 100 --all-pages
sat setup <sat-device-name>
sat details <sat-device-name>
sat allocations
sat allocation <reservation-id>
sat workflows
sat workflow-runs --limit 20
sat workflow-run <run-id>
sat pipelines
sat pipeline-runs --limit 20
sat pipeline-run <run-id>
sat power-rows <sat-device-name>
sat power-history <sat-device-name>
sat power-actions
sat power-off-notices
sat get /api/v1/component-update/firmware
```

`--param KEY=VALUE` can be repeated to pass documented API query parameters.
`--limit` is capped at 1000, `--cursor` passes an explicit page cursor, and
`--all-pages` follows same-origin SAT `Link` pagination (up to 100 pages).
Responses are JSON. `--timeout` is bounded to 1-60 seconds.

`sat details` reads a setup, its card list and its firmware telemetry. Its
`device`, `cards` and `firmware` sections each contain `ok` and either `data`
or `error`; partial results are printed but exit nonzero. The firmware result is
limited to the requested setup. The timestamp records when the snapshot was
requested; it is not proof of current hardware state. Authentication failures
and rate limits stop the remaining reads.

The same queries can be invoked via `mctl sat ...` and `pilot sat ...`. SAT
device names are the `mctl` target's host name, lowercased without a domain
(target `dsk61` → host `SA-DSK61-CSR` → SAT `sa-dsk61-csr`), so
`pilot -t <target> sat setup|details` needs no mapping. The
optional machine-pilot `sat_setup` field overrides this only for exceptions.

## Allocation and machine-operation rules

Use `sat allocations` and the station's live host probe to establish current
ownership before touching a lab machine. SAT lookup errors, inaccessible
setups, and stale snapshots mean **unknown**, never **free**. Honor another
person's allocation or active host holders as observe-only unless the user
explicitly authorizes the operation.

Do not automatically allocate/release setups, launch jobs, alter workflow
definitions, stop runs, or power/reset machines. These operations have real
lab impact and require complete payload/permission knowledge and explicit
user authorization. SAT documents `409` conflicts, `429` rate limits (honor
`Retry-After`), and `502`/`503` downstream/service failures; report failures
instead of turning them into a success-shaped empty result.

## API reference and gaps

The Swagger overview reviewed for this integration describes API v1 and documents:

- `GET /api/v1/devices` and `/api/v1/devices/{name}` for setups.
- `GET /api/v1/allocations` and reservation detail.
- `GET /api/v1/workflows`, workflow runs, pipelines, and pipeline runs.
- Power rows/history/activity endpoints.
- `POST` allocation, run, release, and power routes; these are not exposed by
  the current read-only client.

For exact request/response fields, use the authenticated
`https://sat.elmnt.local/api/docs/json` specification. Never guess write
payloads from route names or a collapsed Swagger schema.

## Console integration

See [Console and portal integration](references/console-integration.md) for
the browser-session-only SAT console API boundary and the multi-pane/broadcast
design exploration. Do not treat API-token access as permission to open a UART
or send console input.
