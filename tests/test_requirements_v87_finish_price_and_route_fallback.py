from __future__ import annotations

import ast
import pathlib
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot/handlers.py"
ORDER_SERVICE = ROOT / "bot/order_service.py"
PARALLEL = ROOT / "bot/parallel_orders.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def function_source(source: str, name: str) -> str:
    return ast.get_source_segment(source, function_node(source, name)) or ""


def load_function(source: str, name: str, namespace: dict) -> object:
    node = function_node(source, name)
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            node,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), name, "exec"), namespace)
    return namespace[name]


class FinishPriceAndRouteFallbackV87(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handlers = read(HANDLERS)
        cls.order_service = read(ORDER_SERVICE)
        cls.parallel = read(PARALLEL)

    def test_01_changed_modules_parse(self) -> None:
        for path in (HANDLERS, ORDER_SERVICE, PARALLEL):
            ast.parse(read(path), filename=str(path))

    def test_02_price_state_beats_global_offer_lock(self) -> None:
        body = function_source(self.handlers, "_handle_message_impl")
        self.assertLess(
            body.index("state = get_state(session, vk_id).state"),
            body.index("if user.role == ROLE_DRIVER and pending_offer:"),
        )
        self.assertLess(
            body.index("if state in _DRIVER_OWNED_INPUT_STATES:"),
            body.index("if user.role == ROLE_DRIVER and pending_offer:"),
        )
        self.assertIn("return handle_driver(session, user, state, text, payload, attachments)", body)

    def test_03_price_is_completed_even_when_a_stale_offer_exists(self) -> None:
        calls: list[tuple] = []

        class States:
            D_FINISH_PRICE = "driver_finish_price"

        def release(session, user, pending, current):
            calls.append(("released", pending.id, current.id))
            return True

        def complete(session, user, text):
            calls.append(("completed", user.vk_id, text))
            return "done"

        handle_driver = load_function(
            self.handlers,
            "handle_driver",
            {
                "States": States,
                "offered_order_for": lambda session, user: SimpleNamespace(id=20),
                "active_order_for": lambda session, user, as_driver=False: SimpleNamespace(id=10),
                "_release_stale_offer_for_active_ride": release,
                "driver_complete_ride": complete,
            },
        )
        result = handle_driver(
            None,
            SimpleNamespace(vk_id=77),
            States.D_FINISH_PRICE,
            "350",
            {},
            [],
        )
        self.assertEqual("done", result)
        self.assertEqual(
            [("released", 20, 10), ("completed", 77, "350")],
            calls,
        )

    def test_04_stale_offer_is_reoffered_without_overwriting_driver_state(self) -> None:
        body = function_source(self.handlers, "_release_stale_offer_for_active_ride")
        self.assertIn('timers.cancel("accept", offer.id)', body)
        self.assertIn("order_service.finalize_offer_message", body)
        self.assertIn("offer.offered_driver_id = None", body)
        self.assertIn('offer.status = "created"', body)
        self.assertIn("queue_service.mark_assigned(session, driver)", body)
        self.assertIn("temporary_exclude_driver_ids={driver.id}", body)
        self.assertNotIn("set_state(session, driver.vk_id, States.D_OFFER", body)

    def test_05_route_fallback_is_not_saved_as_a_driver_refusal(self) -> None:
        fallback = function_source(self.parallel, "_fallback_to_free_drivers")
        offer = function_source(self.order_service, "offer_to_next_driver")
        self.assertIn("order.parallel_route_fallback = True", fallback)
        self.assertNotIn(
            "order.last_decline_reason = ROUTE_FALLBACK_REASON",
            fallback,
        )
        self.assertIn(
            "if order.last_decline_reason == ROUTE_FALLBACK_REASON:",
            fallback,
        )
        self.assertIn("order.last_decline_reason = None", fallback)
        self.assertIn(
            "previous_reason != parallel_orders.ROUTE_FALLBACK_REASON",
            offer,
        )

    def test_06_fresh_dispatcher_order_explicitly_starts_without_route_history(self) -> None:
        creator = function_source(self.handlers, "disp_create_order_from_draft")
        self.assertIn("last_decline_reason=None", creator)
        self.assertIn("parallel_route_fallback=False", creator)


if __name__ == "__main__":
    unittest.main()
