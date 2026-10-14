/*
 * test/e2e-live-run.mjs — end-to-end acceptance for SOU-71 (P6-D).
 *
 * Drives a REAL headless opencode run (same engine OpenChamber embeds) with
 * the reporter installed into the documented global plugin directory
 * (~/.config/opencode/plugins/), pointed at a local capture of the dashboard
 * contract, then schema-validates every delivered envelope with the
 * dashboard's own validator (backend/event_schema.py). A second scenario
 * proves an unreachable dashboard never blocks or fails the agent.
 *
 * Run: E2E=1 node --test test/e2e-live-run.mjs   (needs opencode, litellm, python3)
 */
import { execFile, spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUGIN_FILE = path.join(HERE, '..', 'openchamber-star-office-reporter.js')
const GLOBAL_CONFIG = process.env.OPENCODE_GLOBAL_CONFIG || '/storage/opencode-config/opencode.json'
const MODEL = process.env.SOU_E2E_MODEL || 'litellm/qwen3.8-flash'
const CANARY = 'sk-CANARYDONOTLEAKABCDEFGHIJKLMNOP'
const SKIP = process.env.E2E !== '1'

// The OpenChamber service injects OPENCODE_PORT/OPENCODE_PASSWORD/OPENCODE=1
// into this process; children must NOT inherit them or `opencode run` silently
// attaches to the live service instead of running standalone (proven the hard
// way during SOU-71: hijacked prompts executed against the live session dir).
function cleanChildEnv(home, cwd) {
  const env = { ...process.env }
  for (const key of Object.keys(env)) {
    if (key === 'OPENCODE' || key.startsWith('OPENCODE_') || key.startsWith('OPENCHAMBER_') || key === 'AI_AGENT' || key === 'PWD' || key === 'OLDPWD') delete env[key]
  }
  return {
    ...env,
    PWD: cwd, // opencode trusts $PWD for its workspace location — pin it to the scratch dir
    HOME: home,
    XDG_CONFIG_HOME: path.join(home, '.config'),
    XDG_DATA_HOME: path.join(home, '.local', 'share'),
    XDG_STATE_HOME: path.join(home, '.local', 'state'),
    XDG_CACHE_HOME: path.join(home, '.cache'),
  }
}

// Reuse the machine's real litellm provider block (baseURL + key) at runtime,
// inlining {env:NAME} templates so the scrubbed child never needs service env.
function resolveProvider() {
  try {
    const cfg = JSON.parse(fs.readFileSync(GLOBAL_CONFIG, 'utf8'))
    const provider = cfg?.provider?.litellm
    const inlined = JSON.parse(JSON.stringify(provider), (_k, value) =>
      typeof value === 'string' ? value.replace(/\{env:([A-Z0-9_]+)\}/g, (_, v) => process.env[v] || '') : value
    )
    if (!inlined?.options?.apiKey) throw new Error('empty apiKey')
    return inlined
  } catch {
    const key = process.env.LITELLM_API_KEY || ''
    if (!key) throw new Error('litellm key not resolvable for e2e (check OPENCHAMBER_LITELLM_KEY)')
    return { npm: '@ai-sdk/openai-compatible', name: 'LiteLLM (e2e)', options: { baseURL: 'http://litellm.llm-hub.svc.cluster.local:4000/v1', apiKey: key }, models: { 'qwen3.8-flash': {} } }
  }
}

// Throwaway HOME + project dir: nothing touches /storage or the shared
// opencode.db. The plugin is installed exactly the way the README tells users
// to install it: global plugins directory.
function makeScratch(name, pluginTarget) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `sou71-${name}-`))
  const home = path.join(dir, 'home')
  const globalPlugins = path.join(home, '.config', 'opencode', 'plugins')
  fs.mkdirSync(globalPlugins, { recursive: true })
  const project = path.join(dir, 'project')
  fs.mkdirSync(project, { recursive: true })
  const target =
    pluginTarget === 'project'
      ? (fs.mkdirSync(path.join(project, '.opencode', 'plugins'), { recursive: true }), path.join(project, '.opencode', 'plugins', 'openchamber-star-office-reporter.js'))
      : path.join(globalPlugins, 'openchamber-star-office-reporter.js')
  fs.copyFileSync(PLUGIN_FILE, target)
  fs.writeFileSync(
    path.join(home, '.config', 'opencode', 'opencode.json'),
    JSON.stringify({ $schema: 'https://opencode.ai/config.json', autoupdate: false, share: 'disabled', model: MODEL, provider: { litellm: resolveProvider() } }, null, 2)
  )
  return { dir, home, project, pluginAt: target }
}

function runOpencode(scratchData, prompt, extraEnv, timeoutMs = 180000) {
  return new Promise((resolve) => {
    const started = Date.now()
    const child = spawn('opencode', ['run', '--standalone', prompt], {
      cwd: scratchData.project,
      env: { ...cleanChildEnv(scratchData.home, scratchData.project), ...extraEnv },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let out = ''
    let err = ''
    child.stdout.on('data', (d) => (out += d))
    child.stderr.on('data', (d) => (err += d))
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve({ code: 'TIMEOUT', out, err, ms: Date.now() - started })
    }, timeoutMs)
    child.on('exit', (code) => {
      clearTimeout(timer)
      resolve({ code, out, err, ms: Date.now() - started })
    })
  })
}

function startCapture(port, out) {
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [path.join(HERE, 'tools', 'capture-server.mjs'), out, String(port)], { stdio: ['ignore', 'pipe', 'pipe'] })
    let ready = false
    proc.stdout.on('data', (d) => {
      if (!ready && String(d).includes('LISTEN')) {
        ready = true
        resolve({ proc })
      }
    })
    proc.stderr.on('data', () => {})
    proc.on('exit', (code) => {
      if (!ready) reject(new Error(`capture server exited ${code} before ready`))
    })
    setTimeout(() => reject(new Error('capture server did not become ready')), 10000).unref?.()
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function readEvents(file) {
  if (!fs.existsSync(file)) return []
  return fs
    .readFileSync(file, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((l) => JSON.parse(l))
}

async function waitForEvents(file, kinds, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const events = readEvents(file)
    if (kinds.every((k) => events.some((e) => e.event_type === k))) return events
    if (Date.now() > deadline) return readEvents(file)
    await sleep(250)
  }
}

describe(
  'e2e: live opencode run through the reporter',
  { skip: SKIP },
  () => {
    let run1
    let events
    let capture
    let scratch
    let captureFile
    const t0 = Date.now()

    before(async () => {
      scratch = makeScratch('live')
      captureFile = path.join(scratch.dir, 'capture.ndjson')
      capture = await startCapture(19111, captureFile)
      run1 = await runOpencode(
        scratch,
        `Do exactly these three things with tools, one at a time, then reply DONE:
1. Use the write tool to create ping.txt containing one line: pong
2. Use the bash tool to run: echo sou71-e2e-probe
3. Use the bash tool to run: echo ${CANARY}`,
        { SOU_DASHBOARD_URL: 'http://127.0.0.1:19111', SOU_AGENT_NAME: 'openchamber', SOU_DEBUG: '1' }
      )
      events = await waitForEvents(captureFile, ['session_start', 'tool_call', 'code_completion', 'file_edit', 'command_run', 'session_end'])
      await sleep(500) // let late arrivals flush before assertions
      events = readEvents(captureFile)
    })

    after(() => {
      capture?.proc?.kill()
    })

    test('the opencode run itself succeeds', () => {
      if (run1.code !== 0) throw new Error(`opencode run exited ${run1.code}: ${run1.out.slice(-500)} | ${run1.err.slice(-500)}`)
    })

    test('reporter delivered the full event set', () => {
      const kinds = events.map((e) => e.event_type)
      if (!kinds.length) throw new Error(`zero events captured (run exit ${run1.code}, plugin at ${scratch.pluginAt})`)
      for (const kind of ['session_start', 'tool_call', 'code_completion', 'file_edit', 'command_run', 'session_end']) {
        if (!kinds.includes(kind)) throw new Error(`missing ${kind}; got ${JSON.stringify(kinds)}`)
      }
    })

    test('every envelope passes the dashboard schema validator', async () => {
      const { stdout } = await execFileAsync('python3', [path.join(HERE, 'tools', 'validate_envelopes.py'), captureFile, '/storage/workspaces/star-office-ui-fork'])
      if (!/^VALID \d+/m.test(stdout)) throw new Error(stdout)
      if (/INVALID/.test(stdout)) throw new Error(stdout)
    })

    test('agent + version fields are exactly schema v1', () => {
      for (const e of events) {
        if (e.version !== 1) throw new Error(`version ${e.version}`)
        if (e.agent !== 'openchamber') throw new Error(`agent ${e.agent}`)
      }
    })

    test('the canary secret never leaves redacted', () => {
      const raw = fs.readFileSync(captureFile, 'utf8')
      if (raw.includes(CANARY)) throw new Error('canary leaked into delivered payloads')
      if (/Authorization|Bearer /i.test(raw)) throw new Error('authorization material in capture')
      const redacted = events.filter((e) => e.event_type === 'command_run' && /\[redacted\]/i.test(JSON.stringify(e.payload)))
      if (!redacted.length) throw new Error('canary command_run event did not exercise the redaction path')
    })

    test('session_end carries duration + counters', () => {
      const end = events.find((e) => e.event_type === 'session_end')
      if (!end || Date.parse(end.timestamp) < t0) throw new Error('session_end missing or stale')
      if (typeof end.payload?.duration_seconds !== 'number') throw new Error(`session_end payload: ${JSON.stringify(end.payload)}`)
    })

    test('delivered strings are truncated to sane field sizes', () => {
      for (const e of events) {
        for (const value of Object.values(e.payload ?? {})) {
          if (typeof value === 'string' && value.length > 2200) throw new Error(`un-truncated field (${value.length}): ${value.slice(0, 80)}`)
        }
      }
    })
  }
)

describe(
  'e2e: unreachable dashboard does not block the agent',
  { skip: SKIP },
  () => {
    test('run against a dead port completes normally and logs the failure', async () => {
      const s2 = makeScratch('offline')
      const start = Date.now()
      const run2 = await runOpencode(s2, 'Use the bash tool to run: echo offline-probe. Then reply OK.', {
        SOU_DASHBOARD_URL: 'http://127.0.0.1:19199',
        SOU_DEBUG: '1',
      })
      const wall = Date.now() - start
      if (run2.code !== 0) throw new Error(`offline run exited ${run2.code}: ${run2.err.slice(-400)}`)
      if (!run2.out.includes('offline-probe') && !run2.out.includes('OK')) throw new Error(`agent output missing: ${run2.out.slice(-300)}`)
      if (wall > 120000) throw new Error(`offline run took ${wall}ms — looks blocked`)
      const logDir = path.join(s2.home, '.local', 'share', 'opencode', 'log')
      const logText = fs.existsSync(logDir) ? fs.readdirSync(logDir).map((f) => fs.readFileSync(path.join(logDir, f), 'utf8')).join('\n') : ''
      const debugLog = path.join(s2.home, '.local', 'state', 'sou-reporter', 'reporter.log')
      const debugText = fs.existsSync(debugLog) ? fs.readFileSync(debugLog, 'utf8') : ''
      const surface = run2.out + run2.err + logText + debugText
      if (!surface.includes('[sou-reporter]')) {
        throw new Error(`no [sou-reporter] diagnostics found (stdout+stderr+opencode log+${debugLog}). err tail: ${run2.err.slice(-300)}`)
      }
      if (!/unreachable|dropped|rejected/i.test(surface)) throw new Error('diagnostics do not mention delivery failures')
    })
  }
)
