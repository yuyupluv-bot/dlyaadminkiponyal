# -*- coding: utf-8 -*-
"""V94: driver cancellation reason requested by the customer."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("function %s not found" % name)


class DriverCustomerRequestedCancellationV94(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handlers = read("bot/handlers.py")
        cls.keyboards = read("bot/keyboards.py")
        cls.cancel = function_source(cls.handlers, "driver_cancel_active")
        cls.grace = function_source(cls.handlers, "_passenger_within_grace")

    def test_01_cancel_menu_has_requested_reason(self):
        menu = function_source(self.keyboards, "driver_active_cancel_keyboard")
        self.assertIn("Клиент захотел отменить", menu)
        self.assertIn('"cmd":"driver_cancel_customer"', menu)

    def test_02_command_routes_to_new_reason(self):
        self.assertIn(
            'if cmd == "driver_cancel_customer":\n        return driver_cancel_active(session, user, "customer_cancel")',
            self.handlers,
        )
        self.assertIn('reason not in ("no_show", "customer_cancel", "car")', self.cancel)

    def test_03_two_minutes_start_after_eta(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        self.assertIn("order.driver_departed_at", branch)
        self.assertIn("not _passenger_within_grace(session, order)", branch)
        self.assertIn('get_int(session, "passenger_cancel_grace_seconds", 120)', self.grace)
        self.assertIn("<= grace", self.grace)

    def test_04_only_strictly_past_grace_creates_false_call(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        past = branch.split("if past_grace:", 1)[1].split("else:", 1)[0]
        free = branch.split("if past_grace:", 1)[1].split("else:", 1)[1].split("elif passenger", 1)[0]
        self.assertIn("fake_calls_service.create(session, order, user)", past)
        self.assertNotIn("fake_calls_service.create", free)
        self.assertIn("not _is_dispatcher_order(order)", branch)

    def test_05_order_is_cancelled_as_passenger_request(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        self.assertIn('order.status = "cancelled"', branch)
        self.assertIn('order.cancelled_by = "passenger"', branch)
        self.assertIn("timers.cancel_all_for_order(order.id)", self.cancel)
        self.assertIn("parallel_orders.release_route_offers_for_trip(session, order)", self.cancel)

    def test_06_free_cancel_returns_passenger_to_menu(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        self.assertIn("Ваша заявка отменена по вашей просьбе.", branch)
        self.assertIn("States.MAIN_MENU", branch)
        self.assertIn("kb.passenger_menu(", branch)

    def test_07_driver_gets_post_ride_line_choice(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        self.assertIn("lines.ask_post_ride_line(session, user)", branch)
        chooser = function_source(read("bot/keyboards.py"), "post_ride_line_keyboard")
        for label in ("Остаться", "Сменить линию", "Отлучился", "Выйти с линии"):
            self.assertIn(label, chooser)

    def test_08_existing_parallel_order_is_preserved(self):
        branch = self.cancel.split('if reason == "customer_cancel":', 1)[1].split('if reason == "no_show":', 1)[0]
        self.assertIn("parallel_orders.has_pending(session, user)", branch)
        self.assertIn("parallel_orders.promote_after_current(session, user)", branch)


if __name__ == "__main__":
    unittest.main()
