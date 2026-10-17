"""Alembic 003 (SOU-226): drop the rows the deleted v1 state paths wrote, and the
agents left empty by that. Agents that also reported through POST /events keep
their row and every non-v1 event; a second run finds nothing; a dry run writes
nothing and leaves the stamp at 002."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import unittest

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from alembic import command
    from alembic.config import Config

    import db as db_mod
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - runtime extras missing
    sa = None
    _IMPORT_ERROR = exc

ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alembic.ini")


@contextlib.contextmanager
def _alembic_env(url: str):
    """Point every Alembic entry point at one URL and keep its logging quiet."""
    touched = ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")
    previous = {name: os.environ.get(name) for name in touched}
    os.environ["DATABASE_URL"] = url
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _upgrade(url: str, revision: str, x=None) -> None:
    cfg = Config(ALEMBIC_INI)
    cfg.set_main_option("sqlalchemy.url", url)
    if x:
        cfg.cmd_opts = type("Opts", (), {"x": x})()
    with _alembic_env(url):
        command.upgrade(cfg, revision)


V1_STATE = {"state": "idle", "detail": "Idle", "updated_at": "2026-10-15T12:00:00",
            "joinKey": "jk-secret", "authStatus": "approved", "name": "x", "source": "local"}


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
class DropV1RosterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workdir = tempfile.mkdtemp(prefix="sou226-")
        self.url = f"sqlite:///{os.path.join(self.workdir, 'v1.db')}"
        with contextlib.redirect_stdout(io.StringIO()):
            _upgrade(self.url, "002_resanitise_event_payloads")
        self.engine = db_mod.create_db_engine(self.url)
        maker = sessionmaker(bind=self.engine)
        with maker() as s:
            star = db_mod.insert_agent(s, name="Star", agent_type="main")
            db_mod.insert_event(s, star.id, "state_change", dict(V1_STATE, name="Star"))
            guest = db_mod.insert_agent(s, name="v1-guest", agent_type="guest")
            db_mod.insert_event(s, guest.id, "state_change", dict(V1_STATE, name="v1-guest"))
            mixed = db_mod.insert_agent(s, name="hermes-szarik", agent_type="guest")
            db_mod.insert_event(s, mixed.id, "state_change", dict(V1_STATE, name="hermes-szarik"))
            db_mod.insert_event(s, mixed.id, "tool_call", {"tool": "terminal", "command": "git push"})
            plain = db_mod.insert_agent(s, name="claude-code", agent_type="guest")
            db_mod.insert_event(s, plain.id, "tool_call", {"tool": "Read"})
            db_mod.insert_event(s, plain.id, "state_change", {"state": "thinking"})
            s.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def state(self):
        with self.engine.connect() as c:
            agents = {r[1]: r[0] for r in c.execute(sa.text("SELECT id, name FROM agents"))}
            events = [(r[0], r[1]) for r in c.execute(sa.text(
                "SELECT a.name, e.event_type FROM events e JOIN agents a ON a.id = e.agent_id ORDER BY a.name, e.event_type"))]
            stamp = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        return set(agents), events, stamp

    def test_v1_rows_and_empty_v1_agents_go_reporters_stay(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            _upgrade(self.url, "head")
        agents, events, stamp = self.state()
        self.assertEqual(stamp, "003_drop_v1_roster")
        self.assertEqual(agents, {"hermes-szarik", "claude-code"}, "Star and the v1-only guest are gone")
        self.assertEqual(events, [("claude-code", "state_change"), ("claude-code", "tool_call"),
                                  ("hermes-szarik", "tool_call")], "only v1 state rows were deleted")
        with self.engine.connect() as c:
            raw = " ".join(str(r[0]) for r in c.execute(sa.text("SELECT payload FROM events")))
        self.assertNotIn("jk-secret", raw)

    def test_dry_run_writes_nothing_and_keeps_the_stamp(self) -> None:
        before = self.state()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            _upgrade(self.url, "head", x=["dry_run=1"])
        self.assertEqual(self.state(), before)

    def test_second_run_finds_nothing(self) -> None:
        import importlib.util
        with contextlib.redirect_stdout(io.StringIO()):
            _upgrade(self.url, "head")
        path = os.path.join(os.path.dirname(ALEMBIC_INI), "migrations", "versions", "003_drop_v1_roster.py")
        spec = importlib.util.spec_from_file_location("m003", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with self.engine.begin() as c:
            summary = mod.drop_v1_roster(c)
        self.assertEqual((summary["v1_events"], summary["agents"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
