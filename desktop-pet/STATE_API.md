# Desktop Pet State Integration Guide (for AI Agents)

The desktop pet reads **state.json** to get the current state and refreshes its look (head icon/emoji, speech bubbles, character animation, pathfinding target). An integrating AI Agent must **write or update** this file to drive the pet.

---

## 1. File location

- **Path**: `state.json` in the desktop pet's working directory (on startup the pet resolves the project root, i.e. the directory containing `state.json` and `layers/`).
- **Format**: UTF-8 JSON.

---

## 2. state.json structure

```json
{
  "state": "idle",
  "detail": "optional status note, currently only used for display/debug",
  "progress": 0.0,
  "updated_at": "2025-02-27T12:00:00Z"
}
```

| Field          | Type    | Required | Description |
|----------------|---------|----------|-------------|
| `state`        | string  | yes      | Current state, see the table below. The pet polls and reads it every ~2s. |
| `detail`       | string  | no       | Optional description; may later be used for bubbles or debugging. |
| `progress`     | number  | no       | 0~1, optional progress; may be used by later extensions. |
| `updated_at`   | string  | no       | ISO8601 timestamp, optional. |

**Only `state` affects the pet's behavior**; the other fields can be left empty or omitted.

---

## 3. State values (the `state` an Agent should write)

The pet only recognizes these **standard state names** (lowercase). Any other value is treated as `idle` or mapped via an alias.

| state value    | Meaning              | Pet behavior summary |
|----------------|----------------------|----------------------|
| `idle`         | slacking / no task   | 💤 breathing animation, wanders randomly |
| `writing`      | writing / note-taking| Word icon, walks to the writing POI |
| `receiving`    | receiving message    | Hangouts icon, walks to the receiving POI |
| `replying`     | replying to message  | Glovo icon, walks to the replying POI |
| `researching`  | research / lookup    | Google icon, walks to the researching POI |
| `executing`    | running a task       | ⚡ emoji, walks to the executing POI |
| `syncing`      | sync / backup        | ☁️ emoji, walks to the syncing POI |
| `error`        | error occurred       | ❗ emoji, walks to the error POI |

POIs are configured under `pois` in `layers/map.json`; when the state changes, the pet pathfinds to the matching tile.

---

## 4. Alias mapping (optional)

If the Agent side uses different names, the pet frontend first maps **alias → standard state**, then behaves per the table above:

| state an Agent may write | mapped to |
|--------------------------|-----------|
| `working`                | `writing` |
| `run`                    | `executing` |
| `running`                | `executing` |
| `sync`                   | `syncing` |
| `research`               | `researching` |

Any `state` not in the list above is treated as `idle`.

---

## 5. What the Agent needs to “do”

- **Write state.json**: create/overwrite `state.json` in the agreed directory, ensuring `state` is one of the 8 standard states above (or one of the 5 aliases).
- **When to write**: writing once on each state change is enough; the pet polls about every 2 seconds, so no high-frequency writes are needed.
- **Examples**  
  - Start writing docs: `{ "state": "writing" }`  
  - Received a message: `{ "state": "receiving" }`  
  - Replying now: `{ "state": "replying" }`  
  - Looking things up: `{ "state": "researching" }`  
  - Executing a task: `{ "state": "executing" }`  
  - Syncing: `{ "state": "syncing" }`  
  - Error: `{ "state": "error" }`  
  - Slacking / no task: `{ "state": "idle" }`

Updating `state.json` this way keeps the pet in sync with its current state and POI behavior.
