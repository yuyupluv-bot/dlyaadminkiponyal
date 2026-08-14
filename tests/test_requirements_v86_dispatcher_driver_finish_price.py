from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot/handlers.py"


def source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name!r} not found")


class DispatcherDriverFinishPriceV86(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handlers = source(HANDLERS)
        cls.handle_message_impl = function_source(cls.handlers, "_handle_message_impl")
        cls.handle_driver = function_source(cls.handlers, "handle_driver")
        cls.handle_dispatcher = function_source(cls.handlers, "handle_dispatcher")

    def test_01_handlers_parse(self) -> None:
        ast.parse(self.handlers, filename=str(HANDLERS))

    def test_02_finish_price_is_explicitly_owned_by_driver_flow(self) -> None:
        module = ast.parse(self.handlers)
        owned = None
        for node in module.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_DRIVER_OWNED_INPUT_STATES"
                for target in node.targets
            ):
                owned = ast.get_source_segment(self.handlers, node) or ""
                break
        self.assertIsNotNone(owned)
        self.assertIn("States.D_FINISH_PRICE", owned)

    def test_03_global_router_sends_saved_driver_input_before_role_router(self) -> None:
        body = self.handle_message_impl
        driver_scope = "if user.has_role(ROLE_DRIVER):"
        state_guard = "if state in _DRIVER_OWNED_INPUT_STATES:"
        self.assertIn(driver_scope, body)
        self.assertIn(state_guard, body)
        self.assertIn("return handle_driver(session, user, state, text, payload, attachments)", body)
        self.assertLess(body.index(driver_scope), body.index(state_guard))
        self.assertLess(
            body.index(state_guard),
            body.index("if user.role == ROLE_ADMIN:"),
        )
        self.assertLess(
            body.index(state_guard),
            body.index("elif user.role == ROLE_DISPATCHER:"),
        )

    def test_04_finish_price_still_uses_driver_completion_handler(self) -> None:
        self.assertIn("if state == States.D_FINISH_PRICE", self.handle_driver)
        self.assertIn("return driver_complete_ride(session, user, text)", self.handle_driver)

    def test_05_dispatcher_handler_is_not_used_to_parse_driver_price(self) -> None:
        # This captures the former failure mode: dispatcher routing had no
        # D_FINISH_PRICE branch, so an amount was treated as unrelated text.
        self.assertNotIn("States.D_FINISH_PRICE", self.handle_dispatcher)


if __name__ == "__main__":
    unittest.main()
