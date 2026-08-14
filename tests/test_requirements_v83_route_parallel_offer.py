import ast
import __future__
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARALLEL_PATH = ROOT / "bot/parallel_orders.py"
PASSENGER_QUEUE_PATH = ROOT / "bot/passenger_queue.py"
ORDER_SERVICE_PATH = ROOT / "bot/order_service.py"
HANDLERS_PATH = ROOT / "bot/handlers.py"
KEYBOARDS_PATH = ROOT / "bot/keyboards.py"
TIMERS_PATH = ROOT / "bot/timers.py"
MAIN_PATH = ROOT / "bot/main.py"
OUTBOX_PATH = ROOT / "bot/outbox_service.py"
MODELS_PATH = ROOT / "common/models.py"
MIGRATION_PATH = ROOT / "migrations/versions/0042_route_parallel_offer_state.py"


def source(path: Path) -> str:
    return path.read_text("utf-8")


def function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(text, node) or ""


class RouteParallelOfferV83Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parallel = source(PARALLEL_PATH)
        cls.passenger_queue = source(PASSENGER_QUEUE_PATH)
        cls.order_service = source(ORDER_SERVICE_PATH)
        cls.handlers = source(HANDLERS_PATH)
        cls.keyboards = source(KEYBOARDS_PATH)
        cls.timers = source(TIMERS_PATH)
        cls.main = source(MAIN_PATH)
        cls.outbox = source(OUTBOX_PATH)
        cls.models = source(MODELS_PATH)
        cls.migration = source(MIGRATION_PATH)

    def test_01_changed_modules_parse(self):
        for path in (
            PARALLEL_PATH, PASSENGER_QUEUE_PATH, ORDER_SERVICE_PATH,
            HANDLERS_PATH, KEYBOARDS_PATH, TIMERS_PATH, MAIN_PATH, OUTBOX_PATH,
            MODELS_PATH, MIGRATION_PATH,
        ):
            ast.parse(source(path), filename=str(path))

    def test_02_own_village_fifo_precedes_busy_route_offer(self):
        dispatch = function_source(self.passenger_queue, "dispatch_new_order")
        own_line = dispatch.index('session, route_city, line_scope="exact"')
        busy_route = dispatch.index("parallel_orders.notify_busy_drivers(session, order)")
        fallback = dispatch.index("recheck_village=True")
        self.assertLess(own_line, busy_route)
        self.assertLess(busy_route, fallback)

    def test_03_full_card_and_exact_sixty_second_timer(self):
        card = function_source(self.parallel, "_route_offer_card_text")
        notify = function_source(self.parallel, "notify_busy_drivers")
        keyboard = function_source(self.keyboards, "route_parallel_offer_keyboard")
        self.assertIn("Ваша заявка", card)
        self.assertIn("order_service.order_text(order)", card)
        self.assertIn("Комментарий", card)
        self.assertIn("На ответ даётся 60 секунд", card)
        self.assertIn("ROUTE_OFFER_SECONDS = 60", self.parallel)
        self.assertIn('"route_parallel_offer", order.id, ROUTE_OFFER_SECONDS', notify)
        self.assertIn("Взять параллельную заявку", keyboard)
        self.assertIn("Отказаться", keyboard)
        self.assertIn('"cmd": "route_parallel_take"', keyboard)

    def test_04_auto_accept_is_locked_and_enters_existing_eta_flow(self):
        take = function_source(self.parallel, "take")
        route_take = function_source(self.parallel, "take_route_offer")
        self.assertIn("with_for_update()", take)
        self.assertLess(take.index("User.id == driver.id"), take.index("Order.id == int(order_id or 0)"))
        self.assertIn("order.parallel_offer_driver_id != driver.id", take)
        self.assertIn("order.parallel_offer_trip_id != current.id", take)
        self.assertIn('order.status = "parallel_assigned"', take)
        self.assertIn('timers.schedule("parallel_eta", order.id, 120', take)
        self.assertIn("require_live_offer=True", route_take)

    def test_05_explicit_decline_falls_back_but_does_not_disable_trip(self):
        decline = function_source(self.parallel, "decline_route_offer")
        self.assertIn("_exclude_from_auto_offer(order, driver.id)", decline)
        self.assertIn("offer_already_closed=True", decline)
        self.assertNotIn("parallel_auto_offers_disabled = True", decline)
        fallback = function_source(self.parallel, "_fallback_to_free_drivers")
        self.assertIn('session, "Горнозаводск", line_scope="exact"', fallback)
        self.assertIn('line_name="Горнозаводск"', fallback)

    def test_06_timeout_is_idempotent_closes_card_and_disables_exact_trip(self):
        timeout = function_source(self.parallel, "_route_offer_timeout")
        self.assertIn("order.parallel_offer_driver_id != driver_id", timeout)
        self.assertIn("order.parallel_offer_trip_id != trip_id", timeout)
        self.assertIn("current.parallel_auto_offers_disabled = True", timeout)
        self.assertIn("ROUTE_OFFER_TIMEOUT_TEXT", timeout)
        self.assertIn("replace_text=True", timeout)
        self.assertIn("offer_already_closed=True", timeout)
        self.assertIn(
            "Вам была предложена параллельная заявка, но вы не ответили в течение минуты",
            self.parallel,
        )

    def test_07_next_order_after_timeout_does_not_start_another_wait(self):
        eligible = function_source(self.parallel, "_auto_offer_trip_is_eligible")
        dispatch = function_source(self.passenger_queue, "dispatch_new_order")
        fallback = function_source(self.parallel, "_fallback_to_free_drivers")
        self.assertIn("current.parallel_auto_offers_disabled", eligible)
        self.assertIn("if parallel_orders.notify_busy_drivers(session, order):", dispatch)
        self.assertIn("recheck_village=True", dispatch)
        self.assertNotIn('timers.schedule("route_parallel_offer"', fallback)

    def test_08_missed_and_later_orders_remain_in_manual_list(self):
        available = function_source(self.parallel, "available")
        show = function_source(self.parallel, "show")
        self.assertIn("PARALLEL_CANDIDATE_STATUSES", available)
        self.assertIn("Order.parallel_driver_id.is_(None)", available)
        self.assertNotIn("parallel_offer_driver_id.is_(None)", available)
        self.assertNotIn("parallel_auto_offers_disabled", available)
        self.assertIn("_destination_restricted_orders(current, available(session))", show)

    def test_09_manual_take_remains_available_but_old_auto_button_does_not(self):
        take = function_source(self.parallel, "take")
        route_take = function_source(self.parallel, "take_route_offer")
        handler = function_source(self.handlers, "handle_driver")
        self.assertIn("require_live_offer: bool = False", take)
        self.assertIn("if require_live_offer and", take)
        self.assertIn("require_live_offer=True", route_take)
        self.assertIn('if cmd == "parallel_take"', handler)
        self.assertIn('if cmd == "route_parallel_take"', handler)

    def test_10_cancelled_or_taken_order_cannot_be_claimed_manually(self):
        candidate = function_source(self.parallel, "_parallel_candidate_filter")
        take = function_source(self.parallel, "take")
        self.assertIn("PARALLEL_CANDIDATE_STATUSES", candidate)
        self.assertIn("Order.driver_id.is_(None)", candidate)
        self.assertIn("Order.parallel_driver_id.is_(None)", candidate)
        self.assertIn("with_for_update().one_or_none()", take)
        self.assertIn("уже отменена или её взял другой водитель", take)

    def test_11_restriction_is_trip_scoped_and_next_trip_starts_enabled(self):
        self.assertIn("parallel_auto_offers_disabled = Column(Boolean, default=False", self.models)
        self.assertNotIn("parallel_auto_offers_disabled = Column", self.models.split("class User", 1)[1].split("class AdminUser", 1)[0])
        eligible = function_source(self.parallel, "_auto_offer_trip_is_eligible")
        self.assertIn("current.parallel_auto_offers_disabled", eligible)
        self.assertIn("release_route_offers_for_trip(session, order)", self.handlers)

    def test_12_restart_restores_saved_offer_timer_and_trip_state(self):
        restored = function_source(self.timers, "_restored_callback")
        self.assertIn('elif kind == "route_parallel_offer"', restored)
        self.assertIn("_route_offer_timeout(object_id)", restored)
        self.assertIn("parallel_offer_driver_id", self.migration)
        self.assertIn("parallel_offer_trip_id", self.migration)
        self.assertIn("parallel_auto_offers_disabled", self.migration)
        reconcile = function_source(self.parallel, "reconcile_route_offers")
        startup = function_source(self.main, "run")
        self.assertIn("ScheduledJob.job_key", reconcile)
        self.assertIn("_route_offer_timeout(order_id)", reconcile)
        self.assertLess(
            startup.index("timers.restore_persistent()"),
            startup.index("parallel_orders.reconcile_route_offers()"),
        )

    def test_13_pashiya_and_kusya_are_symmetric_and_never_mixed(self):
        wanted = {
            "_city_first_two", "_destination_city", "_origin_city",
            "route_priority_city", "_has_return_intent",
            "_destination_restricted_orders",
        }
        tree = ast.parse(self.parallel)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        namespace = {"re": re, "Order": object}
        module = ast.Module(body=nodes, type_ignores=[])
        exec(
            compile(
                module,
                "route_helpers",
                "exec",
                flags=__future__.annotations.compiler_flag,
                dont_inherit=True,
            ),
            namespace,
        )

        class Row:
            def __init__(self, route):
                self.route_text = route
                self.address_from = route
                self.address_to = route

        restrict = namespace["_destination_restricted_orders"]
        pashiya_trip = Row("Горнозаводск, Ленина 1 до Пашии")
        kusya_trip = Row("Горнозаводск, Ленина 1 до Кусьи")
        pashiya = Row("Пашия, Советская 2 — Горнозаводск")
        kusya = Row("Кусья, Центральная 3 — Горнозаводск")
        self.assertEqual([pashiya], restrict(pashiya_trip, [pashiya, kusya]))
        self.assertEqual([kusya], restrict(kusya_trip, [pashiya, kusya]))

    def test_14_double_accept_is_serialized_for_busy_and_free_drivers(self):
        take = function_source(self.parallel, "take")
        ordinary = function_source(self.order_service, "offer_to_next_driver")
        claim_trip = function_source(self.parallel, "_claim_auto_offer_trip")
        self.assertIn("session.query(User).filter(User.id == driver.id).with_for_update()", take)
        self.assertIn("with_for_update().one_or_none()", take)
        self.assertIn("locked_order", ordinary)
        self.assertIn("with_for_update()", ordinary)
        self.assertIn("with_for_update(skip_locked=True)", claim_trip)

    def test_15_send_or_edit_error_cannot_roll_back_saved_business_decision(self):
        notify = function_source(self.parallel, "notify_busy_drivers")
        finalize = function_source(self.parallel, "_finalize_route_offer_card")
        timeout = function_source(self.parallel, "_route_offer_timeout")
        self.assertIn("if not outbox_id:\n        return False", notify)
        self.assertIn("outbox_service.finalize_tracked_message", finalize)
        # The return value is intentionally ignored; state is cleared and the
        # durable queue transition continues even when VK edit fails.
        self.assertNotIn("if not outbox_service.finalize_tracked_message", finalize)
        self.assertIn("_fallback_to_free_drivers", timeout)

    def test_16_multiple_simultaneous_orders_use_one_live_offer_per_driver(self):
        eligibility = function_source(self.parallel, "_auto_offer_trip_is_eligible")
        claim = function_source(self.parallel, "_claim_auto_offer_trip")
        notify = function_source(self.parallel, "notify_busy_drivers")
        self.assertIn("Order.parallel_offer_driver_id == driver_id", eligibility)
        self.assertIn("with_for_update(skip_locked=True)", claim)
        self.assertIn("order.parallel_offer_driver_id = driver.id", notify)
        self.assertIn("order.parallel_offer_trip_id = current.id", notify)

    def test_17_gorno_fallback_is_durable_even_if_decline_reason_changes(self):
        helper = function_source(self.parallel, "is_route_fallback")
        fallback = function_source(self.parallel, "_fallback_to_free_drivers")
        self.assertIn("order.parallel_route_fallback", helper)
        self.assertIn("order.parallel_route_fallback = True", fallback)
        self.assertIn("parallel_route_fallback", self.migration)
        self.assertIn("last_decline_reason='route_parallel_fallback'", self.migration)

    def test_18_outbox_stale_card_detection_knows_auto_take_command(self):
        self.assertIn('"route_parallel_take"', self.outbox)
        cancel = function_source(self.parallel, "cancel_route_offer")
        remove = function_source(self.passenger_queue, "remove")
        self.assertIn("replace_text=True", cancel)
        self.assertIn("parallel_orders.cancel_route_offer", remove)


if __name__ == "__main__":
    unittest.main()
