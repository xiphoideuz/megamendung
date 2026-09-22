# megamendung web — GUI relay (Cloudflare Worker)

WhatsApp-Web-style browser GUI for the `megamendung` CLI:

- Serves a single-page dashboard (Tailwind via CDN, no build step).
- A `RelayDO` Durable Object hosts one isolated "pair room" per `pair_id` and
  relays JSON frames between the browser and the `megamendung connect` agent.
- Storage (rclone) and compression (ffmpeg) always run on the machine; the
  Worker is stateless apart from each room's pairing secret hash.

## Deploy

```sh
cd web
npm install
npx wrangler kv namespace create PAIRS        # once; paste the id into wrangler.toml
npm run deploy
```

Local preview (Node proxy; use a free port):

```sh
npm run dev                 # wrangler dev (port 8787 is often taken - use --port 8790)
```

`wrangler dev` binds the port in the Node proxy, which dies with the launching
shell - use `setsid nohup npx wrangler dev --port 8790 < /dev/null & disown`
when you need it to survive.

## Source

- `src/index.ts` — request router (static assets first, then `/api/health`,
  then `/relay/<pair_id>` upgrades).
- `src/relay.ts` — the `RelayDO`: TOFU pairing, per-room secret hash, fan-out.
- `src/relay-core.ts` — pure pairing/ROUTING logic (no Workers APIs),
  unit-tested by `src/relay-core.spec.ts` under plain vitest.
- `public/index.html`, `public/app.js` — the SPA (no build step).

## Protocol (JSON frames over WebSocket)

| direction | frame | meaning |
|---|---|---|
| both -> worker | `{"type":"hello","role":"agent\|client","code":"<pair>:<secret>"}` | pair on first message |
| worker -> agent | `{"type":"hello","reply":true,"you":"agent","pair_id":...}` | bound |
| worker -> client | `{"type":"hello","reply":true,"you":"client","agent_online":bool,...}` | bound |
| client -> agent | `{"id","method","params"}` | RPC request |
| agent -> clients | `{"type":"reply","id","ok","result\|error"}` | RPC response |
| agent -> clients | `{"type":"event","name":"agent_status\|job",...}` | streaming progress |
| either          | `{"type":"ping"}` -> `{"type":"pong"}` | keepalive (every 25s) |

The first agent to present a code binds the room (the SHA-256 of the code is
stored in the Durable Object). Clients must present the same code.

## Tests

```sh
npm test          # vitest (relay-core)
npm run typecheck # tsc --noEmit
```

End-to-end: run `wrangler dev --port 8790`, then
`python3 /tmp/opencode/webint/test_webint.py` (drives the real
`megamendung connect` agent through the room and asserts RPC round trips).