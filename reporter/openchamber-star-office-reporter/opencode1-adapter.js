// opencode1-adapter.js - Lodge (Star Office UI) reporter for OpenCode 1.x
// (GUL-2037 phase 1c, replaces the cancelled GUL-39 bridge).
//
// openchamber-star-office-reporter.js was written for the OpenChamber /
// OpenCode 2.x host API (`setup(ctx)`, ctx.tool.hook, ctx.event.subscribe).
// OpenCode 1.19 loads a plugin as `async (ctx) => hooks` and calls the hooks
// "tool.execute.before", "tool.execute.after" and `event` instead
// (https://opencode.ai/docs/plugins/). With the old file only `session_start`
// ever reached the Lodge. This adapter keeps the core (config, redaction,
// token handling, sending) and maps the 1.x hooks onto it.
//
// Install: copy or symlink this file AND openchamber-star-office-reporter.js
// into ~/.config/opencode/plugins/ (both files, same directory). Config is
// the same as the core: env SOU_* > ~/.config/opencode/star-office-reporter.json.
//
// Only ONE export, on purpose: OpenCode treats every exported function of a
// plugin module as a plugin.

import {
  loadConfig,
  resolveRoute,
  createReporter,
  classifyTool,
} from "./openchamber-star-office-reporter.js";

function compact(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null) out[k] = v;
  return out;
}

function pickPath(args) {
  if (!args || typeof args !== "object") return undefined;
  return args.filePath ?? args.file_path ?? args.path ?? args.file ?? undefined;
}

function textOf(output) {
  if (output == null) return undefined;
  const raw = typeof output.output === "string" ? output.output : typeof output === "string" ? output : undefined;
  return raw === undefined ? undefined : raw.slice(0, 2000);
}

function exitCodeOf(output) {
  const m = output?.metadata;
  const code = m?.exit ?? m?.exitCode ?? m?.exit_code;
  return typeof code === "number" ? code : undefined;
}

export const SouLodgeReporter = async (ctx = {}) => {
  try {
    const directory = ctx.directory ?? ctx.worktree ?? process.cwd();
    const config = resolveRoute(loadConfig(), directory);
    const reporter = createReporter(config);
    const startedAt = Date.now();
    const calls = new Map(); // callID -> { t0, args }
    const edits = new Map(); // sessionID -> Set(files)
    const count = () => {
      const s = reporter.stats();
      return s.sent + s.failed + s.dropped;
    };
    const safe = (fn) => async (...a) => {
      try {
        await fn(...a);
      } catch {
        /* reporting must never break the host */
      }
    };

    void reporter.report("session_start", compact({
      directory,
      engine: "opencode",
      plugin: "opencode1-adapter",
      pid: process.pid,
    }));

    const completions = (sessionID) => {
      const files = sessionID ? edits.get(sessionID) : undefined;
      if (files && files.size) {
        void reporter.report("code_completion", compact({
          session_id: sessionID,
          files_touched: [...files].slice(0, 50),
          edits: files.size,
        }));
        edits.set(sessionID, new Set());
      }
    };

    return {
      "tool.execute.before": safe((input, output) => {
        if (calls.size > 500) calls.delete(calls.keys().next().value);
        calls.set(input?.callID, { t0: Date.now(), args: output?.args });
      }),

      "tool.execute.after": safe((input, output) => {
        const start = calls.get(input?.callID);
        calls.delete(input?.callID);
        const tool = String(input?.tool ?? "unknown");
        const sessionID = input?.sessionID;
        const args = start?.args ?? input?.args;
        const durationMs = start?.t0 ? Date.now() - start.t0 : undefined;
        const cls = classifyTool(tool);
        void reporter.report("tool_call", compact({ tool, status: "completed", session_id: sessionID, duration_ms: durationMs }));
        if (cls.fileEdit) {
          const file = pickPath(args);
          if (sessionID && file) {
            if (!edits.has(sessionID)) edits.set(sessionID, new Set());
            edits.get(sessionID).add(String(file));
          }
          void reporter.report("file_edit", compact({ tool, file, session_id: sessionID, status: "completed", duration_ms: durationMs }));
        }
        if (cls.command) {
          void reporter.report("command_run", compact({
            command: args?.command,
            cwd: args?.workdir ?? args?.cwd,
            session_id: sessionID,
            status: "completed",
            exit_code: exitCodeOf(output),
            duration_ms: durationMs,
            output: textOf(output),
          }));
        }
      }),

      event: safe(({ event } = {}) => {
        const type = event?.type ?? "";
        const p = event?.properties ?? {};
        const sessionID = p.sessionID ?? p.info?.id;
        if (type === "session.idle") {
          // `opencode run` (how Paperclip starts OpenCode) exits right after
          // the session goes idle, so idle is the end of the run.
          completions(sessionID);
          void reporter.report("session_end", compact({
            session_id: sessionID,
            reason: "session_idle",
            duration_seconds: Math.round((Date.now() - startedAt) / 1000),
            events_logged: count(),
          }));
          // Awaited: OpenCode waits for the hook, so the events leave before exit.
          return reporter.flush(Math.min(config.timeoutMs, 2000));
        } else if (type === "session.deleted") {
          completions(sessionID);
          void reporter.report("session_end", compact({ session_id: sessionID, reason: "session_deleted", events_logged: count() }));
        } else if (type === "session.error") {
          void reporter.report("tool_call", compact({ tool: "session", status: "error", session_id: sessionID, error: String(p.error?.name ?? p.error?.message ?? "session error") }));
        }
      }),
    };
  } catch (err) {
    try { console.error(`[sou-reporter] opencode1 adapter setup failed: ${err && err.message}`); } catch { /* ignore */ }
    return {};
  }
};
