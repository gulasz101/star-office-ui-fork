// Contract tests for the Star Office UI reporter plugin (SOU-71 / P6-A).
// Run: node --test reporter/openchamber-star-office-reporter/test/
// The module under test is one self-contained ESM file; the suite exercises
// the pure core (config, envelope, privacy, transport) exactly the way the
// OpenCode adapter will use it.

// a HOME with no config file in it: the suite never reads the developer's own ~/.config/opencode
const NO_HOME = "/nonexistent-sou-home";

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULTS,
  PLUGIN_ID,
  loadConfig,
  buildEnvelope,
  redactPayload,
  classifyTool,
  createReporter,
} from "../openchamber-star-office-reporter.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Locate a Star-Office-UI checkout for the Python validator: explicit env,
// then nearest ancestor containing backend/event_schema.py (works inside the
// fork monorepo and from any checkout of this repo placed beside one).
const REPO_ROOT = (() => {
  if (process.env.SOU_REPO_ROOT) return process.env.SOU_REPO_ROOT;
  let dir = HERE;
  for (let i = 0; i < 6; i++) {
    if (existsSync(path.join(dir, "backend", "event_schema.py"))) return dir;
    dir = path.dirname(dir);
  }
  return path.resolve(HERE, "..", "..", "..");
})();
const BACKEND = path.join(REPO_ROOT, "backend", "event_schema.py");

// ---------------------------------------------------------------------------
// AC4 — configuration
// ---------------------------------------------------------------------------

test("loadConfig: safe defaults with no env", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  assert.equal(cfg.dashboardUrl, DEFAULTS.dashboardUrl);
  assert.equal(cfg.agent, "openchamber"); // AC4 default agent name
  assert.equal(cfg.timeoutMs, 5000); // AC4 recommended timeout
  assert.equal(cfg.enabled, true);
  assert.equal(typeof cfg.maxFieldChars, "number");
  assert.equal(cfg.maxPayloadBytes, 65536);
});

test("loadConfig: env overrides (SOU_DASHBOARD_URL, SOU_AGENT_NAME)", () => {
  const cfg = loadConfig({ HOME: NO_HOME,
    SOU_DASHBOARD_URL: "http://192.168.1.119:19000/",
    SOU_AGENT_NAME: "opencode",
    SOU_TIMEOUT_MS: "2500",
  });
  assert.equal(cfg.dashboardUrl, "http://192.168.1.119:19000"); // trailing slash trimmed
  assert.equal(cfg.eventsUrl, "http://192.168.1.119:19000/events");
  assert.equal(cfg.agent, "opencode"); // issue note: behind OpenChamber report as opencode
  assert.equal(cfg.timeoutMs, 2500);
});

test("loadConfig: enable/disable reporting", () => {
  for (const off of ["0", "false", "no", "off"]) {
    assert.equal(loadConfig({ HOME: NO_HOME, SOU_REPORTER_ENABLED: off }).enabled, false, off);
  }
  for (const on of ["1", "true", "yes", "on", ""]) {
    assert.equal(loadConfig({ HOME: NO_HOME, SOU_REPORTER_ENABLED: on }).enabled, true, on);
  }
});

test("loadConfig: bad timeout falls back, sane bounds", () => {
  assert.equal(loadConfig({ HOME: NO_HOME, SOU_TIMEOUT_MS: "abc" }).timeoutMs, 5000);
  assert.equal(loadConfig({ HOME: NO_HOME, SOU_TIMEOUT_MS: "-10" }).timeoutMs, 5000);
  assert.equal(loadConfig({ HOME: NO_HOME, SOU_TIMEOUT_MS: "999999" }).timeoutMs, DEFAULTS.maxTimeoutMs);
  assert.equal(loadConfig({ HOME: NO_HOME, SOU_TIMEOUT_MS: "800" }).timeoutMs, 800);
});

// ---------------------------------------------------------------------------
// AC2 — event schema v1 envelope
// ---------------------------------------------------------------------------

test("buildEnvelope: exact schema v1 envelope shape", () => {
  const cfg = loadConfig({ HOME: NO_HOME, SOU_AGENT_NAME: "openchamber" });
  const env = buildEnvelope(cfg, "tool_call", { tool: "bash", status: "completed" }, new Date("2026-10-14T10:00:00Z"));
  assert.deepEqual(Object.keys(env).sort(), ["agent", "event_type", "payload", "timestamp", "version"]);
  assert.equal(env.version, 1);
  assert.equal(env.agent, "openchamber");
  assert.equal(env.event_type, "tool_call");
  assert.equal(env.timestamp, "2026-10-14T10:00:00.000Z");
  assert.equal(env.payload.tool, "bash");
});

test("buildEnvelope: rejects empty agent and unknown event type strings", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  assert.throws(() => buildEnvelope(cfg, "", {}), /event_type/);
  assert.throws(() => buildEnvelope({ ...cfg, agent: " " }, "tool_call", {}), /agent/);
});

// The strongest schema proof available: hand real generated envelopes to the
// dashboard's own Python validator (backend/event_schema.py, ADR-035).
test("buildEnvelope: accepted by backend event_schema.validate_event", { skip: !existsSync(BACKEND) && "backend validator not present" }, () => {
  const cfg = loadConfig({ HOME: NO_HOME, SOU_AGENT_NAME: "openchamber" });
  const envelopes = [
    buildEnvelope(cfg, "session_start", { directory: "/tmp/x" }),
    buildEnvelope(cfg, "tool_call", { tool: "bash", status: "completed", duration_ms: 12 }),
    buildEnvelope(cfg, "file_edit", { tool: "write", file: "a.txt", status: "completed" }),
    buildEnvelope(cfg, "command_run", { command: "echo hi", exit_code: 0 }),
    buildEnvelope(cfg, "code_completion", { files_touched: ["a.txt"] }),
    buildEnvelope(cfg, "session_end", { duration_seconds: 3, events_logged: 5 }),
  ];
  const dir = mkdtempSync(path.join(tmpdir(), "sou71-"));
  try {
    const file = path.join(dir, "envelopes.ndjson");
    writeFileSync(file, envelopes.map((e) => JSON.stringify(e)).join("\n") + "\n");
    const out = execFileSync(
      "python3",
      [
        "-c",
        `import json, sys
sys.path.insert(0, "backend"); sys.path.insert(0, ".tooling/site")
import event_schema
n = 0
for line in open(sys.argv[1], encoding="utf-8"):
    if line.strip():
        event_schema.validate_event(json.loads(line)); n += 1
print("VALID", n)`,
        file,
      ],
      { cwd: REPO_ROOT, encoding: "utf8" }
    );
    assert.match(out, /VALID 6/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

function fileExistsSync(p) {
  return existsSync(p);
}

// ---------------------------------------------------------------------------
// AC5 — privacy filters
// ---------------------------------------------------------------------------

test("redactPayload: secret-shaped values are scrubbed from strings", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const cases = [
    ["token sk-ABCDEFGHIJKLMNOP1234 done", /sk-[A-Za-z0-9]{8,}/],
    ["export GITHUB_TOKEN=ghp_16C4e9Vj0Yq0M2r8Tt6ZaBcDeFgHiJkLmNoP", /ghp_[A-Za-z0-9]+/], // gitleaks:allow
    ["aws key AKIA1234567890ABCDEF in output", /AKIA[0-9A-Z]+/], // gitleaks:allow
    ["Authorization: Bearer abc.def_123-xyz~+/", /Bearer\s+[A-Za-z0-9]/],
    ['call with api_key="topsecret-value-1" now', /topsecret-value-1/],
    ["password: hunter2; continue", /hunter2/],
    ["jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.abcDEF123_g.hijKLMN456", /eyJhbGci/],
  ];
  for (const [input, leaky] of cases) {
    const out = redactPayload({ output: input }, cfg).output;
    assert.ok(!leaky.test(out), `expected redaction of ${leaky} in: ${out}`);
    assert.match(out, /\[redacted\]/);
  }
});

test("redactPayload: secret-named keys drop their values anywhere in the tree", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const input = {
    api_key: "super-secret-1",
    nested: { Authorization: "Bearer whatever", token: "t0k3n" },
    list: [{ PASSWORD: "p", harmless: "ok" }],
    safe: "keep me",
  };
  const out = redactPayload(input, cfg);
  assert.equal(out.api_key, "[redacted]");
  assert.equal(out.nested.Authorization, "[redacted]");
  assert.equal(out.nested.token, "[redacted]");
  assert.equal(out.list[0].PASSWORD, "[redacted]");
  assert.equal(out.list[0].harmless, "ok");
  assert.equal(out.safe, "keep me");
});

test("redactPayload: long outputs are truncated", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const huge = "x".repeat(100_000);
  const out = redactPayload({ output: huge }, cfg);
  assert.ok(out.output.length <= cfg.maxFieldChars + 40, "field must be capped");
  assert.match(out.output, /\[truncated/);
});

test("redactPayload: total payload stays within the backend cap (65536)", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const fat = {};
  for (let i = 0; i < 40; i++) fat[`chunk_${i}`] = "y".repeat(8000);
  const out = redactPayload(fat, cfg);
  const bytes = Buffer.byteLength(JSON.stringify(out), "utf8");
  assert.ok(bytes <= cfg.maxPayloadBytes, `payload ${bytes} > ${cfg.maxPayloadBytes}`);
});

// ---------------------------------------------------------------------------
// AC3 — event classification
// ---------------------------------------------------------------------------

test("classifyTool: file-edit and command families", () => {
  assert.deepEqual(classifyTool("write"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("edit"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("patch"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("apply_patch"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("multi_edit"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("str_replace"), { fileEdit: true, command: false });
  assert.deepEqual(classifyTool("bash"), { fileEdit: false, command: true });
  assert.deepEqual(classifyTool("shell"), { fileEdit: false, command: true });
  assert.deepEqual(classifyTool("acme_bash"), { fileEdit: false, command: true }); // namespaced
  assert.deepEqual(classifyTool("read"), { fileEdit: false, command: false });
  assert.deepEqual(classifyTool("grep"), { fileEdit: false, command: false });
  assert.deepEqual(classifyTool(undefined), { fileEdit: false, command: false });
});

// ---------------------------------------------------------------------------
// AC2 — non-blocking transport
// ---------------------------------------------------------------------------

test("createReporter: report() is fire-and-forget and never blocks the hook", async () => {
  let release;
  const hangingFetch = () => new Promise((res) => (release = res));
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const rep = createReporter(cfg, { fetch: hangingFetch, logger: () => {} });
  const t0 = process.hrtime.bigint();
  const p = rep.report("tool_call", { tool: "bash" });
  const dtMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(dtMs < 100, `report() returned synchronously fast (took ${dtMs}ms)`);
  assert.ok(p && typeof p.then === "function", "returns a promise the caller may ignore");
  release({ ok: true, status: 201 });
  await p;
  assert.equal(rep.stats().sent, 1);
  assert.equal(rep.stats().failed, 0);
});

test("createReporter: posts v1 JSON to <dashboard>/events", async () => {
  const seen = [];
  const cfg = loadConfig({ HOME: NO_HOME, SOU_DASHBOARD_URL: "http://dash.test:19000/" });
  const rep = createReporter(cfg, {
    fetch: async (url, init) => {
      seen.push({ url, init });
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  await rep.report("command_run", { command: "ls" });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, "http://dash.test:19000/events");
  assert.equal(seen[0].init.method, "POST");
  assert.equal(seen[0].init.headers["content-type"], "application/json");
  const body = JSON.parse(seen[0].init.body);
  assert.equal(body.version, 1);
  assert.equal(body.agent, "openchamber");
  assert.equal(body.event_type, "command_run");
  assert.ok(AbortSignal.timeout !== undefined ? seen[0].init.signal : true, "timeout signal attached");
});

test("createReporter: dashboard failure never escapes as an error", async () => {
  const logs = [];
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  const rep = createReporter(cfg, {
    fetch: async () => {
      throw new Error("ETIMEDOUT connect");
    },
    logger: (m) => logs.push(m),
  });
  await rep.report("tool_call", { tool: "bash" }); // must resolve, not reject
  assert.equal(rep.stats().failed, 1);
  assert.equal(rep.stats().sent, 0);
  assert.equal(logs.length, 1);
  assert.match(logs[0], /dashboard/);
});

test("createReporter: 400 from dashboard is logged and counted", async () => {
  const logs = [];
  const rep = createReporter(loadConfig({ HOME: NO_HOME, HOME: NO_HOME }), {
    fetch: async () => ({ ok: false, status: 400 }),
    logger: (m) => logs.push(m),
  });
  await rep.report("tool_call", { tool: "bash" });
  assert.equal(rep.stats().failed, 1);
  assert.match(logs.join("\n"), /400/);
});

test("createReporter: disabled config sends nothing (enable/disable AC)", async () => {
  let calls = 0;
  const cfg = loadConfig({ HOME: NO_HOME, SOU_REPORTER_ENABLED: "0" });
  const rep = createReporter(cfg, {
    fetch: async () => {
      calls++;
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  await rep.report("tool_call", { tool: "bash" });
  assert.equal(calls, 0);
  assert.equal(rep.enabled, false);
});

test("createReporter: flush drains inflight within a bound", async () => {
  let release;
  const rep = createReporter(loadConfig({ HOME: NO_HOME, HOME: NO_HOME }), {
    fetch: () => new Promise((res) => (release = res)),
    logger: () => {},
  });
  const p = rep.report("session_end", { reason: "bye" });
  setTimeout(() => release({ ok: true, status: 201 }), 10);
  await rep.flush(1000);
  await p;
  assert.equal(rep.stats().sent, 1);
});

// ---------------------------------------------------------------------------
// SOU-223 — ingest token: configuration plumbing (AC1)
// ---------------------------------------------------------------------------

test("loadConfig: token unset by default", () => {
  const cfg = loadConfig({ HOME: NO_HOME, HOME: NO_HOME });
  assert.ok(!cfg.token, "no SOU_TOKEN, no config token -> no token");
});

test("loadConfig: SOU_TOKEN env picked up and trimmed", () => {
  const cfg = loadConfig({ HOME: NO_HOME, SOU_TOKEN: "  tok-abc123  " });
  assert.equal(cfg.token, "tok-abc123");
});

test("loadConfig: config-file token key honoured", () => {
  const cfg = loadConfig(
    { HOME: "/nonexistent", XDG_CONFIG_HOME: undefined },
    { configPath: "/virtual/star-office-reporter.json", readFile: () => JSON.stringify({ token: "file-tok" }) }
  );
  assert.equal(cfg.token, "file-tok");
});

test("loadConfig: env SOU_TOKEN overrides config-file token", () => {
  const cfg = loadConfig(
    { SOU_TOKEN: "env-tok" },
    { configPath: "/virtual/star-office-reporter.json", readFile: () => JSON.stringify({ token: "file-tok" }) }
  );
  assert.equal(cfg.token, "env-tok");
});

test("loadConfig: blank token means unset", () => {
  assert.ok(!loadConfig({ HOME: NO_HOME, SOU_TOKEN: "   " }).token);
  const cfg = loadConfig(
    {},
    { configPath: "/virtual/star-office-reporter.json", readFile: () => JSON.stringify({ token: "" }) }
  );
  assert.ok(!cfg.token);
});

// ---------------------------------------------------------------------------
// SOU-223 — ingest token: transport + redaction (AC2, AC3)
// ---------------------------------------------------------------------------

const TOK = "soutok-Unit4TestToken-abcdefghij0123456789";

test("createReporter: sends Authorization Bearer header when a token is configured", async () => {
  const seen = [];
  const cfg = loadConfig({ HOME: NO_HOME, SOU_TOKEN: TOK });
  const rep = createReporter(cfg, {
    fetch: async (url, init) => {
      seen.push(init.headers);
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  await rep.report("tool_call", { tool: "bash" });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].Authorization, `Bearer ${TOK}`);
});

test("createReporter: no Authorization header when no token is configured", async () => {
  const seen = [];
  const rep = createReporter(loadConfig({ HOME: NO_HOME, HOME: NO_HOME }), {
    fetch: async (url, init) => {
      seen.push(init.headers);
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  await rep.report("tool_call", { tool: "bash" });
  assert.equal("Authorization" in seen[0], false);
});

test("createReporter: failure lines handed to the logger never carry the plaintext token", async () => {
  const logs = [];
  const cfg = loadConfig({ HOME: NO_HOME, SOU_TOKEN: TOK });
  const rep = createReporter(cfg, {
    fetch: async () => {
      throw new Error(`connect failed with header Authorization: Bearer ${TOK} in diagnostic`);
    },
    logger: (m) => logs.push(m),
  });
  await rep.report("tool_call", { tool: "bash" });
  const all = logs.join("\n");
  assert.ok(!all.includes(TOK), `token leaked into a log line: ${all}`);
  assert.match(all, /\[redacted\]/);
});

test("createReporter: 401 without a token hints at SOU_TOKEN", async () => {
  const logs = [];
  const rep = createReporter(loadConfig({ HOME: NO_HOME, HOME: NO_HOME }), {
    fetch: async () => ({ ok: false, status: 401 }),
    logger: (m) => logs.push(m),
  });
  await rep.report("tool_call", { tool: "bash" });
  assert.match(logs.join("\n"), /401/);
  assert.match(logs.join("\n"), /SOU_TOKEN/);
});

test("redactPayload: the configured ingest token is scrubbed from payload strings", () => {
  const cfg = loadConfig({ HOME: NO_HOME, SOU_TOKEN: TOK });
  const out = redactPayload({ output: `echo ok; token is ${TOK}`, nested: { note: `${TOK} seen in output` } }, cfg);
  const s = JSON.stringify(out);
  assert.ok(!s.includes(TOK), "plaintext token must not survive the privacy pass");
  assert.match(s, /\[redacted\]/g);
});

test("redactPayload: unrelated payloads are untouched when a token is set", () => {
  const cfg = loadConfig({ HOME: NO_HOME, SOU_TOKEN: TOK });
  const out = redactPayload({ command: "ls", status: "ok" }, cfg);
  assert.deepEqual(out, { command: "ls", status: "ok" });
});

// ---------------------------------------------------------------------------
// package identity
// ---------------------------------------------------------------------------

test("plugin identity", () => {
  assert.equal(typeof PLUGIN_ID, "string");
  assert.equal(PLUGIN_ID, "openchamber-star-office-reporter");
});
