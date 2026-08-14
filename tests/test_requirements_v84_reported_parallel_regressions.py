from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUEUE = ROOT / "bot/queue_service.py"
PARALLEL = ROOT / "bot/parallel_orders.py"
HANDLERS = ROOT / "bot/handlers.py"
KEYBOARDS = ROOT / "bot/keyboards.py"


def source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name!r} not found")


class ReportedParallelRegressionsV84(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.queue = source(QUEUE)
        cls.parallel = source(PARALLEL)
        cls.handlers = source(HANDLERS)
        cls.keyboards = source(KEYBOARDS)

    def test_01_changed_modules_parse(self) -> None:
        for path in (QUEUE, PARALLEL, HANDLERS, KEYBOARDS):
            ast.parse(source(path), filename=str(path))

    def test_02_exact_gornozavodsk_line_is_normalized(self) -> None:
        body = function_source(self.queue, "next_waiting_driver")
        tree = ast.parse(body)
        aliases = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "aliases"
                for target in node.targets
            ):
                aliases = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(aliases)
        self.assertEqual(aliases["горнозаводск"], "горнозаводск")
        self.assertEqual(aliases["горнозаводска"], "горнозаводск")
        self.assertIn('if line_scope == "exact"', body)

    def test_03_village_fallback_targets_exact_gorno_fifo(self) -> None:
        body = function_source(self.parallel, "_fallback_to_free_drivers")
        self.assertIn('session, "Горнозаводск", line_scope="exact"', body)
        self.assertIn('line_name="Горнозаводск"', body)
        self.assertIn("order.parallel_route_fallback = True", body)

    def test_04_promotion_becomes_an_ordinary_active_assignment(self) -> None:
        body = function_source(self.parallel, "promote_after_current")
        required = (
            ".with_for_update().first()",
            'order.status = "assigned"',
            "order.driver_id = driver.id",
            "order.parallel_driver_id = None",
            "order.arrival_eta = remaining",
            "order.parallel_eta = None",
            "order.parallel_eta_set_at = None",
            "States.D_IN_RIDE",
            "order_service.schedule_prearrival_notice(session, order)",
        )
        for item in required:
            self.assertIn(item, body)
        self.assertLess(body.index("order.driver_id = driver.id"), body.index("set_state("))

    def test_05_existing_promoted_rows_are_repaired_on_first_action(self) -> None:
        body = function_source(self.handlers, "active_order_for")
        self.assertIn("Order.parallel_driver_id == user.id", body)
        self.assertIn("Order.driver_id.is_(None)", body)
        self.assertIn('Order.status.in_(("assigned", "arrived", "in_progress"))', body)
        self.assertIn("order.driver_id = user.id", body)
        self.assertIn("order.parallel_driver_id = None", body)
        self.assertIn("order.arrival_eta = max(0, int(order.parallel_eta))", body)

    def test_06_finish_price_still_routes_to_completion_handler(self) -> None:
        driver_handler = function_source(self.handlers, "handle_driver")
        completion = function_source(self.handlers, "driver_complete_ride")
        self.assertIn("if state == States.D_FINISH_PRICE", driver_handler)
        self.assertIn("return driver_complete_ride(session, user, text)", driver_handler)
        self.assertLess(
            driver_handler.index("if state == States.D_FINISH_PRICE"),
            driver_handler.index("active = active_order_for(session, user, as_driver=True)"),
        )
        self.assertIn("order = active_order_for(session, user, as_driver=True)", completion)
        self.assertIn('order.status = "completed"', completion)

    def test_07_promoted_ride_uses_gender_aware_canonical_keyboard(self) -> None:
        promote = function_source(self.parallel, "promote_after_current")
        ride_keyboard = function_source(self.handlers, "_driver_ride_kb")
        keyboard_builder = function_source(self.keyboards, "driver_ride_keyboard")
        self.assertIn("keyboard=_driver_ride_kb(session, order)", promote)
        self.assertNotIn('kb.driver_ride_keyboard("assigned"', promote)
        self.assertIn("driver_gender=driver.driver_gender if driver else None", ride_keyboard)
        self.assertIn('"🚘 Подъехала" if driver_gender == "female" else "🚘 Подъехал"', keyboard_builder)

    def test_08_elapsed_parallel_eta_remains_a_valid_set_eta(self) -> None:
        ride_keyboard = function_source(self.handlers, "_driver_ride_kb")
        self.assertIn("eta_set=order.arrival_eta is not None", ride_keyboard)

    def test_09_parallel_eta_return_menu_is_also_gender_aware(self) -> None:
        save_eta = function_source(self.parallel, "save_eta")
        empty_list = function_source(self.parallel, "show")
        self.assertIn("keyboard=_driver_ride_kb(session, current)", save_eta)
        self.assertIn("keyboard=_driver_ride_kb(session, current)", empty_list)

    def test_10_legacy_promoted_order_is_repaired_dynamically(self) -> None:
        class Column:
            def __eq__(self, other):
                return ("eq", other)

            def in_(self, values):
                return ("in", values)

            def is_(self, value):
                return ("is", value)

            def desc(self):
                return self

        class OrderModel:
            status = Column()
            passenger_id = Column()
            driver_id = Column()
            parallel_driver_id = Column()
            created_at = Column()

        class Query:
            def __init__(self, result):
                self.result = result

            def filter(self, *args):
                return self

            def order_by(self, *args):
                return self

            def first(self):
                return self.result

        class Session:
            def __init__(self, legacy):
                self.legacy = legacy
                self.calls = 0

            def query(self, model):
                self.calls += 1
                return Query(None if self.calls == 1 else self.legacy)

        legacy = type("LegacyOrder", (), {
            "status": "assigned",
            "driver_id": None,
            "parallel_driver_id": 17,
            "arrival_eta": None,
            "parallel_eta": 14,
            "parallel_eta_set_at": object(),
        })()
        driver = type("Driver", (), {"id": 17})()
        namespace = {"Session": object, "User": object, "Order": OrderModel}
        exec(function_source(self.handlers, "active_order_for"), namespace)
        repaired = namespace["active_order_for"](Session(legacy), driver, as_driver=True)
        self.assertIs(repaired, legacy)
        self.assertEqual(repaired.driver_id, 17)
        self.assertIsNone(repaired.parallel_driver_id)
        self.assertEqual(repaired.arrival_eta, 14)
        self.assertIsNone(repaired.parallel_eta)
        self.assertIsNone(repaired.parallel_eta_set_at)

    def test_11_promotion_mutates_fields_and_uses_gender_keyboard_dynamically(self) -> None:
        import datetime as dt
        from types import SimpleNamespace

        class Column:
            def __eq__(self, other):
                return ("eq", other)

            def asc(self):
                return self

        class OrderModel:
            parallel_driver_id = Column()
            status = Column()
            created_at = Column()

        class Query:
            def __init__(self, result):
                self.result = result

            def filter(self, *args):
                return self

            def order_by(self, *args):
                return self

            def with_for_update(self):
                return self

            def first(self):
                return self.result

        class Session:
            def __init__(self, order):
                self.order = order

            def query(self, model):
                return Query(self.order)

            def get(self, model, object_id):
                return None

        fixed_now = dt.datetime(2026, 8, 13, 15, 0, tzinfo=dt.timezone.utc)
        order = SimpleNamespace(
            id=51,
            status="parallel_assigned",
            driver_id=9,
            parallel_driver_id=9,
            parallel_eta=12,
            parallel_eta_set_at=None,
            arrival_eta=None,
            driver_accept_time=None,
            driver_departed_at=None,
            route_text="Пашия — Горнозаводск",
            address_to="Горнозаводск",
            passenger_id=100,
            dispatcher_id=None,
        )
        driver = SimpleNamespace(id=9, vk_id=9009, driver_gender="female")
        scheduled = []
        states = []
        sent = []
        body = function_source(self.parallel, "promote_after_current")
        body = body.replace("    from . import order_service\n", "")
        body = body.replace("    from .handlers import _driver_ride_kb\n", "")
        namespace = {
            "Session": object,
            "User": object,
            "Order": OrderModel,
            "time_utils": SimpleNamespace(now=lambda: fixed_now),
            "States": SimpleNamespace(D_IN_RIDE="driver_in_ride"),
            "set_state": lambda *args: states.append(args),
            "order_service": SimpleNamespace(
                schedule_prearrival_notice=lambda *args: scheduled.append(args)
            ),
            "_driver_ride_kb": lambda session, active: (
                "female-arrived-keyboard" if driver.driver_gender == "female" else "male"
            ),
            "vk": SimpleNamespace(
                send_message=lambda *args, **kwargs: sent.append((args, kwargs))
            ),
        }
        exec(body, namespace)
        promoted = namespace["promote_after_current"](Session(order), driver)
        self.assertIs(promoted, order)
        self.assertEqual(order.status, "assigned")
        self.assertEqual(order.driver_id, driver.id)
        self.assertIsNone(order.parallel_driver_id)
        self.assertEqual(order.arrival_eta, 12)
        self.assertIsNone(order.parallel_eta)
        self.assertTrue(scheduled)
        self.assertTrue(states)
        self.assertEqual(sent[0][1]["keyboard"], "female-arrived-keyboard")


if __name__ == "__main__":
    unittest.main()
