import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PendingClaimV38Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_changed_modules_parse(self):
        for rel in ("bot/handlers.py", "bot/order_service.py", "bot/queue_service.py"):
            ast.parse(self.src(rel), filename=rel)

    def test_assignment_is_flushed_before_dispatch_queries(self):
        source = self.src("bot/queue_service.py")
        fn = source.split("def mark_assigned", 1)[1].split("def mark_offered", 1)[0]
        self.assertLess(fn.index('driver.driver_status = "busy"'), fn.index("session.flush()"))
        self.assertLess(fn.index("session.flush()"), fn.index("_notify_fronts(session)"))

    def test_pending_claim_uses_canonical_busy_assignment(self):
        source = self.src("bot/handlers.py")
        fn = source.split("def driver_take_pending", 1)[1].split("def _commission_for_order", 1)[0]
        self.assertIn('order.status = "assigned"', fn)
        self.assertIn("queue_service.mark_assigned(session, user)", fn)

    def test_offer_card_is_tracked_and_finalized_on_accept(self):
        service = self.src("bot/order_service.py")
        self.assertIn("order.offer_outbox_id = vk.send_tracked_message", service)
        self.assertIn("def finalize_offer_message", service)
        handlers = self.src("bot/handlers.py")
        accept = handlers.split("def driver_accept", 1)[1].split("def driver_contact_dispatcher", 1)[0]
        self.assertIn('finalize_offer_message(session, order, "✅ Вы приняли заявку.")', accept)


if __name__ == "__main__":
    unittest.main()
