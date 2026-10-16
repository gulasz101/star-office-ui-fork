# Re-sanitising the stored event payloads (SOU-235, migration 002)

One-off data migration: every `events.payload` row stored **before SOU-218 put
the sanitiser on the `/events` door** is raw — full command lines, absolute
home paths, URLs with credentials, free-text detail, join keys. Revision
`002_resanitise_event_payloads` walks the table once and rewrites each such row
through `backend/sanitize.sanitize_payload(event_type, payload)`, the exact
projection the door applies today.

What it does **not** do, by ruling on the ticket (comment on SOU-235):

- It leaves the v1 state-door rows alone — the `state_change` rows written by
  `save_agents_state` / the `Star` roster / `POST /set_state`, recognised by
  the `updated_at` marker and the v1 key set. Sanitising those would delete the
  `detail` / `joinKey` / `authStatus` fields `load_agents_state()` and
  `GET /status` read back, breaking v1 until it is retired. They are the
  residual the dry run reports as `skipped_v1` / `skipped_v1_sensitive`; SOU-234
  (door on the v1 paths) and SOU-226/v1 deletion close it.
- It never deletes events, touches `agent_id`, `event_type`, `created_at`, or
  any other table. `downgrade` moves the version stamp only — the raw bytes are
  gone by design; restore the backup copy if you need them.

Run it on the **M1 Max deployment** (`~/sou-deploy/backend/sou.db`, or the
container's Postgres). **Claude Code or Szarik performs the run** — the t460
worker wrote and tested this against throwaway fixture databases only (see the
AC2 evidence on SOU-235/SOU-241) and never against the real DB.

## 0. Backup first — non-negotiable

SQLite:

```bash
cd ~/sou-deploy
cp backend/sou.db backend/sou.db.bak-$(date +%Y%m%d-%H%M)
```

Postgres (if the deployment uses it, per `backend/.env` `DATABASE_URL`):

```bash
pg_dump "$DATABASE_URL" -Fc -f sou-$(date +%Y%m%d-%H%M).dump
```

## 1. Pull the merged branch

This ships only after Claude Code has merged `sou-235-resanitise-migration` to
`master` and the deploy is updated:

```bash
cd ~/sou-deploy && git pull --ff-only origin master && uv sync
```

## 2. Dry run (prints counts, writes nothing)

Either of these — both are read-only for the data. The second is the revision
itself refusing to commit:

```bash
uv run python -m backend.resanitise --dry-run
uv run alembic -c backend/alembic.ini -x dry_run=1 upgrade head   # rolls back on purpose
```

Expected shape (numbers will differ; this is what a 1200-row fixture produced):

```
resanitise (dry-run, nothing written): scanned=1200 changed=930 unchanged=180 skipped_v1=90 skipped_v1_sensitive=90 corrupt=0 updates=0 batch_size=500
```

- `changed` should be ≈ the rows that arrived before the SOU-218 door.
- `skipped_v1` are the protected rows; `skipped_v1_sensitive` is how many of
  them still visibly carry free text or a join key — the accepted residual.
- After the dry run, `alembic_version` must still read `001_initial_schema`
  (`sqlite3 backend/sou.db 'select * from alembic_version'`).

## 3. Apply

```bash
uv run alembic -c backend/alembic.ini upgrade head
```

(If the container entrypoint runs migrations on start, simply restarting the
backend applies it too — the entrypoint executes `alembic upgrade head`.)

Postgres deployment: export the same `DATABASE_URL` you run the backend with
before both commands; nothing else changes.

## 4. Verify

```bash
uv run python scripts/inspect_event_payloads.py --database "sqlite:///backend/sou.db" --expect-clean
uv run python -m backend.resanitise --re-scan --dry-run   # must report changed=0
```

`--expect-clean` exits 1 if any sanitisable row still carries a marker. On the
real DB, use it as a smoke read (its marker patterns are fixture-shaped); the
authoritative check is the second line: a full re-scan dry run reporting
`changed=0` means every reachable row is at its sanitised projection.

Spot-check the app still behaves: `curl -s localhost:19000/status | head -c
400` and `GET /events/recent?limit=20` — v1 rows keep their `detail`/`joinKey`
by ruling, everything else is now the projection.

## 5. Rollback

There is no data rollback: `alembic downgrade 001_initial_schema` un-stamps but
does not restore raw payloads. To undo, stop the backend, restore the step-0
backup, and start again.

## Notes

- Idempotent: re-running is a no-op (`changed=0`); rows that arrive between the
  stamp and the run are handled by `--re-scan` (walks without touching the
  stamp; the normal upgrade path is `alembic upgrade head`).
- Batched keyset walk (default 500/batch, `--batch-size` to change): no OFFSET,
  no full-table load; works on opaque `VARCHAR(36)` ids and legacy integer ids.
- Unparseable JSON payloads are written as `{}` and counted as `corrupt` — same
  treatment the door gives them; on Postgres JSONB cannot even store those, so
  `corrupt` there is expected to be 0.
