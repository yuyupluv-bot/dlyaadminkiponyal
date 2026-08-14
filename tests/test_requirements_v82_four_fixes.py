# -*- coding: utf-8 -*-
"""V82: rating correction, voice persistence, admin payment notice, away timeout."""
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


class PassengerRatingCorrection(unittest.TestCase):
    def test_comment_menu_has_change_rating(self):
        source = function_source(read("bot/keyboards.py"), "review_comment_keyboard")
        self.assertIn("Изменить оценку", source)
        self.assertIn('"review_rating_change"', source)

    def test_replacement_picker_has_five_choices(self):
        source = function_source(read("bot/keyboards.py"), "review_rating_keyboard")
        self.assertIn("range(5, 0, -1)", source)
        self.assertIn('"review_rating_set"', source)

    def test_less_than_five_uses_review_specific_menu(self):
        source = function_source(read("bot/handlers.py"), "save_rating")
        self.assertIn("if stars < 5", source)
        self.assertIn("review_comment_keyboard(review.id)", source)

    def test_change_updates_existing_review_and_aggregate(self):
        source = function_source(read("bot/handlers.py"), "passenger_review_rating_set")
        self.assertIn("review.stars = stars", source)
        self.assertIn("order.rating = stars", source)
        self.assertIn("+ stars - old_stars", source)
        self.assertNotIn("Review(", source)

    def test_new_five_star_rating_returns_to_main_menu(self):
        source = function_source(read("bot/handlers.py"), "passenger_review_rating_set")
        self.assertIn("States.MAIN_MENU", source)
        self.assertIn("Оценка изменена на 5 из 5", source)


class VoiceOrderPersistence(unittest.TestCase):
    def test_accepted_eta_card_repeats_voice(self):
        source = function_source(read("bot/handlers.py"), "_show_eta_menu")
        self.assertIn("prepare_voice_attachment", source)
        self.assertIn("attachment=voice_attachment", source)

    def test_offer_finalization_supplies_voice_fallback(self):
        source = function_source(read("bot/order_service.py"), "finalize_offer_message")
        self.assertIn("order.voice_attachment", source)
        self.assertIn("attachment=reusable_voice", source)

    def test_every_tracked_text_update_preserves_attachment(self):
        source = function_source(read("bot/outbox_service.py"), "update_tracked_message")
        self.assertIn("row.attachment", source)

    def test_finalize_can_restore_missing_attachment(self):
        source = function_source(read("bot/outbox_service.py"), "finalize_tracked_message")
        self.assertIn("attachment: str | None = None", source)
        self.assertIn("row.attachment = attachment", source)


class AdminFakeCallPayment(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / "web/app.py"
        if not self.path.exists():
            self.skipTest("web admin is not part of this archive")
        self.source = self.path.read_text(encoding="utf-8")

    def test_notice_is_enqueued_with_exact_text(self):
        helper = function_source(self.source, "_queue_fake_call_paid_notice")
        self.assertIn("OutboxMessage(", helper)
        self.assertIn("Спасибо за оплату ложного вызова.", helper)

    def test_passenger_is_returned_to_main_menu(self):
        helper = function_source(self.source, "_queue_fake_call_paid_notice")
        self.assertIn("States.MAIN_MENU", helper)
        self.assertIn("passenger_menu", helper)
        self.assertIn('state.data = "{}"', helper)

    def test_paid_route_is_idempotent_and_clears_reminder(self):
        route = function_source(self.source, "fake_call_mark_paid")
        self.assertIn('if fc.status == "paid"', route)
        self.assertIn('f"fakecall:{fc.id}"', route)
        self.assertIn("_queue_fake_call_paid_notice(s, passenger)", route)


class DriverAwayTimeout(unittest.TestCase):
    def test_timeout_is_exactly_thirty_minutes(self):
        source = read("bot/driver_away_timeout_service.py")
        self.assertIn("TIMEOUT_SECONDS = 30 * 60", source)

    def test_away_transition_schedules_and_return_cancels(self):
        source = read("bot/queue_service.py")
        set_away = function_source(source, "set_away")
        sync = function_source(source, "_sync_away_notice")
        self.assertIn("driver_away_timeout_service.schedule(driver.id)", set_away)
        self.assertIn("driver_away_timeout_service.cancel(driver.id)", sync)

    def test_timeout_removes_driver_and_returns_main_menu(self):
        source = function_source(read("bot/driver_away_timeout_service.py"), "expire")
        self.assertIn("driver.driver_status != \"away\"", source)
        self.assertIn("queue_service.leave_queue", source)
        self.assertIn("driver.is_on_line = False", source)
        self.assertIn("States.D_MENU", source)
        self.assertIn("kb.driver_menu", source)
        self.assertIn("более чем на 30 минут", read("bot/driver_away_timeout_service.py"))

    def test_timeout_is_restored_after_restart(self):
        source = function_source(read("bot/timers.py"), "_restored_callback")
        self.assertIn('kind == "driver_away"', source)
        self.assertIn("expire(object_id)", source)

    def test_existing_away_drivers_get_missing_timer_at_startup(self):
        service = function_source(
            read("bot/driver_away_timeout_service.py"),
            "schedule_missing_for_away_drivers",
        )
        self.assertIn('User.driver_status == "away"', service)
        self.assertIn("ScheduledJob.kind == TIMER_KIND", service)
        self.assertIn("schedule(driver_id)", service)
        self.assertIn(
            "driver_away_timeout_service.schedule_missing_for_away_drivers()",
            read("bot/main.py"),
        )


if __name__ == "__main__":
    unittest.main()
