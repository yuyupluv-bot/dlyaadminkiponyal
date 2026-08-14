import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class OnePendingOfferPerDriverV63Tests(unittest.TestCase):
    def src(self, rel): return (ROOT/rel).read_text("utf-8")

    def test_offer_reservation_is_flushed_immediately(self):
        queue=self.src("bot/queue_service.py")
        ast.parse(queue, filename="queue_service.py")
        mark=queue.split("def mark_offered",1)[1].split("def release_offer",1)[0]
        self.assertIn('entry.status = "offered"', mark)
        self.assertIn("session.flush()", mark)
        self.assertIn("return True", mark)

    def test_selection_excludes_live_offer_and_active_ride(self):
        queue=self.src("bot/queue_service.py")
        select=queue.split("def next_waiting_driver",1)[1].split("def has_waiting_driver",1)[0]
        self.assertIn("session.query(Order.offered_driver_id)", select)
        self.assertIn('Order.status == "searching"', select)
        self.assertIn("session.query(Order.driver_id)", select)

    def test_failed_reservation_moves_to_next_driver(self):
        service=self.src("bot/order_service.py")
        offer=service.split("def offer_to_next_driver",1)[1].split("def _accept_timeout",1)[0]
        self.assertIn("if not queue_service.mark_offered(session, driver):", offer)
        self.assertIn("excluded | {driver.id}", offer)

    def test_startup_repairs_existing_duplicate_cards(self):
        service=self.src("bot/order_service.py")
        repair=service.split("def reassign_duplicate_pending_offers",1)[1]
        self.assertIn("for duplicate in orders[1:]", repair)
        self.assertIn("temporary_exclude_driver_ids={driver_id}", repair)
        main=self.src("bot/main.py")
        self.assertIn("order_service.reassign_duplicate_pending_offers()", main)

if __name__=="__main__": unittest.main()
