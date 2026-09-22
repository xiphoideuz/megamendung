/** megamendung web - the GUI relay worker.
 *
 * Serves the static SPA (via the [assets] binding), a tiny health endpoint,
 * and upgrades ``/relay/<pair_id>`` sockets into a per-pairing RelayDO for
 * WhatsApp-Web-style device pairing with the ``megamendung connect`` agent.
 */
import { RelayDO, type Env } from "./relay";

const PAIR_RE = /^[A-Za-z0-9_]{1,64}$/;

export { RelayDO };

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // 1. static SPA / assets
    const asset = await env.ASSETS.fetch(request);
    if (asset.status !== 404) return asset;

    // 2. health / API
    if (url.pathname === "/api/health") {
      return json({
        ok: true,
        service: "megamendung-relay",
        docs: "pair with `megamendung pair` + `megamendung connect <this-origin>`",
      });
    }

    // 3. relay upgrade
    if (url.pathname.startsWith("/relay/")) {
      const pairId = url.pathname.slice("/relay/".length).split("/")[0];
      if (!PAIR_RE.test(pairId)) return json({ error: "bad pair id" }, 400);
      const stub = env.RELAY.get(env.RELAY.idFromName(pairId));
      return stub.fetch(request);
    }

    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}