"""Server-side ingest sanitiser (SOU-218, Phase 13 — docs/public-lodge.md §3).

Reporters can be old or buggy, so the server is the gate: ``POST /events``
persists only this **safe projection** of every payload, never the raw one.
A DB dump then leaks nothing, because the sensitive fields never land.

The rules are ported from the Claude Code reporter
(``reporter/claude-code/star_office_hook.py``, SOU-157/SOU-173), which stays
the reference implementation and test table for what a command label may
contain. The port is deliberate — the reporter must stay stdlib-only and
self-contained, so the duplication is the documented price of the one-way
privacy contract; when the reporter's rules move, move these with it.

Public API used by ``app.ingest_event``:

    sanitize_payload(event_type, payload) -> dict

Pure module: no I/O, no Flask, no database.
"""

from __future__ import annotations

import re

# A single value in a payload is a label for a pixel character, not a
# document (same cap as the reporter, MAX_VALUE_CHARS).
MAX_VALUE_CHARS = 120

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# --- command label rules (ported verbatim from the reporter) ---------------
# Wrappers that are not the program the agent actually ran.
COMMAND_WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "command", "nice", "timeout"}
# `timeout 300 cmd` / `timeout 5m cmd`: the duration is the wrapper's argument.
DURATION = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")
# Directory changes chained in front of the real work (`cd /repo && git push`)
# are skipped; a command that is only a `cd` still reports as `cd`.
DIR_CHANGERS = {"cd", "pushd"}
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|]")
PYTHONS = re.compile(r"^python[0-9.]*$")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PROGRAM = re.compile(r"^[A-Za-z0-9._+-]{1,32}$")
TOKEN_SPLIT = re.compile(r"\s+")

# Programs allowed to carry one subcommand token in the label (SOU-173).
# Mirrors SUBCOMMAND_PROGRAMS in the reporter and DISPLAY_SUB_PROGS in
# frontend/v3/activity.js: these are the CLI families whose verb decides the
# office zone, and the renderer re-parses the same label.
SUBCOMMAND_PROGRAMS = {
    "git", "npm", "pnpm", "yarn", "docker", "kubectl", "cargo", "go",
    "helm", "flux", "make", "uv", "pip", "poetry",
}
# npm/pnpm/yarn need `run <script>` as TWO tokens: activity.js computes the
# runner verb from the token after "run", so a lone "npm run" would classify
# `npm run build` as ops instead of the garage.
RUNNER_PROGRAMS = {"npm", "pnpm", "yarn"}
# A subcommand is a bare lowercase identifier, never a flag, path, URL,
# key=value pair or value. This regex is the whole privacy boundary.
SUBCOMMAND = re.compile(r"^[a-z][a-z0-9-]{0,20}$")


def text_of(value, limit: int = MAX_VALUE_CHARS) -> str:
    """A trimmed, control-character-free single-line string, or "" for non-text."""
    if isinstance(value, str):
        out = _CONTROL_CHARS.sub(" ", value).strip()
        return out[:limit]
    return ""


def basename(value) -> str:
    """Last path segment only — the whole path is the thing we must not store."""
    raw = text_of(value, 1024)
    if not raw:
        return ""
    normalised = raw.replace("\\", "/")
    parts = [part for part in normalised.split("/") if part not in ("", ".")]
    if not parts:
        return ""
    last = parts[-1]
    if last in {"..", "/"} or "\x00" in last:
        return ""
    # A drive letter or a leftover separator means we did not get a basename.
    if ":" in last or "/" in last or "\\" in last:
        return ""
    return last[:MAX_VALUE_CHARS]


def enum(value) -> str:
    """A documented enum value kept as-is when it is only letters/_/-."""
    raw = text_of(value, 64)
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw or ""):
        return raw
    return ""


# `state` / `area` carry an office meaning; anything shaped like prose or a
# path is not a state name and gets dropped (ticket: enum-ish ^[a-z_]{1,32}$).
STATE_NAME = re.compile(r"^[a-z_]{1,32}$")


def state_name(value) -> str:
    raw = text_of(value, 64)
    return raw if STATE_NAME.fullmatch(raw) else ""


def token(value) -> str:
    """An opaque identifier (task id): keep it, but keep it single-token."""
    raw = text_of(value, MAX_VALUE_CHARS)
    if not raw or re.search(r"\s", raw):
        return ""
    return raw


def command_parts(command):
    """First real command -> ``(program, [arguments after it])``.

    Leading ``cd``/``pushd`` segments are skipped when something follows them.
    Env prefixes (``FOO=bar``), wrapper tokens (``sudo``, ``env``,
    ``timeout 300`` …) and flags are skipped while finding the program;
    ``python -m <module>`` reports the module. Nothing usable -> ``("shell", [])``.
    """
    raw = text_of(command, 4096)
    if not raw:
        return "", []
    segments = [seg for seg in SEGMENT_SPLIT.split(raw) if seg.strip()]
    parsed = None
    for seg in segments:
        parsed = _segment_parts(seg)
        if parsed[0] not in DIR_CHANGERS:
            return parsed
    return parsed or ("shell", [])


def _segment_parts(segment):
    tokens = [token for token in TOKEN_SPLIT.split(segment.strip()) if token]
    skip_duration = False
    for index, tok in enumerate(tokens):
        if skip_duration and DURATION.match(tok):
            skip_duration = False
            continue
        if ASSIGNMENT.match(tok) or tok.startswith("-"):
            continue  # env assignment or a flag of the wrapper
        name = basename(tok.replace("\\", "/")) or tok
        name = name.split("/")[-1]
        if name in COMMAND_WRAPPERS:
            skip_duration = name == "timeout"
            continue
        if not PROGRAM.match(name) or ":" in name:
            return "shell", []
        rest = tokens[index + 1:]
        if PYTHONS.match(name) and len(rest) >= 2 and rest[0] == "-m" and PROGRAM.match(rest[1]):
            return rest[1], rest[2:]
        return name, rest
    return "shell", []


def program(command) -> str:
    """Program name of a shell command: first real token, never arguments."""
    return command_parts(command)[0]


def command_label(command) -> str:
    """Privacy-safe label: the program plus ONE allow-listed subcommand.

    ``git push origin main`` -> ``git push``, ``docker build -t x .`` ->
    ``docker build``, ``npm run build`` -> ``npm run build``. Flags, values,
    paths and URLs are skipped and never echoed; programs outside
    ``SUBCOMMAND_PROGRAMS`` keep program-only labels.
    """
    prog, args = command_parts(command)
    if not prog or prog.lower() not in SUBCOMMAND_PROGRAMS:
        return prog
    picked = []
    for tok in args:
        if tok.startswith("-") or not SUBCOMMAND.match(tok):
            continue  # a flag or a value: never a subcommand, never echoed
        picked.append(tok)
        want = 2 if prog.lower() in RUNNER_PROGRAMS and picked[0] == "run" else 1
        if len(picked) >= want:
            break
    return " ".join([prog] + picked)


# --- per-field rules --------------------------------------------------------
def _clean_text(value) -> str:
    """Free-ish identifier text (tool names): cleaned and capped, nothing more."""
    return text_of(value, MAX_VALUE_CHARS)


def _whole_int(value, lo: int, hi: int):
    """An integer in range, else None (a bool is never a count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < lo or value > hi:
        return None
    return value


# key -> rule turning the raw value into the stored value ("" or None drops it)
FIELD_RULES = {
    "command": command_label,
    "label": command_label,  # background-task labels are command-shaped
    "file": basename,
    "path": basename,
    "target_file": basename,
    "file_path": basename,
    "notebook_path": basename,
    "absolute_path": basename,
    "cwd": basename,
    "tool": _clean_text,
    "status": enum,
    "reason": enum,
    "notification_type": enum,
    "source": enum,
    "kind": enum,
    "state": state_name,
    "area": state_name,
    "task_id": token,
    "duration_ms": lambda v: _whole_int(v, 0, 86_400_000),
    "exit_code": lambda v: _whole_int(v, -1000, 1000),
}

# The label-ish keys a "work" event may keep (activity.js mapEvent and
# src/card.js read these and nothing else). Free text (detail, message,
# error, output, prompt), url and session_id are NOT here — they are dropped
# for every event type.
WORK_KEYS = {
    "tool", "status", "duration_ms", "exit_code", "kind", "label", "task_id",
    "source", "command",
    "file", "path", "target_file", "file_path", "notebook_path",
    "absolute_path", "cwd",
}

# Per-event-type allow-list (the ticket's rules; the table is the contract).
# Types the table does not name fall back to _DEFAULT_KEYS.
_EVENT_KEYS = {
    "session_start": {"cwd", "source"},
    "session_end": {"reason"},
    "state_change": {"state", "area", "notification_type"},
    "code_completion": {"duration_ms", "task_id", "status", "kind", "label"},
}
# tool_call / file_edit / command_run / background_* / itsaplan_issue_transition
# and every future type: the work labels. `state`/`reason`/`notification_type`
# stay legal on unknown types (the ticket's superset), still enum-checked.
_DEFAULT_KEYS = WORK_KEYS | {"state", "reason", "notification_type"}


def allowed_keys(event_type) -> set:
    """The keys this event type may keep; everything else is dropped."""
    name = text_of(event_type, 100).strip().lower()
    if name in _EVENT_KEYS:
        return _EVENT_KEYS[name]
    return _DEFAULT_KEYS


def sanitize_payload(event_type, payload) -> dict:
    """The safe projection of one event payload: only allow-listed, only clean.

    Unknown keys, free text and session ids are dropped; commands become
    labels, paths become basenames, enums must match their shape; values are
    single-line, <= 120 chars, control-character-free. A field whose
    sanitised value is empty or unusable disappears with it. Pure and total:
    never raises, whatever the reporter sent.
    """
    if not isinstance(payload, dict):
        return {}
    allowed = allowed_keys(event_type)
    clean: dict = {}
    for key, value in payload.items():
        if not isinstance(key, str) or key not in allowed:
            continue  # unknown key, url, session_id, free text — gone
        rule = FIELD_RULES.get(key)
        if rule is None:
            continue  # allow-listed but no rule: safer to drop than to echo
        stored = rule(value)
        if stored is None or stored == "":
            continue
        clean[key] = stored
    return clean
