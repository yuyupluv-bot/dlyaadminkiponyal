from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIMERS = ROOT / "bot/timers.py"
HANDLERS = ROOT / "bot/handlers.py"


def source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name!r} not found")


class _EmptyQuery:
    def filter(self, *args, **kwargs):
        return self

    def one_or_none(self):
        return None


class _UnflushedSession:
    """Minimal autoflush=False session double for the exact production bug."""

    def __init__(self) -> None:
        self.new: list[object] = []
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def query(self, *args, **kwargs):
        return _EmptyQuery()

    def add(self, row) -> None:
        self.new.append(row)


class IdempotentDriverAwayTimerV88(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.timers_source = source(TIMERS)
        cls.handlers_source = source(HANDLERS)

    def test_01_changed_modules_parse(self) -> None:
        ast.parse(self.timers_source, filename=str(TIMERS))
        ast.parse(self.handlers_source, filename=str(HANDLERS))

    def test_02_second_schedule_reuses_unflushed_same_key(self) -> None:
        # Reproduces the log: two driver_away schedules in one transaction
        # whose SELECT cannot see the first pending INSERT with autoflush off.
        from bot import timers

        class _Field:
            def __eq__(self, _other):
                return True

        class ScheduledJob:
            job_key = _Field()

            def __init__(self, **values) -> None:
                self.__dict__.update(values)

        # The test sandbox intentionally does not install SQLAlchemy.  The
        # timer function imports these two modules at call time, so lightweight
        # modules let this test execute the same autoflush=False branch without
        # needing a live database driver.
        database_module = types.ModuleType("common.database")
        db = _UnflushedSession()
        database_module.current_session = lambda: db
        models_module = types.ModuleType("common.models")
        models_module.ScheduledJob = ScheduledJob
        key = "driver_away:1"
        with patch.object(timers, "_DB_AVAILABLE", True), patch.dict(
            sys.modules,
            {
                "common.database": database_module,
                "common.models": models_module,
            },
        ):
            timers.schedule("driver_away", 1, 1800, lambda: None)
            timers.schedule("driver_away", 1, 1800, lambda: None)
        try:
            jobs = [row for row in db.new if isinstance(row, ScheduledJob)]
            self.assertEqual(1, len(jobs))
            self.assertEqual(key, jobs[0].job_key)
            self.assertEqual("pending", jobs[0].status)
        finally:
            with timers._condition:
                timers._entries.pop(key, None)
                timers._condition.notify()

    def test_03_postgres_schedule_uses_atomic_upsert(self) -> None:
        schedule = function_source(self.timers_source, "schedule")
        self.assertIn("for candidate in db.new", schedule)
        self.assertIn("on_conflict_do_update", schedule)
        self.assertIn("db.execute(stmt)", schedule)
        self.assertIn("ScheduledJob.job_key", schedule)

    def test_04_ride_completion_has_one_away_transition(self) -> None:
        normal = function_source(self.handlers_source, "driver_complete_ride")
        delivery = function_source(self.handlers_source, "driver_complete_delivery")
        rating = function_source(self.handlers_source, "_ask_driver_rate_passenger")
        self.assertNotIn("queue_service.set_away(session, user)", normal)
        self.assertNotIn("queue_service.set_away(session, user)", delivery)
        self.assertIn("_ask_driver_rate_passenger(session, user, order)", normal)
        self.assertIn("_ask_driver_rate_passenger(session, user, order)", delivery)
        self.assertIn("lines.ask_post_ride_line(session, user)", rating)


if __name__ == "__main__":
    unittest.main()
