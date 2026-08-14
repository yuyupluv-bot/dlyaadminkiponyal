from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(name)


class DeclineHandoffAndParallelQueueV91(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handlers = read("bot/handlers.py")
        cls.orders = read("bot/order_service.py")
        cls.queue = read("bot/queue_service.py")
        cls.parallel = read("bot/parallel_orders.py")

    def test_01_sources_parse_without_replacement_characters(self) -> None:
        for source in (self.handlers, self.orders, self.queue, self.parallel):
            ast.parse(source)
            self.assertNotIn("\ufffd", source)

    def test_02_decline_releases_both_offer_ownership_markers(self) -> None:
        clear = function_source(self.orders, "clear_current_offer")
        self.assertIn("order.offered_driver_id = None", clear)
        self.assertIn('{"current_offer": None}', clear)
        decline = function_source(self.handlers, "driver_decline")
        release = decline.index("order_service.clear_current_offer(session, order)")
        flush = decline.index("session.flush()", release)
        dispatch = decline.index("order_service.offer_to_next_driver(session, order)", flush)
        self.assertLess(release, flush)
        self.assertLess(flush, dispatch)

    def test_03_every_ordinary_reason_is_handed_to_next_driver(self) -> None:
        decline = function_source(self.handlers, "driver_decline")
        self.assertIn('if cat == "away":', decline)
        self.assertIn('if cat == "dislike":', decline)
        self.assertIn('label = "доставки" if cat == "delivery" else "дальней поездки"', decline)
        self.assertGreaterEqual(
            decline.count("order_service.offer_to_next_driver(session, order)"), 3
        )
        self.assertNotIn("publish_special_decline_to_requests_chat", decline)

    def test_04_leaving_or_stepping_away_also_releases_offer_lock(self) -> None:
        for name in ("driver_go_offline", "driver_go_away"):
            body = function_source(self.handlers, name)
            self.assertIn("order_service.clear_current_offer(session, pending)", body)
            self.assertIn("session.flush()", body)
            self.assertIn("order_service.offer_to_next_driver(session, pending)", body)

    def test_05_parallel_reservation_repairs_driver_to_busy(self) -> None:
        take = function_source(self.parallel, "take")
        assigned = take.index('order.status = "parallel_assigned"')
        marked = take.index("queue_service.mark_assigned(session, driver)")
        self.assertLess(assigned, marked)

    def test_06_dispatch_never_selects_parallel_driver(self) -> None:
        next_driver = function_source(self.queue, "next_waiting_driver")
        self.assertIn("session.query(Order.parallel_driver_id)", next_driver)
        self.assertIn('Order.status == "parallel_assigned"', next_driver)
        repair = function_source(self.queue, "_repair_visibly_free_drivers")
        self.assertIn("parallel_driver_ids", repair)
        self.assertIn("active_driver_ids.update(parallel_driver_ids)", repair)

    def test_07_parallel_driver_has_no_free_rank_or_free_list_entry(self) -> None:
        for name in ("driver_queue_rank", "driver_line_rank"):
            self.assertIn(
                "_driver_has_active_work(session, driver.id)",
                function_source(self.queue, name),
            )
        for name in ("free_drivers", "free_drivers_on_line"):
            self.assertIn(
                "not _driver_has_active_work(session, driver.id)",
                function_source(self.queue, name),
            )

    def test_08_queue_uses_live_parallel_status_and_sorts_it_as_busy(self) -> None:
        actual = function_source(self.queue, "actual_driver_statuses")
        self.assertIn("session.query(Order.parallel_driver_id)", actual)
        entries = function_source(self.queue, "queue_entries")
        self.assertIn("live_statuses = actual_driver_statuses", entries)
        self.assertIn('effective_status = "assigned"', entries)
        screen = function_source(self.handlers, "show_queue")
        self.assertIn("queue_service.actual_driver_statuses", screen)
        self.assertIn('live_status == "busy"', screen)


if __name__ == "__main__":
    unittest.main()
