/**
 * RelayDO - the "pair room". One Durable Object is named after a pair_id
 * (``/relay/<pair_id>``), so every pairing is fully isolated.
 *
 * Room lifecycle (trust-on-first-use):
 *   1. The machine (agent) dials out, presents ``pair:secret`` -> binds, the
 *      SHA-256 of the code is persisted in the DO.
 *   2. A browser (client) presents the same code -> joins the room.
 *   3. The DO fans RPC frames from clients to the agent and reply/event
 *      frames from the agent to every connected client.
 */
import { decideBinding, hashCode, routeFrame, splitCode, type Role } from "./relay-core";

export interface Env {
  RELAY: DurableObjectNamespace;
  PAIRS: KVNamespace;
  ASSETS: Fetcher;
}

interface Hello {
  type: "hello";
  role: Role;
  code: string;
  name?: string;
}

export class RelayDO {
  private agent: WebSocket | null = null;
  private agentName: string | null = null;
  private clients = new Set<WebSocket>();
  private secretHash: string | null = null;
  private loaded = false;

  constructor(
    private state: DurableObjectState,
    private env: Env,
  ) {}

  private async getHash(): Promise<string | null> {
    if (!this.loaded) {
      this.secretHash = (await this.state.storage.get<string>("secret_hash")) ?? null;
      this.loaded = true;
    }
    return this.secretHash;
  }

  private send(ws: WebSocket, obj: unknown): void {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  private broadcast(obj: unknown, except?: WebSocket): void {
    for (const c of this.clients) {
      if (c !== except) this.send(c, obj);
    }
  }

  async fetch(request: Request): Promise<Response> {
    const upgrade = request.headers.get("Upgrade");
    if (upgrade?.toLowerCase() !== "websocket") {
      return new Response("relay expects a WebSocket upgrade", { status: 426 });
    }
    const pair = new WebSocketPair();
    const server = pair[1];
    server.accept();

    const pairId = this.state.id.name;
    if (!pairId) {
      return new Response("missing pair id", { status: 400 });
    }
    let role: Role | null = null;

    server.addEventListener("message", async (ev) => {
      let frame: any;
      try {
        frame = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data));
      } catch {
        return;
      }
      const currentRole = role;
      if (currentRole === null) {
        if (frame?.type === "hello") {
          const bound = await this.bindPeer(pairId, server, frame);
          if (bound === "agent") role = "agent";
          else if (bound === "client") role = "client";
        }
        return;
      }
      this.route(pairId, server, currentRole, frame);
    });

    server.addEventListener("close", () => {
      if (server === this.agent) {
        this.agent = null;
        this.agentName = null;
        this.broadcast({ type: "event", name: "agent_status", online: false });
      } else {
        this.clients.delete(server);
      }
    });

    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  private async bindPeer(pairId: string, server: WebSocket, hello: Hello): Promise<Role | null> {
    let hash: string;
    try {
      const { pairId: cid, secret } = splitCode(hello.code);
      if (cid !== pairId) {
        throw new Error("pair id in code does not match this room");
      }
      hash = await hashCode(`${cid}:${secret}`);
    } catch (err) {
      this.send(server, { type: "error", error: String(err) });
      server.close(1008, "bad pairing code");
      return null;
    }

    const room = { secretHash: await this.getHash() };
    const decision = decideBinding(room, hello.role as Role, hash, this.agent?.readyState === WebSocket.OPEN, this.clients.size > 0);

    if (!decision.ok) {
      this.send(server, { type: "error", error: decision.error });
      server.close(1008, "forbidden");
      return null;
    }

    if (hello.role === "client") {
      this.clients.add(server);
      this.send(server, {
        type: "hello",
        reply: true,
        you: "client",
        pair_id: pairId,
        agent: this.agentName,
        agent_online: this.agent?.readyState === WebSocket.OPEN,
      });
      return "client";
    }
    if (!room.secretHash) {
      await this.state.storage.put("secret_hash", hash);
      this.secretHash = hash;
    } else if (room.secretHash !== hash) {
      // a different (already used) secret tried to take over - reject
      this.send(server, { type: "error", error: "forbidden: pairing code does not match this room" });
      server.close(1008, "forbidden");
      return null;
    }
    if (this.agent && this.agent !== server && this.agent.readyState === WebSocket.OPEN) {
      this.send(this.agent, { type: "shutdown", reason: "device re-linked" });
      this.agent.close(4000, "re-linked");
    }
    this.agent = server;
    this.agentName = hello.name || "megamendung-device";
    this.send(server, {
      type: "hello",
      reply: true,
      you: "agent",
      pair_id: pairId,
      clients: this.clients.size,
    });
    this.broadcast({
      type: "event",
      name: "agent_status",
      online: true,
      agent: this.agentName,
    });
    return "agent";
  }

  private route(pairId: string, server: WebSocket, role: Role, frame: any): void {
    const sink = routeFrame(frame, role, this.clients.size > 0);
    if (sink === "pong-src") {
      this.send(server, { type: "pong", t: frame?.t ?? null });
      return;
    }
    if (sink === "clients") {
      this.broadcast(frame);
      return;
    }
    if (sink === "agent" && this.agent?.readyState === WebSocket.OPEN) {
      this.agent.send(JSON.stringify(frame));
      return;
    }
  }
}