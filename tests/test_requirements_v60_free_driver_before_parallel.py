import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class FreeDriverBeforeParallelV60Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT/rel).read_text("utf-8")

    def test_parallel_list_notifications_and_take_respect_free_priority(self):
        source=self.src("bot/parallel_orders.py")
        ast.parse(source, filename="bot/parallel_orders.py")
        helper=source.split("def _free_driver_has_priority",1)[1].split("def available",1)[0]
        self.assertIn("order.offered_driver_id or order.offer_outbox_id", helper)
        self.assertIn("order_service.has_eligible_waiting_driver(session, order)", helper)
        available=source.split("def available",1)[1].split("def _parallel_candidate_filter",1)[0]
        self.assertIn("if not _free_driver_has_priority(session, order)", available)
        eligible=source.split("def has_eligible_busy_driver_for_order",1)[1].split("def _update_existing_driver_menu",1)[0]
        self.assertIn("if _free_driver_has_priority(session, order):", eligible)
        notify=source.split("def notify_busy_drivers",1)[1].split("def notify_assigned_to_free_driver",1)[0]
        self.assertIn("if _free_driver_has_priority(session, order):", notify)
        take=source.split("def take",1)[1].split("def decline_route_offer",1)[0]
        self.assertIn("if _free_driver_has_priority(session, order):", take)

    def test_new_route_order_checks_free_driver_before_parallel(self):
        source=self.src("bot/passenger_queue.py")
        dispatch=source.split("def dispatch_new_order",1)[1].split("def _dispatcher_unclaimed_timeout",1)[0]
        free=dispatch.index("if order_service.has_eligible_waiting_driver(session, order):")
        parallel=dispatch.index("if parallel_orders.has_departed_driver_to_city(session, route_city):")
        self.assertLess(free, parallel)

    def test_recovery_no_longer_suppresses_free_driver_for_route_parallel(self):
        source=self.src("bot/passenger_queue.py")
        promote=source.split("def try_promote",1)[1].split("def _recovery_worker",1)[0]
        self.assertNotIn("preserve the second-tier parallel", promote)
        self.assertIn("Any eligible free driver always has priority", promote)

if __name__=="__main__": unittest.main()
