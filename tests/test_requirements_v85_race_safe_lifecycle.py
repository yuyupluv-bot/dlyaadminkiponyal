from __future__ import annotations

import ast
import pathlib
import unittest
from typing import Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot/handlers.py"
ORDER_SERVICE = ROOT / "bot/order_service.py"
PARALLEL = ROOT / "bot/parallel_orders.py"
PASSENGER_QUEUE = ROOT / "bot/passenger_queue.py"
DELIVERY = ROOT / "bot/delivery_service.py"
TIMERS = ROOT / "bot/timers.py"
ADMIN_CANCEL = ROOT / "bot/admin_order_cancel_service.py"
CONFIG = ROOT / "common/config.py"
SETTINGS = ROOT / "common/settings_service.py"
MIGRATION_0004 = ROOT / "migrations/versions/0004_extra_features.py"


def source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name!r} not found")


class RaceSafeLifecycleV85(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handlers = source(HANDLERS)
        cls.order_service = source(ORDER_SERVICE)
        cls.parallel = source(PARALLEL)
        cls.passenger_queue = source(PASSENGER_QUEUE)
        cls.delivery = source(DELIVERY)
        cls.timers = source(TIMERS)
        cls.admin_cancel = source(ADMIN_CANCEL)
        cls.config = source(CONFIG)
        cls.settings = source(SETTINGS)

    def test_01_changed_modules_parse(self) -> None:
        for path in (
            HANDLERS, ORDER_SERVICE, PARALLEL, PASSENGER_QUEUE,
            DELIVERY, TIMERS, ADMIN_CANCEL, CONFIG, SETTINGS, MIGRATION_0004,
        ):
            ast.parse(source(path), filename=str(path))

    def test_02_driver_offer_default_is_60_but_setting_is_live(self) -> None:
        offer = function_source(self.order_service, "offer_to_next_driver")
        self.assertIn('get_int(session, "driver_accept_timeout", 60)', offer)
        self.assertIn('DRIVER_ACCEPT_TIMEOUT", "60"', self.config)
        self.assertIn('"driver_accept_timeout": "60"', self.settings)

    def test_03_delivery_confirmation_default_is_120_but_setting_is_live(self) -> None:
        submit = function_source(self.delivery, "submit_price")
        self.assertIn('get_int(session, "delivery_confirm_timeout", 120)', submit)
        self.assertIn('"delivery_confirm_timeout": "120"', self.settings)
        self.assertIn('(\"delivery_confirm_timeout\", \"120\")', source(MIGRATION_0004))

    def test_04_decline_reason_locks_and_revalidates_exact_offer(self) -> None:
        body = function_source(self.handlers, "driver_decline")
        self.assertIn(".with_for_update()", body)
        self.assertIn("order_service._current_offer(session, order) != user.id", body)
        self.assertLess(body.index(".with_for_update()"), body.index('timers.cancel("accept"'))

    def test_05_decline_prompt_and_back_reject_stale_buttons(self) -> None:
        for name in ("driver_decline_prompt", "driver_decline_back"):
            body = function_source(self.handlers, name)
            self.assertIn("order_service._current_offer(session, order) != user.id", body)

    def test_06_accept_timeout_serializes_with_accept_and_decline(self) -> None:
        body = function_source(self.order_service, "_accept_timeout")
        self.assertIn(".with_for_update()", body)
        self.assertLess(body.index(".with_for_update()"), body.index("_current_offer(session, order)"))

    def test_07_parallel_eta_click_and_timeout_both_lock(self) -> None:
        for name in ("save_eta", "_eta_timeout"):
            body = function_source(self.parallel, name)
            self.assertIn(".with_for_update()", body)

    def test_08_actuality_click_and_timeout_both_lock_same_rows(self) -> None:
        for name in ("confirm", "_poll_timeout"):
            body = function_source(self.passenger_queue, name)
            self.assertGreaterEqual(body.count(".with_for_update()"), 2)
            self.assertLess(body.index("PassengerQueue"), body.index(".filter(Order.id"))

    def test_09_delivery_click_and_timeout_both_lock(self) -> None:
        for name in ("passenger_response", "_confirm_timeout"):
            body = function_source(self.delivery, name)
            self.assertIn(".with_for_update()", body)
            self.assertIn("order.actuality_confirmed", body)

    def test_10_delivery_has_durable_answer_marker(self) -> None:
        submit = function_source(self.delivery, "submit_price")
        confirm = function_source(self.delivery, "_confirm")
        timeout = function_source(self.delivery, "_confirm_timeout")
        self.assertIn("order.actuality_confirmed = False", submit)
        self.assertIn("order.actuality_confirmed = True", confirm)
        self.assertIn("or order.actuality_confirmed", timeout)

    def test_11_driver_cancellation_closes_live_route_offer(self) -> None:
        body = function_source(self.handlers, "driver_cancel_active")
        self.assertIn(".with_for_update()", body)
        self.assertIn(".populate_existing()", body)
        self.assertIn("parallel_orders.release_route_offers_for_trip(session, order)", body)
        self.assertLess(
            body.index("release_route_offers_for_trip"),
            body.index('if reason == "no_show"'),
        )

    def test_12_dispatcher_cancellation_promotes_reserved_parallel(self) -> None:
        body = function_source(self.handlers, "disp_cancel_order")
        self.assertIn("parallel_orders.release_route_offers_for_trip(session, order)", body)
        self.assertIn("parallel_orders.has_pending(session, driver)", body)
        self.assertIn("parallel_orders.promote_after_current(session, driver)", body)

    def test_13_admin_cancellation_promotes_and_keeps_ride_keyboard(self) -> None:
        body = function_source(self.admin_cancel, "cancel_from_admin")
        self.assertIn(".with_for_update()", body)
        self.assertIn(".populate_existing()", body)
        self.assertIn("parallel_orders.release_route_offers_for_trip(session, order)", body)
        self.assertIn("parallel_orders.has_pending(session, assigned)", body)
        self.assertIn("parallel_orders.promote_after_current(session, assigned)", body)
        self.assertIn("promoted_order is not None", body)
        self.assertIn("kb.driver_ride_keyboard(", body)

    def test_14_failed_timer_is_retried_and_not_deleted_first(self) -> None:
        body = function_source(self.timers, "_safe_run")
        self.assertIn("_persist_retry(kind, order_id, retry_delay, exc)", body)
        self.assertIn("schedule(kind, order_id, retry_delay, callback, _persist=False)", body)
        self.assertIn("if _key(kind, order_id) not in _entries", body)
        self.assertIn("_delete_persistent(kind, order_id)", body)
        self.assertLess(body.index("callback()"), body.index("_delete_persistent"))

    def test_15_safe_run_order_is_correct_dynamically(self) -> None:
        events: list[str] = []

        class Log:
            def exception(self, *args, **kwargs):
                events.append("logged")

        namespace = {
            "Callable": Callable,
            "log": Log(),
            "_persist_retry": lambda *args: events.append("persisted"),
            "schedule": lambda *args, **kwargs: events.append("scheduled"),
            "_delete_persistent": lambda *args: events.append("deleted"),
            "_condition": __import__("threading").Condition(),
            "_entries": {},
            "_key": lambda kind, object_id: f"{kind}:{object_id}",
        }
        exec(body := function_source(self.timers, "_safe_run"), namespace)
        namespace["_safe_run"]("accept", 1, lambda: events.append("callback"))
        self.assertEqual(events, ["callback", "deleted"])

        events.clear()
        def fail() -> None:
            events.append("callback")
            raise RuntimeError("temporary")
        namespace["_safe_run"]("accept", 1, fail)
        self.assertEqual(events, ["callback", "logged", "scheduled", "persisted"])
        self.assertNotIn("deleted", events)

    def test_16_timer_stays_pending_until_success_for_restart_replay(self) -> None:
        restore = function_source(self.timers, "restore_persistent")
        self.assertIn('ScheduledJob.status == "pending"', restore)


if __name__ == "__main__":
    unittest.main()
