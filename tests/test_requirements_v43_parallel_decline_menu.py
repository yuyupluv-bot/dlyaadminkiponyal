import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ParallelDeclineMenuV43Tests(unittest.TestCase):
    def source(self):
        return (ROOT / "bot/parallel_orders.py").read_text("utf-8")

    def test_module_parses(self):
        ast.parse(self.source(), filename="bot/parallel_orders.py")

    def test_full_active_menu_is_restored(self):
        source = self.source()
        helper = source.split("def _restore_active_menu_after_parallel_decline", 1)[1].split("def decline(session", 1)[0]
        self.assertIn("States.D_IN_RIDE", helper)
        self.assertIn("from .handlers import _driver_ride_kb", helper)
        self.assertIn("keyboard=_driver_ride_kb(session, current)", helper)

    def test_every_decline_path_restores_menu(self):
        source = self.source()
        reserved = source.split("def decline(session", 1)[1].split("def decline_route_offer", 1)[0]
        self.assertIn("_restore_active_menu_after_parallel_decline(session, driver, current)", reserved)
        route = source.split("def decline_route_offer", 1)[1].split("def _fallback_to_free_drivers", 1)[0]
        self.assertIn("_restore_active_menu_after_parallel_decline(session, driver, current)", route)
        release = source.split("def _release", 1)[1].split("def release_reserved", 1)[0]
        self.assertIn("_restore_active_menu_after_parallel_decline(session, driver, current)", release)

if __name__ == "__main__":
    unittest.main()
