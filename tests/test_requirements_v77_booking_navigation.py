# -*- coding: utf-8 -*-
"""V77: dispatchers never land on the intermediate booking-section screen."""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CANCEL_BOOKING = "\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0431\u0440\u043e\u043d\u044c"
BACK_TO_MENU = "\u0412\u0435\u0440\u043d\u0443\u0442\u044c\u0441\u044f \u0432 \u0433\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e"
NO_BOOKINGS = "\u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0431\u0440\u043e\u043d\u0435\u0439."
SECTION = "\u0420\u0430\u0437\u0434\u0435\u043b \u0431\u0440\u043e\u043d\u0438:"


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class BookingNavigation(unittest.TestCase):
    def test_my_bookings_list_has_cancel_buttons_and_main_menu(self):
        source = function_source(read("bot/keyboards.py"), "dispatcher_bookings_keyboard")
        self.assertIn('"cmd": "disp_booking_cancel"', source)
        self.assertIn(CANCEL_BOOKING, source)
        self.assertIn(BACK_TO_MENU, source)
        self.assertIn('"cmd": "start"', source)
        self.assertNotIn("disp_booking_menu", source)

    def test_empty_list_returns_the_main_dispatcher_menu(self):
        source = function_source(read("bot/handlers.py"), "disp_show_bookings")
        self.assertIn(NO_BOOKINGS, source)
        self.assertIn("kb.dispatcher_menu(can_switch_role(user))", source)
        self.assertNotIn("dispatcher_booking_menu", source)

    def test_do_not_book_returns_to_the_main_menu(self):
        source = function_source(read("bot/handlers.py"), "handle_dispatcher")
        _, marker, tail = source.partition('if cmd == "booking_back":')
        self.assertTrue(marker, "booking_back branch is missing")
        branch = tail.split("if cmd ==", 1)[0]
        self.assertIn("show_main_menu(session, user)", branch)
        self.assertNotIn("dispatcher_booking_menu", branch)

    def test_booking_section_command_opens_the_main_menu(self):
        source = function_source(read("bot/handlers.py"), "handle_dispatcher")
        _, marker, tail = source.partition('if cmd == "disp_booking_menu":')
        self.assertTrue(marker, "disp_booking_menu branch is missing")
        branch = tail.split("if cmd ==", 1)[0]
        self.assertIn("show_main_menu(session, user)", branch)
        self.assertNotIn("dispatcher_booking_menu", branch)

    def test_section_screen_is_never_sent_anymore(self):
        handlers = read("bot/handlers.py")
        self.assertNotIn("kb.dispatcher_booking_menu()", handlers)
        self.assertNotIn(SECTION, handlers)

    def test_saved_booking_returns_to_the_main_menu(self):
        source = function_source(read("bot/handlers.py"), "passenger_booking_confirm")
        self.assertIn("show_main_menu(session, user)", source)


if __name__ == "__main__":
    unittest.main()
