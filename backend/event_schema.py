"""Event Schema v1 validation for ``POST /events`` (SOU-36, ADR-035).

One contract, one place: ``backend/schemas/event_v1.json`` is the machine-
readable envelope agents send to the office. This module loads that file at
import, runs it with the ``jsonschema`` library (draft 2020-12), and adds the
two rules a schema document cannot express on its own:

* **The instant.** ``timestamp`` is parsed as ISO 8601 and normalised to
  *naive UTC*, the application-layer convention ADR-035 fixes for both
  backends (see ``db._utcnow``). ``jsonschema`` ignores ``format`` unless an
  optional format-checker is installed, so relying on the keyword alone would
  quietly accept garbage — the parse here is what enforces it (SOU-36 ruling).
* **The size.** ``payload`` is capped at :data:`PAYLOAD_MAX_BYTES`. The ADR
  names storage growth as a risk and this slice adds no authentication, so the
  request shape is the guard until SOU-44 lands a credential check.

Usage::

    import event_schema

    try:
        envelope = event_schema.validate_event(request.get_json())
    except event_schema.EventValidationError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

``EventValidationError`` is the only error this module raises for bad input:
its message is what the endpoint hands back to the agent, so it has to name
the offending field.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from jsonschema import Draft202012Validator

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schemas", "event_v1.json")

# The one schema version this build accepts (AC 2: reject unknown versions).
EVENT_VERSION = 1

# Cap on the JSON serialisation of ``payload``, in bytes. A tool_result or a
# command_run output can be a megabyte of text; the office wants the signal,
# not the blob, and the events feed is append-only with no retention yet.
PAYLOAD_MAX_BYTES = 65536


class EventValidationError(ValueError):
    """The body is not a valid v1 event. The endpoint answers 400."""


@dataclass(frozen=True)
class EventEnvelope:
    """A validated event, ready to persist.

    ``created_at`` is the normalised naive-UTC instant; ``timestamp`` keeps
    the string as the agent sent it, so the feed can echo what was posted.
    """

    agent: str
    event_type: str
    payload: dict
    created_at: datetime
    version: int = EVENT_VERSION
    timestamp: str = field(default="")


def load_schema(path: str = SCHEMA_PATH) -> dict:
    """Read the JSON Schema document. Called once at import."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


EVENT_SCHEMA_V1: dict = load_schema()
Draft202012Validator.check_schema(EVENT_SCHEMA_V1)
_VALIDATOR = Draft202012Validator(EVENT_SCHEMA_V1)


def _schema_error(instance: Mapping[str, Any]) -> Optional[str]:
    """The first structural violation, phrased with the field that caused it."""
    error = next(iter(_VALIDATOR.iter_errors(instance)), None)
    if error is None:
        return None
    where = "/".join(str(part) for part in error.absolute_path) or "event"
    return f"{where}: {error.message}"


def _check_version(instance: Mapping[str, Any]) -> None:
    """A readable message for the version rule the schema states as ``const``."""
    if "version" not in instance:
        return  # the required-property error already says it best
    version = instance["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise EventValidationError(
            f"version: must be the integer {EVENT_VERSION}, got {version!r}"
        )
    if version != EVENT_VERSION:
        raise EventValidationError(
            f"version: unknown schema version {version!r}; "
            f"this build accepts version={EVENT_VERSION}"
        )


def parse_timestamp(value: Any) -> datetime:
    """ISO 8601 date-time -> naive UTC, ADR-035's storage convention."""
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"timestamp: must be a non-empty ISO 8601 string, got {value!r}")
    text = value.strip()
    normalised = text[:-1] + "+00:00" if text[-1] in ("Z", "z") else text
    if not any(sep in normalised for sep in ("T", "t", " ")):
        raise EventValidationError(
            f"timestamp: {text!r} is not an ISO 8601 date-time (a time part is required)"
        )
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise EventValidationError(f"timestamp: {text!r} is not an ISO 8601 date-time ({exc})")
    if parsed.tzinfo is None:
        return parsed  # naive means UTC here
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _check_payload(payload: Any) -> None:
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > PAYLOAD_MAX_BYTES:
        raise EventValidationError(
            f"payload: {size} bytes exceeds the {PAYLOAD_MAX_BYTES}-byte cap; "
            "store large tool output outside the office and send a reference"
        )


def validate_event(data: Any) -> EventEnvelope:
    """Validate one request body against ``backend/schemas/event_v1.json``.

    Returns the normalised :class:`EventEnvelope`; raises
    :class:`EventValidationError` for anything the agents must be told about.
    """
    if not isinstance(data, Mapping):
        raise EventValidationError(
            f"event: expected a JSON object, got {_kind(data)}"
        )

    _check_version(data)
    error = _schema_error(data)
    if error is not None:
        raise EventValidationError(error)

    agent = data["agent"].strip()
    event_type = data["event_type"].strip()
    payload = data["payload"]
    _check_payload(payload)
    created_at = parse_timestamp(data["timestamp"])

    return EventEnvelope(
        agent=agent,
        event_type=event_type,
        payload=payload,
        created_at=created_at,
        version=data["version"],
        timestamp=data["timestamp"].strip(),
    )


def _kind(value: Any) -> str:
    """A JSON-ish type name for error messages (``null``, ``array``, ...)."""
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def schema_document() -> dict:
    """The loaded schema, for docs endpoints and tests."""
    return json.loads(json.dumps(EVENT_SCHEMA_V1))
