import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class PassengerActualityOneMinuteV61Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT/rel).read_text("utf-8")

    def test_queue_actuality_timeout_is_60_seconds(self):
        config=self.src("common/config.py")
        queue=self.src("bot/passenger_queue.py")
        self.assertIn('PASSENGER_POLL_TIMEOUT: int = int(_get("PASSENGER_POLL_TIMEOUT", "60"))', config)
        self.assertIn('get_int(session, "passenger_poll_timeout", 60)', queue)
        self.assertIn('timers.schedule("pqueue", order.id, timeout', queue)

    def test_chat_actuality_timeout_is_60_seconds(self):
        handlers=self.src("bot/handlers.py")
        self.assertIn('timers.schedule("chat_actual", order.id, 60', handlers)
        self.assertIn("silent for 1 minute", handlers)

    def test_database_is_forced_to_new_timeout(self):
        guard=self.src("common/db_migrate.py")
        self.assertIn("UPDATE settings SET value='60' WHERE key='passenger_poll_timeout'", guard)
        migration=self.src("migrations/versions/0039_passenger_actuality_60.py")
        ast.parse(migration, filename="0039_passenger_actuality_60.py")
        self.assertIn('revision = "0039_passenger_actuality_60"', migration)
        self.assertIn('down_revision = "0038_driver_chat_45_minutes"', migration)
        self.assertIn("SET value='60'", migration)

if __name__=="__main__": unittest.main()
