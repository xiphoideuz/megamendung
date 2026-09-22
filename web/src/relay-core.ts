/** Pure relay/room logic - kept free of Workers APIs so it is unit-testable
 *  with plain vitest. The Durable Object adapter (`relay.ts`) only glues this
 *  to WebSockets. */

export type Role = "agent" | "client";

export interface Room {
  /** SHA-256 hex of the pairing code, set the first time an agent binds
   *  (trust-on-first-use: the code itself is the credential). */
  secretHash: string | null;
}

export async function hashCode(code: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(code));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function splitCode(code: string): { pairId: string; secret: string } {
  if (typeof code !== "string") throw new Error("invalid pairing code");
  const idx = code.indexOf(":");
  if (idx <= 0 || idx === code.length - 1) {
    throw new Error("invalid pairing code; expected <pair_id>:<secret>");
  }
  return { pairId: code.slice(0, idx), secret: code.slice(idx + 1) };
}

export type BindDecision =
  | { ok: true; role: Role }
  | { ok: false; error: string };

export function decideBinding(
  room: Room,
  role: Role,
  hash: string,
  agentOnline: boolean,
  hasClients: boolean,
): BindDecision {
  if (role === "client") {
    if (!room.secretHash) {
      return {
        ok: false,
        error:
          "no device paired yet: run `megamendung pair`, then `megamendung connect` on the machine first",
      };
    }
    if (room.secretHash !== hash) {
      return { ok: false, error: "forbidden: pairing code does not match this room" };
    }
    return { ok: true, role: "client" };
  }
  // agent
  if (room.secretHash && room.secretHash !== hash) {
    return { ok: false, error: "forbidden: pairing code does not match this room" };
  }
  // any verified secret claim (including the very first, TOFU) may bind as agent
  return { ok: true, role: "agent" };
}

export type FrameSink = "agent" | "clients" | "pong-src" | "ignore";

/** Routing decision for one frame received from a peer. */
export function routeFrame(
  frame: any,
  from: Role,
  hasClients: boolean,
): FrameSink {
  const t = frame?.type;
  if (t === "ping") return "pong-src";
  if (t === "pong" || t === "hello" || t === "event" && from !== "agent") return "ignore";
  if (from === "agent") {
    if (t === "event" || t === "reply") return hasClients ? "clients" : "ignore";
    return "ignore";
  }
  // from a client: only well-formed RPC frames reach the agent
  if (typeof frame?.id === "string" && typeof frame?.method === "string") return "agent";
  return "ignore";
}