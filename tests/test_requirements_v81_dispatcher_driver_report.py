# -*- coding: utf-8 -*-
"""V81: manual dispatcher message for every user with the driver role."""
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
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class DispatcherDriverReportMenu(unittest.TestCase):
    def test_menu_has_manual_report_button(self):
        menu = function_source(read("bot/keyboards.py"), "dispatcher_menu")
        self.assertEqual(menu.count('"disp_driver_report"'), 1)
        self.assertIn("Отправить отчет водителям", menu)

    def test_flow_has_one_input_state(self):
        states = read("bot/states_service.py")
        self.assertIn("DISP_REPORT_INPUT", states)

    def test_router_intercepts_the_flow(self):
        router = function_source(read("bot/handlers.py"), "handle_dispatcher")
        self.assertIn("_DISPATCHER_REPORT_STATES", router)
        self.assertIn(
            "_handle_dispatcher_report(session, user, state, text, payload, attachments)",
            router,
        )

    def test_prompt_requests_photo_text_and_requisites(self):
        start = function_source(read("bot/handlers.py"), "disp_report_start")
        self.assertIn("фотографию", start)
        self.assertIn("текст отчета", start)
        self.assertIn("реквизитов", start)
        self.assertIn("одним сообщением", start)


class DispatcherDriverReportValidation(unittest.TestCase):
    def setUp(self):
        self.submit = function_source(read("bot/handlers.py"), "disp_report_submit")

    def test_only_dispatchers_can_submit(self):
        self.assertIn("has_role(ROLE_DISPATCHER)", self.submit)

    def test_text_is_required(self):
        self.assertIn("Текст не найден", self.submit)

    def test_only_photo_attachments_are_accepted(self):
        self.assertIn('attachment.get("type") == "photo"', self.submit)
        self.assertIn("Фотография не найдена", self.submit)

    def test_photo_is_reuploaded_before_forwarding(self):
        self.assertIn("reupload_attachments", self.submit)
        self.assertIn("photos[:1]", self.submit)

    def test_send_starts_immediately_after_valid_input(self):
        self.assertIn("broadcast_service.start", self.submit)
        self.assertIn("text, attachment", self.submit)

    def test_cancel_returns_to_dispatcher_menu(self):
        cancel = function_source(read("bot/handlers.py"), "disp_report_cancel")
        self.assertIn("States.DISP_MENU", cancel)
        self.assertIn("dispatcher_menu", cancel)


class DriverOnlyAudience(unittest.TestCase):
    def test_manual_report_uses_driver_target(self):
        self.assertIn(
            'broadcast_service.start(user.vk_id, text, attachment, "driver")',
            function_source(read("bot/handlers.py"), "disp_report_submit"),
        )

    def test_driver_target_uses_all_granted_roles(self):
        broadcast = read("bot/broadcast_service.py")
        self.assertIn('if target == "driver"', broadcast)
        self.assertIn('u.has_role("driver")', broadcast)


if __name__ == "__main__":
    unittest.main()
