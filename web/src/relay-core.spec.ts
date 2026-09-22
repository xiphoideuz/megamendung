import { describe, expect, it } from "vitest";
import {
  decideBinding,
  hashCode,
  routeFrame,
  splitCode,
  type Room,
} from "./relay-core";

const room = (hash: string | null): Room => ({ secretHash: hash });

describe("splitCode", () => {
  it("splits pair_id and secret", () => {
    expect(splitCode("ab12cd34:sekret")).toEqual({ pairId: "ab12cd34", secret: "sekret" });
  });
  it("rejects malformed codes", () => {
    expect(() => splitCode("nope")).toThrow();
    expect(() => splitCode(":s")).toThrow();
    expect(() => splitCode("x:")).toThrow();
  });
});

describe("hashCode", () => {
  it("produces a stable 64-hex hash", async () => {
    const h = await hashCode("ab12cd34:sekret");
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    expect(await hashCode("ab12cd34:sekret")).toBe(h);
    expect(await hashCode("ab12cd34:sekret2")).not.toBe(h);
  });
});

describe("decideBinding - agent (TOFU)", () => {
  it("allows the very first agent to claim an empty room", () => {
    expect(decideBinding(room(null), "agent", "HASH", false, false)).toEqual({
      ok: true,
      role: "agent",
    });
  });
  it("allows a reconnecting agent with the matching secret", () => {
    expect(decideBinding(room("HASH"), "agent", "HASH", true, true)).toEqual({
      ok: true,
      role: "agent",
    });
  });
  it("rejects an agent with the wrong secret", () => {
    const d = decideBinding(room("HASH"), "agent", "NOPE", false, false);
    expect(d.ok).toBe(false);
  });
});

describe("decideBinding - client", () => {
  it("rejects clients before any device is paired", () => {
    const d = decideBinding(room(null), "client", "HASH", false, false);
    expect(d.ok).toBe(false);
  });
  it("rejects clients with the wrong secret", () => {
    const d = decideBinding(room("HASH"), "client", "NOPE", true, true);
    expect(d.ok).toBe(false);
  });
  it("accepts a client with the matching secret", () => {
    expect(decideBinding(room("HASH"), "client", "HASH", true, true)).toEqual({
      ok: true,
      role: "client",
    });
  });
});

describe("routeFrame", () => {
  it("pong's pings back to the sender", () => {
    expect(routeFrame({ type: "ping" }, "agent", true)).toBe("pong-src");
    expect(routeFrame({ type: "ping" }, "client", true)).toBe("pong-src");
  });
  it("broadcasts agent replies/events to clients", () => {
    expect(routeFrame({ type: "reply", id: "1", ok: true }, "agent", true)).toBe("clients");
    expect(routeFrame({ type: "event", name: "job" }, "agent", true)).toBe("clients");
    expect(routeFrame({ type: "reply" }, "agent", false)).toBe("ignore");
  });
  it("forwards client RPC frames to the agent", () => {
    expect(routeFrame({ id: "1", method: "df", params: {} }, "client", true)).toBe("agent");
  });
  it("ignores junk from clients", () => {
    expect(routeFrame({ hello: "x" }, "client", true)).toBe("ignore");
  });
});