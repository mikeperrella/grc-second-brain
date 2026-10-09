import { describe, expect, it } from "vitest";
import contract from "./health-contract.json";
import {
  failureKindSchema,
  fetchHealth,
  healthErrorSchema,
  healthReplySchema,
  parseHealthRejection,
  parseHealthResult,
} from "./health";

const INVALID = { state: "failed", source: "ipc", code: "invalid_response" };
const REJECTED = { state: "failed", source: "ipc", code: "rejected" };

// The names the schema accepts, read from the schema itself (not from a second hand-kept list).
const schemaKinds = failureKindSchema.options.map((o) => o.shape.kind.value);
const schemaStatuses = healthReplySchema.options.map((o) => o.shape.status.value);
const schemaErrors = healthErrorSchema.options.map((o) => o.shape.error.value);

describe("the contract fixture written by the Rust test", () => {
  it("is not empty, so the checks below cannot pass over nothing", () => {
    expect(contract.replies.length).toBeGreaterThan(20);
    expect(contract.errors.length).toBe(5);
  });

  it("parses every reply Rust can send, none as invalid", () => {
    for (const reply of contract.replies) {
      expect(parseHealthResult(reply), JSON.stringify(reply)).not.toEqual(INVALID);
      expect(healthReplySchema.safeParse(reply).success).toBe(true);
    }
  });

  it("parses every error Rust can send, none as rejected", () => {
    for (const error of contract.errors) {
      expect(parseHealthRejection(error), JSON.stringify(error)).not.toEqual(REJECTED);
      expect(healthErrorSchema.safeParse(error).success).toBe(true);
    }
  });

  it("names exactly the failure kinds, statuses and errors the schema names", () => {
    const fixtureKinds = new Set<string>();
    const fixtureStatuses = new Set<string>();
    for (const reply of contract.replies) {
      // `parse` throws (failing the test) if a fixture entry is not a valid reply.
      const parsed = healthReplySchema.parse(reply);
      fixtureStatuses.add(parsed.status);
      if (parsed.status === "failed") {
        fixtureKinds.add(parsed.reason.kind);
      }
    }
    const fixtureErrors = contract.errors.map((e) => e.error);

    expect([...fixtureKinds].sort()).toEqual([...schemaKinds].sort());
    expect([...fixtureStatuses].sort()).toEqual([...schemaStatuses].sort());
    expect([...fixtureErrors].sort()).toEqual([...schemaErrors].sort());
  });
});

describe("parseHealthResult: valid replies", () => {
  it("maps each reply to its view", () => {
    expect(parseHealthResult({ status: "starting" })).toEqual({ state: "starting" });
    expect(parseHealthResult({ status: "healthy" })).toEqual({ state: "healthy" });
    expect(parseHealthResult({ status: "not_running" })).toEqual({
      state: "failed",
      source: "stopped",
      code: "not_running",
    });
    expect(
      parseHealthResult({ status: "failed", reason: { kind: "timeout" } }),
    ).toEqual({ state: "failed", source: "sidecar", code: "timeout" });
  });

  it("shows only the kind of an early exit, never the exit code", () => {
    for (const code of [3, null, -2147483648, 2147483647]) {
      expect(
        parseHealthResult({
          status: "failed",
          reason: { kind: "exited_before_ready", code },
        }),
      ).toEqual({
        state: "failed",
        source: "sidecar",
        code: "exited_before_ready",
      });
    }
  });
});

describe("parseHealthResult: invalid replies are invalid_response", () => {
  const cases: [string, unknown][] = [
    ["unknown status", { status: "degraded" }],
    ["unknown failure kind", { status: "failed", reason: { kind: "exploded" } }],
    ["extra field on a reply", { status: "healthy", extra: 1 }],
    ["a token field on a reply", { status: "healthy", token: "0".repeat(64) }],
    ["a detail field on a reply", { status: "healthy", detail: "text" }],
    ["extra field on failed", { status: "failed", reason: { kind: "timeout" }, detail: "x" }],
    ["extra field on a reason", { status: "failed", reason: { kind: "timeout", detail: "x" } }],
    ["a token field on a reason", { status: "failed", reason: { kind: "timeout", token: "t" } }],
    [
      "extra field on exited_before_ready",
      { status: "failed", reason: { kind: "exited_before_ready", code: 3, path: "C:\\x" } },
    ],
    ["a nested detail object", { status: "failed", reason: { kind: "timeout", detail: { a: 1 } } }],
    ["missing status", {}],
    ["missing reason", { status: "failed" }],
    ["missing kind", { status: "failed", reason: {} }],
    ["missing exit code", { status: "failed", reason: { kind: "exited_before_ready" } }],
    ["status of the wrong type", { status: 1 }],
    ["reason of the wrong type", { status: "failed", reason: "timeout" }],
    ["exit code as a string", { status: "failed", reason: { kind: "exited_before_ready", code: "3" } }],
    ["fractional exit code", { status: "failed", reason: { kind: "exited_before_ready", code: 1.5 } }],
    ["exit code above i32", { status: "failed", reason: { kind: "exited_before_ready", code: 2147483648 } }],
    ["exit code below i32", { status: "failed", reason: { kind: "exited_before_ready", code: -2147483649 } }],
    ["null", null],
    ["undefined", undefined],
    ["an array", [{ status: "healthy" }]],
    ["an array of the status word", ["healthy"]],
    ["a string", "healthy"],
    ["a number", 1],
    ["an error shape on the resolve channel", { error: "timeout" }],
  ];
  for (const [name, raw] of cases) {
    it(name, () => {
      expect(parseHealthResult(raw)).toEqual(INVALID);
    });
  }

  it("a JSON object that carries __proto__ is not healthy", () => {
    const raw: unknown = JSON.parse('{"__proto__":{"x":1},"status":"healthy"}');
    expect(parseHealthResult(raw)).toEqual(INVALID);
  });

  it("never throws, even for a value whose properties throw", () => {
    const hostile = new Proxy(
      {},
      {
        get() {
          throw new Error("boom");
        },
        ownKeys() {
          throw new Error("boom");
        },
        getPrototypeOf() {
          throw new Error("boom");
        },
      },
    );
    expect(() => parseHealthResult(hostile)).not.toThrow();
    expect(parseHealthResult(hostile)).toEqual(INVALID);
  });
});

describe("parseHealthRejection", () => {
  it("maps each HealthError to a health_check failure", () => {
    for (const error of schemaErrors) {
      expect(parseHealthRejection({ error })).toEqual({
        state: "failed",
        source: "health_check",
        code: error,
      });
    }
  });

  // Everything that is not exactly a HealthError is `rejected`, never healthy: one test per case.
  const rejectedCases: [string, unknown][] = [
    ["the string a refused command gives", "command sidecar_health not allowed by ACL"],
    ["an Error object", new Error("refused")],
    ["null", null],
    ["undefined", undefined],
    ["a healthy reply shape", { status: "healthy" }],
    ["a starting reply shape", { status: "starting" }],
    ["an unknown error name", { error: "unknown_error" }],
    ["an extra field", { error: "timeout", extra: 1 }],
    ["a token field", { error: "timeout", token: "t" }],
    ["a detail field", { error: "timeout", detail: "text" }],
    ["a nested detail object", { error: "timeout", detail: { a: 1 } }],
    ["a missing error key", {}],
    ["an error name of the wrong type", { error: 1 }],
    ["an array", ["timeout"]],
    ["a number", 42],
  ];
  for (const [name, raw] of rejectedCases) {
    it(`calls ${name} rejected`, () => {
      expect(parseHealthRejection(raw)).toEqual(REJECTED);
    });
  }

  it("never throws for a hostile value", () => {
    const hostile = new Proxy(
      {},
      {
        get() {
          throw new Error("boom");
        },
        ownKeys() {
          throw new Error("boom");
        },
        getPrototypeOf() {
          throw new Error("boom");
        },
      },
    );
    expect(() => parseHealthRejection(hostile)).not.toThrow();
    expect(parseHealthRejection(hostile)).toEqual(REJECTED);
  });
});

describe("fetchHealth", () => {
  it("reads a resolved reply", async () => {
    await expect(fetchHealth(() => Promise.resolve({ status: "healthy" }))).resolves.toEqual({
      state: "healthy",
    });
  });

  it("reads a rejected HealthError", async () => {
    await expect(
      fetchHealth(() => Promise.reject({ error: "unreachable" })),
    ).resolves.toEqual({ state: "failed", source: "health_check", code: "unreachable" });
  });

  it("an error shape that RESOLVES is invalid_response", async () => {
    await expect(fetchHealth(() => Promise.resolve({ error: "timeout" }))).resolves.toEqual(INVALID);
  });

  it("a reply shape that REJECTS is rejected, not healthy", async () => {
    await expect(fetchHealth(() => Promise.reject({ status: "healthy" }))).resolves.toEqual(REJECTED);
  });

  it("never rejects: a string, an Error, a hostile object and a synchronous throw all give rejected", async () => {
    const calls: (() => Promise<unknown>)[] = [
      () => Promise.reject("not allowed"),
      () => Promise.reject(new Error("boom")),
      () => Promise.reject(new Proxy({}, { get() { throw new Error("boom"); } })),
      () => {
        throw new Error("thrown before a promise existed");
      },
    ];
    for (const call of calls) {
      await expect(fetchHealth(call)).resolves.toEqual(REJECTED);
    }
  });
});
