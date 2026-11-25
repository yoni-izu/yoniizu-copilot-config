# SAT console and portal integration

## What the API overview establishes

The SAT Swagger overview lists:

- `GET /api/v1/devices/{name}/console/targets`
- `POST /api/v1/devices/{name}/console/tickets`

The console section explicitly says these endpoints are **browser-session-only**
and describes UART data as WebSocket streamed. Therefore the API-token based
`sat` CLI cannot obtain console tickets or act as a terminal client. The
provided PDF does not show the WebSocket protocol, ticket lifetime/permissions,
or whether server-side broadcast is supported.

## Multi-pane and broadcast idea

Treat this as a future Team Portal feature, not an `mctl` shell feature:

1. A portal-owned view could let a user select several SAT console targets and
   arrange one console per pane.
2. The browser would need an approved SAT-authenticated session to request
   tickets and open each documented WebSocket; do not relay a personal API token
   through the portal terminal or expose it to JavaScript.
3. Optional input broadcast should be opt-in, visibly armed, and limited to
   explicitly selected panes. A single keystroke can affect multiple boards;
   require a deliberate confirmation before enabling it and provide a clear
   disarm control.
4. Confirm with the SAT maintainers whether console tickets can be issued to an
   existing portal session, how ticket lifetime and revocation work, whether
   multi-target access is allowed, and whether SAT has a supported broadcast
   contract. If not, the portal should implement fan-out over separately
   authorized WebSockets rather than inventing a SAT API endpoint.
5. Keep the existing `pilot` one-reader-per-UART and station-allocation rules.
   The portal must not silently open duplicate readers or send input to an
   allocated setup.

This note records feasibility boundaries only. No portal code, SAT session,
console ticket, WebSocket, or machine operation is created by the CLI work.
