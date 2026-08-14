from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(name)


class OrdinaryAndParallelTimeoutsV93(unittest.TestCase):
    def test_01_changed_sources_parse(self) -> None:
        for rel in (
            "bot/order_service.py", "bot/accept_timeout_service.py",
            "bot/main.py", "bot/parallel_orders.py",
        ):
            ast.parse(read(rel), filename=rel)

    def test_02_ordinary_timeout_removes_driver_from_line(self) -> None:
        body = function_source(read("bot/order_service.py"), "_accept_timeout")
        self.assertIn("queue_service.leave_queue(session, driver)", body)
        self.assertIn("driver.is_on_line = False", body)
        self.assertIn("clear_current_offer(session, order)", body)
        self.assertIn("session.flush()", body)
        self.assertIn("offer_to_next_driver(session, order, {driver_id})", body)
        self.assertIn("Вы сняты с линии", body)

    def test_03_watchdog_replays_only_overdue_ordinary_jobs(self) -> None:
        source = read("bot/accept_timeout_service.py")
        body = function_source(source, "reconcile_once")
        self.assertIn('ScheduledJob.kind == "accept"', body)
        self.assertIn('ScheduledJob.status == "pending"', body)
        self.assertIn("ScheduledJob.run_at <= time_utils.now()", body)
        self.assertIn("order_service._accept_timeout(order_id, driver_id)", body)
        self.assertNotIn("route_parallel_offer", body)
        self.assertIn("INTERVAL_SECONDS = 5", source)
        self.assertIn("accept_timeout_service.start_worker()", read("bot/main.py"))

    def test_04_parallel_timeout_keeps_driver_on_line(self) -> None:
        body = function_source(read("bot/parallel_orders.py"), "_route_offer_timeout")
        self.assertIn("current.parallel_auto_offers_disabled = True", body)
        self.assertIn("_fallback_to_free_drivers", body)
        self.assertNotIn("queue_service.leave_queue", body)
        self.assertNotIn("driver.is_on_line = False", body)

    def test_05_parallel_manual_list_remains_available(self) -> None:
        source = read("bot/parallel_orders.py")
        take = function_source(source, "take")
        show = function_source(source, "show")
        self.assertIn("if require_live_offer and (", take)
        self.assertIn("current.parallel_auto_offers_disabled", take)
        self.assertNotIn("parallel_auto_offers_disabled", show)


if __name__ == "__main__":
    unittest.main()
