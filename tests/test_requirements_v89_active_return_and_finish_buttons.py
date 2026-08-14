from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot/handlers.py"
KEYBOARDS = ROOT / "bot/keyboards.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {name!r} not found")


class ActiveReturnAndFinishButtonsV89(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handlers = read(HANDLERS)
        cls.keyboards = read(KEYBOARDS)

    def test_01_changed_modules_parse(self) -> None:
        ast.parse(self.handlers, filename=str(HANDLERS))
        ast.parse(self.keyboards, filename=str(KEYBOARDS))

    def test_02_payloadless_vk_back_label_is_recovered(self) -> None:
        entry = function_source(self.handlers, "_handle_message_impl")
        self.assertIn('"вернуться к активной заявке"', entry)
        self.assertIn('"вернуться к заявке"', entry)
        self.assertIn('payload = {"cmd": "parallel_back"}', entry)
        self.assertIn('cmd = "parallel_back"', entry)

    def test_03_parallel_back_uses_one_robust_return_handler(self) -> None:
        driver = function_source(self.handlers, "handle_driver")
        self.assertIn('if cmd == "parallel_back":', driver)
        self.assertIn("return driver_return_to_active(session, user)", driver)

    def test_04_return_restores_state_and_full_ride_keyboard(self) -> None:
        back = function_source(self.handlers, "driver_return_to_active")
        self.assertIn("active_order_for(session, user, as_driver=True)", back)
        self.assertIn("States.D_IN_RIDE", back)
        self.assertIn("States.D_FINISH_PRICE", back)
        self.assertIn("_active_driver_order_keyboard(session, order)", back)
        self.assertIn("Введите итоговую стоимость поездки", back)

    def test_05_finish_price_consumes_only_plain_text_not_buttons(self) -> None:
        driver = function_source(self.handlers, "handle_driver")
        price_guard = "if state == States.D_FINISH_PRICE and not cmd:"
        self.assertIn(price_guard, driver)
        self.assertIn("return driver_complete_ride(session, user, text)", driver)
        self.assertLess(driver.index(price_guard), driver.index('if cmd == "price":'))
        self.assertLess(driver.index(price_guard), driver.index('if cmd == "driver_cancel_active":'))
        self.assertLess(driver.index(price_guard), driver.index('if cmd == "parallel_orders":'))

    def test_06_finish_prompt_keeps_active_buttons_visible(self) -> None:
        finish = function_source(self.handlers, "driver_finish_prompt")
        self.assertIn("keyboard=_active_driver_order_keyboard(session, order)", finish)
        ride = function_source(self.keyboards, "driver_ride_keyboard")
        self.assertIn('{"cmd": "price"}', ride)
        self.assertIn('{"cmd":"driver_cancel_active"}', ride)
        self.assertIn('{"cmd": "parallel_orders"}', ride)

    def test_07_price_and_cancel_back_preserve_finish_prompt(self) -> None:
        price_back = function_source(self.handlers, "return_from_price")
        cancel_back = function_source(self.handlers, "driver_cancel_back")
        self.assertIn("driver_return_to_active(session, user)", price_back)
        self.assertIn("driver_return_to_active(session, user)", cancel_back)


if __name__ == "__main__":
    unittest.main()
