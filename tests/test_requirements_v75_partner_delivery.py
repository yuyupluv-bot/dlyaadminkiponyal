# -*- coding: utf-8 -*-
"""V75: уточнения по доставкам от партнёров (Красный бархат / Галактика)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLERS_PATH = ROOT / "bot" / "handlers.py"
HANDLERS = HANDLERS_PATH.read_text("utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("function not found: " + name)


class PartnerDeliveryV75(unittest.TestCase):
    def test_modules_parse(self):
        ast.parse(HANDLERS, filename="bot/handlers.py")

    # 1. Кнопка реквизитов не показывается водителю.
    def test_no_payment_details_button_for_partner_delivery(self):
        src = function_source(HANDLERS, "_driver_ride_kb")
        self.assertIn("is_partner_delivery = delivery_service.is_partner_delivery(order)", src)
        self.assertIn("and not is_partner_delivery", src)
        self.assertIn("partner_delivery=is_partner_delivery", src)

    def test_payment_details_command_is_guarded(self):
        src = function_source(HANDLERS, "driver_send_payment_details")
        self.assertIn("if delivery_service.is_partner_delivery(order):", src)
        guard = src.split("if delivery_service.is_partner_delivery(order):", 1)[1]
        self.assertIn("return vk.send_message", guard.split("if not user.show_payment_details", 1)[0])

    # 2. Актуальность доставки не спрашивается.
    def test_actuality_is_not_requested(self):
        src = function_source(HANDLERS, "_apply_eta")
        branch = src.split("if is_partner_delivery:", 1)[1].split("else:", 1)[0]
        self.assertIn("order.actuality_confirmed = True", branch)
        self.assertNotIn("\u0414\u043e\u0441\u0442\u0430\u0432\u043a\u0430 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u0430?", HANDLERS)

    def test_passenger_departure_keyboard_is_skipped(self):
        src = function_source(HANDLERS, "_apply_eta")
        confirmed = src.split("if order.actuality_confirmed:", 1)[1].split("else:", 1)[0]
        self.assertIn("kb.passenger_ride_keyboard()", confirmed)

    # 3. Текст после «Забрал заказ».
    def test_picked_up_text(self):
        src = function_source(HANDLERS, "driver_seated")
        self.assertIn(
            "\u0412\u043e\u0434\u0438\u0442\u0435\u043b\u044c \u0437\u0430\u0431\u0440\u0430\u043b \u0437\u0430\u043a\u0430\u0437 \u0438 \u0432\u0435\u0437\u0451\u0442 \u0435\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0443.",
            src,
        )
        self.assertNotIn(
            "\u0437\u0430\u0431\u0440\u0430\u043b \u0432\u0430\u0448 \u0437\u0430\u043a\u0430\u0437 \u0438 \u0432\u0435\u0437\u0451\u0442 \u0435\u0433\u043e \u0432\u0430\u043c",
            src,
        )

    # 4. Водитель не оценивает заказчика.
    def test_driver_does_not_rate_partner_delivery(self):
        src = function_source(HANDLERS, "_ask_driver_rate_passenger")
        head = src.split("if not _is_dispatcher_order(order)", 1)[0]
        self.assertIn("if delivery_service.is_partner_delivery(order):", head)
        self.assertIn("return lines.ask_post_ride_line(session, user)", head)

    def test_customer_still_rates_the_ride(self):
        src = function_source(HANDLERS, "driver_complete_ride")
        self.assertIn("kb.rating_keyboard(order.id)", src)

    # 5. Итог по поездке не пишется.
    def test_ride_total_is_not_sent_for_partner_delivery(self):
        src = function_source(HANDLERS, "driver_complete_ride")
        marker = "\u0418\u0442\u043e\u0433 \u043f\u043e \u043f\u043e\u0435\u0437\u0434\u043a\u0435"
        self.assertIn(marker, src)
        before = src.split(marker, 1)[0]
        tail = before.rsplit("if not delivery_service.is_partner_delivery(order):", 1)
        self.assertEqual(len(tail), 2, "total is not guarded by the partner check")
        # Гуард должен стоять непосредственно перед отправкой итога.
        between = tail[1]
        self.assertLessEqual(between.count(chr(10)), 4, between)
        self.assertIn("vk.send_message(", between)
        self.assertNotIn("vk.send_tracked_message", between)


if __name__ == "__main__":
    unittest.main()
