import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class BroadcastAndParallelV42Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_changed_modules_parse(self):
        for rel in ("bot/broadcast_service.py", "bot/parallel_orders.py", "bot/passenger_queue.py"):
            ast.parse(self.src(rel), filename=rel)

    def test_passenger_broadcast_excludes_drivers_and_blocked_users(self):
        source = self.src("bot/broadcast_service.py")
        self.assertIn("BlockedUser.vk_id", source)
        self.assertIn("User.is_blocked.is_(False)", source)
        passenger = source.split('elif target == "passenger"', 1)[1].split("else:", 1)[0]
        self.assertIn('set(u.roles_list()) == {"passenger"}', passenger)
        self.assertNotIn('u.has_role("passenger")', passenger)

    def test_parallel_decline_dispatches_to_free_drivers(self):
        source = self.src("bot/parallel_orders.py")
        self.assertIn("def _fallback_to_free_drivers", source)
        decline = source.split("def decline(session", 1)[1].split("def decline_route_offer", 1)[0]
        self.assertIn("_fallback_to_free_drivers(session, order, driver)", decline)
        route_decline = source.split("def decline_route_offer", 1)[1].split("def _fallback_to_free_drivers", 1)[0]
        self.assertIn("_fallback_to_free_drivers(session, order, driver)", route_decline)
        fallback = source.split("def _fallback_to_free_drivers", 1)[1].split("def _route_offer_timeout", 1)[0]
        self.assertIn('line_scope="exact"', fallback)
        self.assertIn('line_scope="normal"', fallback)
        self.assertIn("order_service.offer_to_next_driver", fallback)

    def test_queued_fallback_reaches_later_free_driver(self):
        source = self.src("bot/passenger_queue.py")
        branch = source.split("ROUTE_FALLBACK_REASON", 1)[1].split("elif free_city", 1)[0]
        self.assertIn('line_scope = "normal"', branch)
        self.assertIn("free_city or route_city", branch)

if __name__ == "__main__":
    unittest.main()
