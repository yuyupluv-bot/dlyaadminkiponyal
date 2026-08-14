# -*- coding: utf-8 -*-
"""V74: доставка от партнёров (Красный бархат / Галактика)."""
import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
DELIVERY_PATH = BOT / "delivery_service.py"
HANDLERS = (BOT / "handlers.py").read_text(encoding="utf-8")
KEYBOARDS = (BOT / "keyboards.py").read_text(encoding="utf-8")
ORDER_SERVICE = (BOT / "order_service.py").read_text(encoding="utf-8")
DELIVERY = DELIVERY_PATH.read_text(encoding="utf-8")


def load_delivery_matchers():
    """Execute only the pure regex part of delivery_service (no SQLAlchemy)."""
    tree = ast.parse(DELIVERY)
    wanted_functions = {
        "_normalize",
        "has_delivery_word",
        "is_partner_text",
        "is_partner_delivery_text",
        "is_delivery_text",
    }
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(name in targets for name in (
                "PARTNER_DELIVERY_TYPE", "_DELIVERY_WORD", "_PARTNER_PATTERNS",
            )):
                body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            body.append(node)
    namespace = {"re": re}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(DELIVERY_PATH), "exec"), namespace)
    return namespace


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("Function %s not found" % name)


MATCHERS = load_delivery_matchers()


class PartnerRecognition(unittest.TestCase):
    """Партнёр + слово «доставка» = доставка от партнёров."""

    def partner_delivery(self, text):
        return MATCHERS["is_partner_delivery_text"](text)

    def partner(self, text):
        return MATCHERS["is_partner_text"](text)

    def test_partner_names_recognized(self):
        for text in [
            "Красный бархат",
            "красного бархата",
            "красном бархате",
            "Красный Бархот",
            "кр. бархат",
            "кр бархата",
            "красный-бархат",
            "бархат",
            "бархота",
            "krasniy barhat",
            "barhat",
            "Галактика",
            "галактики",
            "галактику",
            "галактике",
            "галлактика",
            "галактка",
            "galaktika",
            "galactica",
        ]:
            self.assertTrue(self.partner(text), text)

    def test_partner_delivery_requires_delivery_word(self):
        for text in [
            "Доставка из красного бархата на Мира 7",
            "красный бархат доставить на Ленина 12",
            "доставьте из галактики продукты",
            "Галактика, доставка на Кирова 3",
            "кр бархат доставка",
        ]:
            self.assertTrue(self.partner_delivery(text), text)

    def test_partner_without_delivery_word_is_regular_ride(self):
        for text in [
            "от красного бархата до Ленина 5",
            "галактика - мира 7",
            "от галактики до кусьи",
        ]:
            self.assertFalse(self.partner_delivery(text), text)
            self.assertFalse(MATCHERS["is_delivery_text"](text), text)

    def test_delivery_word_without_partner_stays_ordinary_delivery(self):
        for text in ["доставка цветов на Мира 7", "доставить посылку"]:
            self.assertTrue(MATCHERS["is_delivery_text"](text), text)
            self.assertFalse(self.partner_delivery(text), text)

    def test_plain_rides_are_not_deliveries(self):
        for text in ["от фасоли до кусьи", "Мира 7 - Ленина 12"]:
            self.assertFalse(MATCHERS["is_delivery_text"](text), text)
            self.assertFalse(self.partner(text), text)


class PartnerDeliveryFlow(unittest.TestCase):
    """Поток партнёрской доставки повторяет обычную заявку."""

    def test_is_delivery_excludes_partner_delivery(self):
        source = function_source(DELIVERY, "is_delivery")
        self.assertIn("if is_partner_delivery(order):", source)
        self.assertIn("return False", source)

    def test_partner_delivery_type_constant(self):
        self.assertEqual(MATCHERS["PARTNER_DELIVERY_TYPE"], "partner_delivery")

    def test_text_order_sets_partner_type(self):
        self.assertIn(
            "draft[\"order_type\"] = delivery_service.PARTNER_DELIVERY_TYPE",
            HANDLERS,
        )

    def test_created_order_keeps_partner_type(self):
        source = function_source(HANDLERS, "create_passenger_order")
        self.assertIn("normalized_type", source)
        self.assertIn("order_type=normalized_type,", source)

    def test_eta_prompt_asks_about_the_order_pickup(self):
        self.assertIn("PARTNER_DELIVERY_ETA_PROMPT", HANDLERS)
        self.assertIn("под\u044aедете за заказом", HANDLERS)
        source = function_source(HANDLERS, "_show_eta_menu")
        self.assertIn("delivery_service.is_partner_delivery(order)", source)

    def test_driver_is_never_asked_for_a_delivery_price(self):
        accept = function_source(HANDLERS, "driver_accept")
        # Цена запрашивается только через is_delivery(), который для
        # партнёрской доставки возвращает False.
        self.assertIn("delivery_service.is_delivery(order)", accept)
        self.assertNotIn("is_partner_delivery", accept.split("request_price")[0])

    def test_passenger_gets_driver_card_for_partner_delivery(self):
        source = function_source(HANDLERS, "_apply_eta")
        self.assertIn("is_partner_delivery", source)
        self.assertIn("Вашу доставку взял водитель", source)
        self.assertIn("_driver_card(user)", source)

    def test_arrived_stage_shows_picked_up_button(self):
        source = function_source(KEYBOARDS, "driver_ride_keyboard")
        self.assertIn("partner_delivery: bool = False", source)
        self.assertIn("Забрал заказ", source)
        self.assertIn("Пассажир сел", source)
        self.assertIn('{"cmd": "seated"}', source)

    def test_ride_keyboard_receives_the_flag(self):
        source = function_source(HANDLERS, "_driver_ride_kb")
        self.assertIn("partner_delivery=is_partner_delivery", source)
        self.assertIn("is_partner_delivery = delivery_service.is_partner_delivery(order)", source)

    def test_seated_message_mentions_the_parcel(self):
        source = function_source(HANDLERS, "driver_seated")
        self.assertIn("забрал заказ и везёт его клиенту", source)
        self.assertIn("delivery_service.is_partner_delivery(order)", source)

    def test_finish_uses_the_ordinary_price_prompt(self):
        source = function_source(HANDLERS, "driver_finish_prompt")
        # Партнёрская доставка не попадает в ветку is_delivery и завершается
        # как обычная поездка: водитель вводит сумму в конце.
        self.assertIn("Введите итоговую стоимость", source)

    def test_order_type_label(self):
        self.assertIn("Доставка от партн\u0451ров", ORDER_SERVICE)
        self.assertIn("def is_delivery_type(order: Order) -> bool:", ORDER_SERVICE)

    def test_partner_delivery_falls_back_to_the_drivers_chat(self):
        source = function_source(ORDER_SERVICE, "_handle_no_driver")
        self.assertIn("is_delivery_type(order)", source)

    def test_no_prearrival_notice_for_partner_delivery(self):
        self.assertIn("or delivery_service.is_partner_delivery(order)", ORDER_SERVICE)


if __name__ == "__main__":
    unittest.main()
