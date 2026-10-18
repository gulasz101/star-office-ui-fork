// Adapter contract tests (SOU-71 / P6-C): the OpenCode V2 plugin shape,
// driven through a mock ctx, must produce all six event types (AC3) with
// privacy filters applied in the real payload path (AC5), while never
// throwing into the host (AC2). Run: node --test test/

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { createPlugin, loadConfig, resolveRoute } from "../openchamber-star-office-reporter.js";
// a HOME with no config file in it: the suite never reads the developer's own ~/.config/opencode
const NO_HOME = "/nonexistent-sou-home";

function mockCtx() {
  const hooks = { "execute.before": [], "execute.after": [] };
  const queue = [];
  const ctx = {
    app: { version: "2.0.22-mock" },
    location: { directory: "/tmp/mock-project", project: { id: "p1" } },
    tool: {
      hook: async (name, cb) => {
        hooks[name].push(cb);
        return { dispose: async () => {} };
      },
    },
    event: {
      subscribe({ signal } = {}) {
        return {
          [Symbol.asyncIterator]() {
            return {
              async next() {
                for (let i = 0; i < 400; i++) {
                  if (signal && signal.aborted) return { done: true, value: undefined };
                  if (queue.length) return { done: false, value: queue.shift() };
                  await new Promise((r) => setTimeout(r, 5));
                }
                return { done: true, value: undefined };
              },
            };
          },
        };
      },
    },
    _hooks: hooks,
    _emit: (ev) => queue.push(ev),
  };
  return ctx;
}

function harness(env = {}) {
  const events = [];
  const logs = [];
  const plugin = createPlugin({
    config: loadConfig({ HOME: NO_HOME, SOU_DASHBOARD_URL: "http://capture.test:19000", ...env }),
    fetch: async (url, init) => {
      events.push({ url, envelope: JSON.parse(init.body) });
      return { ok: true, status: 201 };
    },
    logger: (m) => logs.push(m),
  });
  const types = () => events.map((e) => e.envelope.event_type);
  const of = (t) => events.filter((e) => e.envelope.event_type === t).map((e) => e.envelope);
  const waitFor = async (pred, ms = 3000) => {
    const t0 = Date.now();
    while (!pred()) {
      if (Date.now() - t0 > ms) throw new Error("timeout waiting for event");
      await new Promise((r) => setTimeout(r, 10));
    }
  };
  return { plugin, events, logs, types, of, waitFor };
}

test("session_start fires on setup with location context", async () => {
  const h = harness();
  const ctx = mockCtx();
  const cleanup = await h.plugin.setup(ctx);
  assert.equal(h.types()[0], "session_start");
  const ss = h.of("session_start")[0];
  assert.equal(ss.agent, "openchamber");
  assert.equal(ss.version, 1);
  assert.match(ss.timestamp, /^\d{4}-\d{2}-\d{2}T.*Z$/);
  assert.equal(ss.payload.directory, "/tmp/mock-project");
  assert.equal(typeof cleanup, "function");
  await cleanup();
});

test("full lifecycle: all six event types with privacy on the real path", async () => {
  const h = harness();
  const ctx = mockCtx();
  const cleanup = await h.plugin.setup(ctx);
  const before = ctx._hooks["execute.before"][0];
  const after = ctx._hooks["execute.after"][0];

  // a command tool, with a canary secret in the command string
  before({ tool: "bash", sessionID: "s1", callID: "c1", input: { command: "echo sk-CANARYABCDEFGHIJKLMN && ls" } });
  await after({ tool: "bash", status: "completed", sessionID: "s1", callID: "c1", result: { output: "file list here" } });

  // a file-edit tool
  before({ tool: "write", sessionID: "s1", callID: "c2", input: { filePath: "/tmp/mock-project/notes.txt", content: "hi" } });
  await after({ tool: "write", status: "completed", sessionID: "s1", callID: "c2", result: { title: "notes.txt" } });

  // an unrelated tool: tool_call only
  before({ tool: "read", sessionID: "s1", callID: "c3", input: { filePath: "x" } });
  await after({ tool: "read", status: "completed", sessionID: "s1", callID: "c3", result: {} });

  assert.deepEqual(h.types().slice(0, 7), [
    "session_start",
    "tool_call",
    "command_run",
    "tool_call",
    "file_edit",
    "tool_call",
  ]);

  const cr = h.of("command_run")[0];
  assert.equal(cr.payload.output, "file list here");
  assert.match(cr.payload.command, /\[redacted\]/); // AC5 on the live path
  assert.ok(!JSON.stringify(h.events).includes("sk-CANARYABCDEFGHIJKLMN"), "canary must not reach the dashboard");
  const fe = h.of("file_edit")[0];
  assert.equal(fe.payload.file, "/tmp/mock-project/notes.txt");
  assert.equal(fe.payload.tool, "write");
  const tc = h.of("tool_call");
  assert.equal(tc.length, 3);
  assert.equal(tc[2].payload.tool, "read");
  assert.equal(tc[0].payload.session_id, "s1");
  assert.equal(typeof tc[0].payload.duration_ms, "number");

  // session.idle after edits -> code_completion
  ctx._emit({ type: "session.idle", properties: { sessionID: "s1" } });
  await h.waitFor(() => h.types().includes("code_completion"));
  const cc = h.of("code_completion")[0];
  assert.deepEqual(cc.payload.files_touched, ["/tmp/mock-project/notes.txt"]);

  // session.deleted -> session_end
  ctx._emit({ type: "session.deleted", properties: { sessionID: "s1" } });
  await h.waitFor(() => h.types().includes("session_end"));
  const se = h.of("session_end")[0];
  assert.equal(se.payload.reason, "session_deleted");
  assert.equal(typeof se.payload.events_logged, "number");

  // unload -> final session_end + bounded flush
  await cleanup();
  const ends = h.of("session_end");
  assert.ok(ends.some((e) => e.payload.reason === "plugin_unload"));
  assert.deepEqual(h.logs, []); // nothing failed
  for (const e of h.events) {
    assert.equal(e.url, "http://capture.test:19000/events");
    assert.equal(e.envelope.version, 1);
    assert.equal(e.envelope.agent, "openchamber");
  }
});

test("failed tool status is carried, hooks never throw", async () => {
  const h = harness();
  const ctx = mockCtx();
  const cleanup = await h.plugin.setup(ctx);
  const after = ctx._hooks["execute.after"][0];
  // garbage payloads must not escape as exceptions into the host pipeline
  await after({ tool: "bash", status: "error", error: { message: "boom sk-ANOTHERSECRET123456789" } });
  await after(undefined);
  await after({});
  const tcs = h.of("tool_call");
  assert.equal(tcs[0].payload.status, "error");
  assert.match(tcs[0].payload.error, /\[redacted\]/);
  assert.ok(!JSON.stringify(h.events).includes("ANOTHERSECRET123456789"));
  await cleanup();
});

test("disabled reporting sends zero fetches but registers harmless hooks", async () => {
  const h = harness({ SOU_REPORTER_ENABLED: "0" });
  const ctx = mockCtx();
  const cleanup = await h.plugin.setup(ctx);
  ctx._hooks["execute.before"][0]({ tool: "bash", sessionID: "s1", callID: "c1", input: { command: "true" } });
  await ctx._hooks["execute.after"][0]({ tool: "bash", status: "completed", sessionID: "s1", callID: "c1", result: {} });
  ctx._emit({ type: "session.idle", properties: { sessionID: "s1" } });
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(h.events.length, 0);
  await cleanup();
});

test("namespaced tools classify correctly through the adapter", async () => {
  const h = harness();
  const ctx = mockCtx();
  const cleanup = await h.plugin.setup(ctx);
  ctx._hooks["execute.before"][0]({ tool: "acme_bash", callID: "n1", input: { command: "pwd" } });
  await ctx._hooks["execute.after"][0]({ tool: "acme_bash", status: "completed", callID: "n1", result: { output: "/tmp" } });
  assert.ok(h.types().includes("command_run"));
  await cleanup();
});

// ---------------------------------------------------------------------------
// SOU-223 — token wiring through the real plugin path (AC2, AC3)
// ---------------------------------------------------------------------------

const TOK223 = "soutok-Adapter4TestToken-ABCDEFGHIJ0123456789";

test("plugin: SOU_TOKEN reaches every POST the adapter fires", async () => {
  const seen = [];
  const plugin = createPlugin({
    config: loadConfig({ HOME: NO_HOME, SOU_DASHBOARD_URL: "http://capture.test:19000", SOU_TOKEN: TOK223 }),
    fetch: async (url, init) => {
      seen.push(init.headers);
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  const ctx = mockCtx();
  const cleanup = await plugin.setup(ctx);
  ctx._hooks["execute.after"][0]({ tool: "bash", callID: "t1", status: "completed", sessionID: "s1", input: { command: "ls" }, result: { output: "ok" } });
  await cleanup();
  assert.ok(seen.length >= 2, "session_start and later events all POST");
  for (const headers of seen) assert.equal(headers.Authorization, `Bearer ${TOK223}`);
});

test("plugin: the SOU_DEBUG log file never carries the plaintext token", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "sou223-"));
  const logFile = path.join(dir, "reporter.log");
  const plugin = createPlugin({
    config: loadConfig({ HOME: NO_HOME,
      SOU_DASHBOARD_URL: "http://capture.test:19000",
      SOU_TOKEN: TOK223,
      SOU_DEBUG: "1",
      SOU_REPORTER_LOG: logFile,
    }),
    fetch: async () => {
      throw new Error(`connect failed with header Authorization: Bearer ${TOK223} in diagnostic`);
    },
    // no inject.logger on purpose: exercise the real sink (console + app.log mirror + debug file)
  });
  const cleanup = await plugin.setup(mockCtx());
  await cleanup();
  const text = readFileSync(logFile, "utf8");
  rmSync(dir, { recursive: true, force: true });
  assert.ok(text.length > 0, "sink failures were logged");
  assert.ok(!text.includes(TOK223), "plaintext token must not reach the debug file");
  assert.match(text, /\[redacted\]/);
});

// ---------------------------------------------------------------------------
// Directory routes: one engine, several workspaces, one room each.
// ---------------------------------------------------------------------------

const ROUTES_FILE = JSON.stringify({
  dashboardUrl: "http://capture.test:19000",
  agent: "openchamber",
  token: "tok-default-0000000000000000000000",
  routes: [
    { directory: "/storage/workspaces/homelab-2nd", agent: "itsaplan-h2", token: "tok-h2-000000000000000000000000000" },
    { directory: "/storage/workspaces/star-office-ui-fork", agent: "itsaplan-sou", token: "tok-sou-00000000000000000000000000" },
    { directory: "/storage/workspaces", agent: "workspaces-catchall" },
    { directory: "", agent: "dropped-no-directory" },
    { directory: "/x", agent: "" },
  ],
});
const routesConfig = () => loadConfig({ HOME: NO_HOME }, { configPath: "/routes.json", readFile: () => ROUTES_FILE });

test("routes: parsed from the config file, invalid entries dropped", () => {
  const cfg = routesConfig();
  assert.deepEqual(cfg.routes.map((r) => r.agent), ["itsaplan-h2", "itsaplan-sou", "workspaces-catchall"]);
});

test("routes: longest directory prefix wins, on path boundaries", () => {
  const cfg = routesConfig();
  const pick = (d) => resolveRoute(cfg, d);
  assert.equal(pick("/storage/workspaces/homelab-2nd").agent, "itsaplan-h2");
  assert.equal(pick("/storage/workspaces/homelab-2nd/apps/x").agent, "itsaplan-h2");
  assert.equal(pick("/storage/workspaces/star-office-ui-fork").token, "tok-sou-00000000000000000000000000");
  assert.equal(pick("/storage/workspaces/homelab-2nd-old").agent, "workspaces-catchall", "a sibling with the same prefix is not inside");
  assert.equal(pick("/storage/workspaces/other").token, "tok-default-0000000000000000000000", "a route without a token keeps the default token");
  assert.equal(pick("/home/someone/project").agent, "openchamber", "no match keeps the defaults");
  assert.equal(resolveRoute(loadConfig({ HOME: NO_HOME }), "/anything").agent, "openchamber", "no routes at all");
});

test("routes: each OpenCode instance reports as its own agent with its own token", async () => {
  const seen = [];
  const plugin = createPlugin({
    config: routesConfig(),
    fetch: async (url, init) => {
      seen.push({ agent: JSON.parse(init.body).agent, auth: init.headers.Authorization });
      return { ok: true, status: 201 };
    },
    logger: () => {},
  });
  for (const directory of ["/storage/workspaces/homelab-2nd", "/storage/workspaces/star-office-ui-fork"]) {
    const ctx = mockCtx();
    ctx.location.directory = directory;
    const cleanup = await plugin.setup(ctx);
    await cleanup();
  }
  const h2 = seen.filter((s) => s.agent === "itsaplan-h2");
  const sou = seen.filter((s) => s.agent === "itsaplan-sou");
  assert.ok(h2.length >= 2 && sou.length >= 2, "session_start and session_end from each instance");
  for (const s of h2) assert.equal(s.auth, "Bearer tok-h2-000000000000000000000000000");
  for (const s of sou) assert.equal(s.auth, "Bearer tok-sou-00000000000000000000000000");
});

test("routes: OPENCODE_CONFIG_DIR is a config-file candidate", () => {
  const read = [];
  loadConfig({ HOME: NO_HOME, OPENCODE_CONFIG_DIR: "/storage/opencode-config" }, { readFile: (f) => { read.push(f); return null; } });
  assert.ok(read.includes(path.join("/storage/opencode-config", "star-office-reporter.json")), read.join(", "));
});
