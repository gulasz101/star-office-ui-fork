// openchamber-star-office-reporter — OpenCode/OpenChamber reporter plugin for
// the Star Office UI dashboard (SOU-71, ADR-035 event schema v1).
//
// One self-contained ESM file, no runtime dependencies, no OpenCode imports:
// OpenCode 2.x auto-loads direct .js files under .opencode/plugins/ (project
// or global), and the same file is unit-testable with `node --test`.
//
//   Hook layer (adapter):  ctx.tool.hook("execute.before"/"execute.after"),
//                          ctx.event.subscribe()  ->  six event types below.
//   Pure layer (core):     loadConfig / buildEnvelope / redactPayload /
//                          classifyTool / createReporter — exported for tests.
//
// Event types reported (issue SOU-71 AC3):
//   session_start      plugin setup (OpenChamber/OpenCode server came up)
//   tool_call          every tool execution, after it finishes
//   file_edit          write/edit/patch-class tools
//   command_run        bash/shell-class tools, output truncated
//   code_completion    session.idle after the session edited files
//   session_end        session.deleted, plugin cleanup, or SIGINT/SIGTERM
//
// Configuration (AC4), precedence env > config file > defaults:
//   SOU_DASHBOARD_URL      default http://127.0.0.1:19000
//   SOU_AGENT_NAME         default openchamber (use opencode behind OpenChamber)
//   SOU_REPORTER_ENABLED   0/false/no/off disables every POST
//   SOU_TIMEOUT_MS         default 5000, clamped to [200, 30000]
//   SOU_REPORTER_CONFIG    path to a JSON file; otherwise standard locations
//   (see README.md).
//
// Privacy (AC5): secret-shaped values and secret-named keys are redacted,
// long strings truncated, and the whole payload kept under the dashboard's
// 65536-byte cap. Non-blocking (AC2): hooks never await the network; send
// failures are one stderr line and a counter.

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export const PLUGIN_ID = "openchamber-star-office-reporter";

export const DEFAULTS = {
  dashboardUrl: "http://127.0.0.1:19000",
  agent: "openchamber",
  timeoutMs: 5000,
  enabled: true,
  minTimeoutMs: 200,
  maxTimeoutMs: 30000,
  maxFieldChars: 4000,
  maxPayloadBytes: 65536, // backend/event_schema.py PAYLOAD_MAX_BYTES
};

const OFF_WORDS = new Set(["0", "false", "no", "off"]);

// --- configuration ----------------------------------------------------------

function parseTimeoutMs(raw, fallback) {
  const n = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(n) || n < DEFAULTS.minTimeoutMs) return fallback;
  return Math.min(n, DEFAULTS.maxTimeoutMs);
}

function configFileCandidates(env) {
  const out = [];
  if (env.SOU_REPORTER_CONFIG) out.push(env.SOU_REPORTER_CONFIG);
  const xdg = env.XDG_CONFIG_HOME;
  if (xdg) {
    out.push(path.join(xdg, "star-office-reporter.json"));
    out.push(path.join(xdg, "opencode", "star-office-reporter.json"));
  }
  const home = typeof env.HOME === "string" && env.HOME ? env.HOME : homedir();
  out.push(path.join(home, ".config", "opencode", "star-office-reporter.json"));
  return out;
}

function normalizeLayer(obj) {
  const layer = {};
  if (typeof obj.dashboardUrl === "string" && obj.dashboardUrl.trim()) {
    layer.dashboardUrl = obj.dashboardUrl.trim().replace(/\/+$/, "");
  }
  if (typeof obj.agent === "string" && obj.agent.trim()) layer.agent = obj.agent.trim();
  if (obj.enabled !== undefined) {
    layer.enabled = typeof obj.enabled === "boolean" ? obj.enabled : !OFF_WORDS.has(String(obj.enabled).trim().toLowerCase());
  }
  if (obj.timeoutMs !== undefined) {
    const n = Number.parseInt(String(obj.timeoutMs), 10);
    layer.timeoutMs = Number.isFinite(n) ? n : undefined;
  }
  return layer;
}

/**
 * Load reporter configuration. `env` defaults to process.env; `opts.readFile`
 * is injectable for tests. Precedence: env vars > JSON config file > defaults.
 */
export function loadConfig(env = process.env, opts = {}) {
  const readFile = opts.readFile ?? safeReadFile;
  let merged = { dashboardUrl: DEFAULTS.dashboardUrl, agent: DEFAULTS.agent, timeoutMs: DEFAULTS.timeoutMs, enabled: DEFAULTS.enabled };

  const candidates = opts.configPath ? [opts.configPath] : configFileCandidates(env);
  for (const file of candidates) {
    const text = readFile(file);
    if (text == null) continue;
    try {
      Object.assign(merged, normalizeLayer(JSON.parse(text)));
    } catch {
      // Malformed config file: ignore it, the plugin must not fight the host.
    }
    break;
  }

  const envLayer = normalizeLayer({
    dashboardUrl: env.SOU_DASHBOARD_URL,
    agent: env.SOU_AGENT_NAME,
    timeoutMs: env.SOU_TIMEOUT_MS,
    enabled: env.SOU_REPORTER_ENABLED === undefined ? undefined : !OFF_WORDS.has(String(env.SOU_REPORTER_ENABLED).trim().toLowerCase()),
  });
  Object.assign(merged, envLayer);

  const timeoutMs = parseTimeoutMs(merged.timeoutMs, DEFAULTS.timeoutMs);
  const dashboardUrl = String(merged.dashboardUrl || DEFAULTS.dashboardUrl).trim().replace(/\/+$/, "");
  return {
    dashboardUrl,
    eventsUrl: `${dashboardUrl}/events`,
    agent: merged.agent || DEFAULTS.agent,
    enabled: merged.enabled !== false,
    timeoutMs,
    maxFieldChars: DEFAULTS.maxFieldChars,
    maxPayloadBytes: DEFAULTS.maxPayloadBytes,
    minTimeoutMs: DEFAULTS.minTimeoutMs,
    maxTimeoutMs: DEFAULTS.maxTimeoutMs,
  };
}

function safeReadFile(file) {
  try {
    return readFileSync(file, "utf8");
  } catch {
    return null;
  }
}

// --- privacy ----------------------------------------------------------------

const SECRET_VALUE_PATTERNS = [
  [/\b(bearer|basic)\s+[A-Za-z0-9\-._~+/=]{4,}\b/gi, "$1 [redacted]"],
  [/\b(?:sk|rk|pk|key)-[A-Za-z0-9_\-]{12,}\b/g, "[redacted]"],
  [/\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b/g, "[redacted]"],
  [/\bAKIA[0-9A-Z]{12,}\b/g, "[redacted]"],
  [/\bxox[abprs]-[A-Za-z0-9-]{10,}\b/g, "[redacted]"],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b/g, "[redacted]"],
  // key: value / key=value with a secret-named key
  [/((?:api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|passwd|credential|authorization|auth[_-]?key)[\w-]*\s*[=:]\s*)(?:"[^"]{1,}"|'[^']{1,}'|(?!\[redacted\])[^\s;,']{4,})/gi, "$1[redacted]"],
];

const SECRET_KEY_NAME = /(api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|credential|authorization|auth[_-]?key|private[_-]?key|cookie)/i;

function scrubString(str, maxFieldChars) {
  let out = str;
  if (out.length > maxFieldChars) {
    out = `${out.slice(0, maxFieldChars)}…[truncated ${out.length - maxFieldChars} chars]`;
  }
  for (const [re, repl] of SECRET_VALUE_PATTERNS) out = out.replace(re, repl);
  return out;
}

function redactWalk(node, maxFieldChars, depth) {
  if (node === null || node === undefined) return node;
  if (typeof node === "string") return scrubString(node, maxFieldChars);
  if (typeof node === "number" || typeof node === "boolean") return node;
  if (depth > 8) return "[depth-limited]";
  if (Array.isArray(node)) {
    return node.slice(0, 200).map((item) => redactWalk(item, maxFieldChars, depth + 1));
  }
  if (typeof node === "object") {
    const out = {};
    for (const [key, value] of Object.entries(node).slice(0, 200)) {
      out[key] = SECRET_KEY_NAME.test(key) ? "[redacted]" : redactWalk(value, maxFieldChars, depth + 1);
    }
    return out;
  }
  return scrubString(String(node), maxFieldChars);
}

/**
 * Privacy pass (AC5): redact secret-shaped values and secret-named keys,
 * truncate long strings, and keep the serialized payload within
 * config.maxPayloadBytes so POST /events never 400s on size.
 */
export function redactPayload(payload, config = loadConfig(process.env)) {
  const maxField = config.maxFieldChars ?? DEFAULTS.maxFieldChars;
  const cap = config.maxPayloadBytes ?? DEFAULTS.maxPayloadBytes;
  let out = redactWalk(payload && typeof payload === "object" ? payload : { detail: payload ?? {} }, maxField, 0);

  // Size cap: halve the longest string fields until the payload fits.
  let guard = 0;
  while (Buffer.byteLength(JSON.stringify(out), "utf8") > cap && guard < 8) {
    const limit = Math.max(64, Math.floor(maxField / 2 ** (guard + 1)));
    out = shrinkStrings(out, limit);
    guard += 1;
  }
  if (Buffer.byteLength(JSON.stringify(out), "utf8") > cap) {
    out = { truncated: true, note: "payload exceeded reporter size cap" };
  }
  return out;
}

function shrinkStrings(node, limit) {
  if (typeof node === "string") return node.length > limit ? `${node.slice(0, limit)}…[truncated ${node.length - limit} chars]` : node;
  if (Array.isArray(node)) return node.map((item) => shrinkStrings(item, limit));
  if (node && typeof node === "object") {
    const out = {};
    for (const [key, value] of Object.entries(node)) out[key] = shrinkStrings(value, limit);
    return out;
  }
  return node;
}

// --- classification -----------------------------------------------------------

const FILE_EDIT_TOOL = /^(?:[\w.-]+_)?(write|edit|patch|apply_patch|multi[_-]?edit|create[_-]?file|str[_-]?replace|search[_-]?replace|replace[_-]?in[_-]?file)$/i;
const COMMAND_TOOL = /^(?:[\w.-]+_)?(bash|shell|terminal|exec|execute|run[_-]?command|command|cmd)$/i;

/** Map a tool name to the extra event types it implies (AC3). */
export function classifyTool(name) {
  const tool = typeof name === "string" ? name.trim().toLowerCase() : "";
  return { fileEdit: FILE_EDIT_TOOL.test(tool), command: COMMAND_TOOL.test(tool) };
}

// --- envelope -----------------------------------------------------------------

/** Build an ADR-035 event schema v1 envelope. Throws on an unusable shape. */
export function buildEnvelope(config, eventType, payload, now = new Date()) {
  const event = typeof eventType === "string" ? eventType.trim() : "";
  if (!event || /\s/.test(event) || event.length > 100) {
    throw new TypeError(`event_type: must be 1-100 non-whitespace chars, got ${JSON.stringify(eventType)}`);
  }
  const agent = typeof config.agent === "string" ? config.agent.trim() : "";
  if (!agent || /\s/.test(agent) || agent.length > 255) {
    throw new TypeError(`agent: must be a single token of 1-255 chars, got ${JSON.stringify(config.agent)}`);
  }
  return {
    version: 1,
    agent,
    event_type: event,
    timestamp: now.toISOString(),
    payload: redactPayload(payload ?? {}, config),
  };
}

// --- transport ----------------------------------------------------------------

/**
 * Fire-and-forget reporter. report() never blocks on the network (AC2):
 * it starts the POST and hands back a promise the caller may ignore.
 * Every failure path ends in one log line plus a counter — the plugin
 * must not slow OpenChamber down or crash it when the dashboard is off.
 */
export function createReporter(config, deps = {}) {
  const doFetch = deps.fetch ?? globalThis.fetch;
  const logger = deps.logger ?? ((msg) => { try { console.error(`[sou-reporter] ${msg}`); } catch { /* ignore */ } });
  const stats = { sent: 0, failed: 0, dropped: 0 };
  const enabled = config.enabled !== false;
  const inflight = new Set();

  async function send(envelope) {
    try {
      if (typeof doFetch !== "function") throw new Error("no fetch implementation");
      const res = await doFetch(config.eventsUrl, {
        method: "POST",
        headers: { "content-type": "application/json", "user-agent": `${PLUGIN_ID}/1.0 (${config.agent})` },
        body: JSON.stringify(envelope),
        signal: AbortSignal.timeout(config.timeoutMs),
      });
      if (res && res.ok) {
        stats.sent += 1;
        return true;
      }
      stats.failed += 1;
      logger(`dashboard rejected ${envelope.event_type}: HTTP ${res ? res.status : "?"}`);
      return false;
    } catch (err) {
      stats.failed += 1;
      logger(`dashboard unreachable, dropped ${envelope.event_type}: ${(err && err.message) || err}`);
      return false;
    }
  }

  function report(eventType, payload) {
    if (!enabled) return Promise.resolve(false);
    let envelope;
    try {
      envelope = buildEnvelope(config, eventType, payload ?? {});
    } catch (err) {
      stats.dropped += 1;
      logger(`invalid event ${eventType}: ${err.message}`);
      return Promise.resolve(false);
    }
    const p = send(envelope).finally(() => inflight.delete(p));
    inflight.add(p);
    return p;
  }

  async function flush(boundMs = config.timeoutMs) {
    const all = Promise.allSettled([...inflight]);
    if (boundMs > 0) {
      await Promise.race([all, new Promise((resolve) => setTimeout(resolve, boundMs))]);
    } else {
      await all;
    }
  }

  return { report, flush, stats: () => ({ ...stats }), enabled };
}
