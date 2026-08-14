# -*- coding: utf-8 -*-
"""V72: autonomous operation fixes.

No new restrictions for drivers or admins: those checks are explicitly
asserted to be absent.
"""
import ast
import datetime as dt
import os
import io
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"

HANDLERS = (BOT / "handlers.py").read_text(encoding="utf-8")
KEYBOARDS_PATH = BOT / "keyboards.py"
KEYBOARDS = KEYBOARDS_PATH.read_text(encoding="utf-8")
PARALLEL = (BOT / "parallel_orders.py").read_text(encoding="utf-8")
MAINTENANCE = (BOT / "maintenance_service.py").read_text(encoding="utf-8")
AWAY = (BOT / "away_order_notice_service.py").read_text(encoding="utf-8")


def load_module_without(path, drop_prefixes, injected):
    """Execute a pure module, skipping imports that need heavy dependencies."""
    source = path.read_text(encoding="utf-8")
    kept = [
        line for line in source.splitlines()
        if not any(line.startswith(prefix) for prefix in drop_prefixes)
    ]
    namespace = dict(injected)
    exec(compile("\n".join(kept), str(path), "exec"), namespace)
    return namespace


def extract_function(path, name, namespace):
    """Compile a single top-level function so it can be called in isolation."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, str(path), "exec"), namespace)
            return namespace[name]
    raise AssertionError("Function %s not found in %s" % (name, path))


def extract_value(path, name):
    """Evaluate a single top-level constant assignment from a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in targets:
                namespace = {}
                module = ast.Module(body=[node], type_ignores=[])
                exec(compile(module, str(path), "exec"), namespace)
                return namespace[name]
    raise AssertionError("Value %s not found in %s" % (name, path))


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("Function %s not found" % name)


KB = load_module_without(
    KEYBOARDS_PATH,
    drop_prefixes=("from .roles",),
    injected={"ROLE_TITLES": {}},
)


class HandlerErrorSafetyNet(unittest.TestCase):
    """A crashing handler must never answer a person with silence."""

    def test_error_notice_text_exists(self):
        self.assertIn("HANDLER_ERROR_NOTICE = (", HANDLERS)

    def test_notice_is_in_russian_and_suggests_a_retry(self):
        self.assertIn("\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437", HANDLERS)

    def test_public_entry_point_is_a_wrapper(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertIn("_handle_message_impl(session, event)", wrapper)

    def test_wrapper_catches_every_exception(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertIn("except Exception as exc", wrapper)

    def test_wrapper_logs_the_traceback(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertIn("log.exception", wrapper)

    def test_wrapper_reraises_so_the_transaction_rolls_back(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertTrue(wrapper.rstrip().endswith("raise"))

    def test_notice_is_not_sent_into_group_conversations(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertIn("peer_id == vk_id", wrapper)

    def test_notification_failure_cannot_mask_the_original_error(self):
        wrapper = function_source(HANDLERS, "handle_message")
        self.assertEqual(wrapper.count("except Exception"), 2)

    def test_original_body_is_preserved_in_the_impl(self):
        impl = function_source(HANDLERS, "_handle_message_impl")
        self.assertIn("blocked_ids = _cached_blocked_vk_ids(session)", impl)
        self.assertIn("is_conversation = peer_id != vk_id", impl)

    def test_entry_point_is_defined_exactly_once(self):
        self.assertEqual(HANDLERS.count("\ndef handle_message("), 1)
        self.assertEqual(HANDLERS.count("\ndef _handle_message_impl("), 1)


class NoNewRestrictionsForDriversAndAdmins(unittest.TestCase):
    """Explicit guard: verified drivers and admins stay unrestricted."""

    def test_chat_claim_has_no_block_check(self):
        claim = function_source(HANDLERS, "driver_take_from_chat")
        self.assertNotIn("is_blocked", claim)

    def test_booking_claim_has_no_block_check(self):
        booking = function_source(HANDLERS, "driver_take_booking")
        self.assertNotIn("driver_block_service", booking)

    def test_no_antiflood_was_added_for_drivers(self):
        self.assertNotIn("DRIVER_ANTIFLOOD", HANDLERS)
        self.assertNotIn("driver_rate_limit", HANDLERS)

    def test_chat_claim_still_locks_the_order_row(self):
        claim = function_source(HANDLERS, "driver_take_from_chat")
        self.assertIn("with_for_update", claim)


class TextOrderNoise(unittest.TestCase):
    """Courtesy chatter must open the menu, not create a ride request."""

    def setUp(self):
        self.is_noise = extract_function(
            BOT / "handlers.py",
            "_is_not_order_text",
            {
                "Session": object,
                "TEXT_ORDER_STOP_WORDS": frozenset({
                    "\u043e\u043a", "\u0441\u043f\u0441", "\u043f\u0440\u0438\u0432\u0435\u0442", "\u0434\u0430", "\u0442\u0435\u0441\u0442", "\u0430\u043b\u043b\u043e",
                }),
                "TEXT_ORDER_MIN_LENGTH": 5,
                "_menu_text_commands": lambda session: {"\u043c\u0435\u043d\u044e", "\u043d\u0430\u0437\u0430\u0434"},
            },
        )

    def test_minimum_length_raised_to_five(self):
        self.assertIn("TEXT_ORDER_MIN_LENGTH = 5", HANDLERS)

    def test_old_three_character_rule_is_gone(self):
        self.assertNotIn("TEXT_ORDER_MIN_LENGTH = 3", HANDLERS)

    def test_stop_word_is_not_an_order(self):
        for word in ("\u043e\u043a", "\u0441\u043f\u0441", "\u043f\u0440\u0438\u0432\u0435\u0442", "\u0434\u0430", "\u0442\u0435\u0441\u0442"):
            self.assertTrue(self.is_noise(None, word), word)

    def test_stop_word_with_punctuation_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, "\u041e\u041a!!!"))
        self.assertTrue(self.is_noise(None, "\u0421\u043f\u0441."))

    def test_stop_word_is_case_insensitive(self):
        self.assertTrue(self.is_noise(None, "\u041f\u0440\u0438\u0432\u0435\u0442"))

    def test_menu_command_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, "\u041c\u0435\u043d\u044e"))

    def test_emoji_only_message_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, "\U0001f44d\U0001f44d"))

    def test_punctuation_only_message_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, "???"))

    def test_empty_message_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, ""))
        self.assertTrue(self.is_noise(None, "   "))

    def test_short_message_is_not_an_order(self):
        self.assertTrue(self.is_noise(None, "\u0430\u0431\u0432"))

    def test_real_address_is_an_order(self):
        self.assertFalse(self.is_noise(None, "\u041b\u0435\u043d\u0438\u043d\u0430 12 \u043d\u0430 \u0421\u0432\u0435\u0440\u0434\u043b\u043e\u0432\u0430 3"))

    def test_short_but_real_address_is_an_order(self):
        self.assertFalse(self.is_noise(None, "\u041b\u0435\u043d\u0438\u043d\u0430 5"))

    def test_house_number_request_is_an_order(self):
        self.assertFalse(self.is_noise(None, "\u041a\u0443\u0441\u044c\u044f 14"))

    def test_passenger_text_order_uses_the_filter(self):
        creator = function_source(HANDLERS, "passenger_text_order")
        self.assertIn("_is_not_order_text(session, raw)", creator)


class TextOrderStopWordsV73(unittest.TestCase):
    """Real stop-word list from handlers.py, checked on real phrases."""

    def setUp(self):
        self.stop_words = extract_value(BOT / "handlers.py", "TEXT_ORDER_STOP_WORDS")
        self.is_noise = extract_function(
            BOT / "handlers.py",
            "_is_not_order_text",
            {
                "Session": object,
                "TEXT_ORDER_STOP_WORDS": self.stop_words,
                "TEXT_ORDER_MIN_LENGTH": extract_value(
                    BOT / "handlers.py", "TEXT_ORDER_MIN_LENGTH"
                ),
                "_menu_text_commands": lambda session: {"меню", "назад", "старт"},
            },
        )

    # --- главное: вежливое начало НЕ ломает заявку ---

    def test_greeting_plus_route_is_an_order(self):
        self.assertFalse(
            self.is_noise(None, "Здравствуйте, можно от фасоли до кусьи.")
        )

    def test_greeting_plus_address_is_an_order(self):
        self.assertFalse(self.is_noise(None, "Привет, нужна машина на Мира 7"))

    def test_stop_word_inside_longer_text_is_an_order(self):
        for text in (
            "есть машинки до кусьи?",
            "дорого до Пашии, но надо",
            "Сколько стоит до Пашии?",
            "есть свободные до Горнозаводска",
        ):
            self.assertFalse(self.is_noise(None, text), text)

    def test_route_without_greeting_is_an_order(self):
        self.assertFalse(self.is_noise(None, "от фасоли до кусьи"))

    def test_single_landmark_is_an_order(self):
        self.assertFalse(self.is_noise(None, "Фасоль"))

    # --- новые стоп-слова ---

    def test_price_complaint_is_not_an_order(self):
        for text in ("дорого", "Дорого!", "дорого.", "Это дорого", "очень дорого"):
            self.assertTrue(self.is_noise(None, text), text)

    def test_car_availability_question_is_not_an_order(self):
        for text in (
            "есть машинки?",
            "Есть машинки",
            "есть машины?",
            "машинки есть",
            "машинки?",
        ):
            self.assertTrue(self.is_noise(None, text), text)

    def test_free_driver_question_is_not_an_order(self):
        for text in (
            "есть свободные?",
            "ЕСТЬ СВОБОДНЫЕ",
            "есть свободные машины",
            "есть водители?",
            "кто свободен?",
        ):
            self.assertTrue(self.is_noise(None, text), text)

    def test_bare_price_question_is_not_an_order(self):
        for text in ("сколько стоит?", "почём", "цена", "тарифы"):
            self.assertTrue(self.is_noise(None, text), text)

    def test_requested_words_are_present_in_the_list(self):
        for word in ("дорого", "есть машинки", "есть свободные"):
            self.assertIn(word, self.stop_words)

    def test_stop_list_has_no_punctuation_entries(self):
        # записи со знаками никогда не совпадут: текст нормализуется
        for word in self.stop_words:
            self.assertEqual(word, word.strip("!?.,;:"), word)

    def test_stop_list_is_lowercase(self):
        for word in self.stop_words:
            self.assertEqual(word, word.casefold(), word)

    def test_stop_list_has_no_duplicates_after_normalization(self):
        normalized = [w.strip().casefold() for w in self.stop_words]
        self.assertEqual(len(normalized), len(set(normalized)))

    def test_stop_list_entries_are_not_too_generic(self):
        # однобуквенные записи допустимы только как междометия
        allowed_single = {"а"}
        for word in self.stop_words:
            if len(word) == 1:
                self.assertIn(word, allowed_single, word)

    def test_no_street_name_became_a_stop_word(self):
        for street in ("ленина", "свердлова", "мира", "кусья", "пашия", "фасоль"):
            self.assertNotIn(street, self.stop_words)


class KeyboardLabelLimit(unittest.TestCase):
    """VK error 911: a label longer than 40 characters kills the message."""

    def test_limit_constant(self):
        self.assertEqual(KB["VK_MAX_LABEL_LENGTH"], 40)

    def test_short_label_untouched(self):
        self.assertEqual(KB["fit_label"]("\u041e\u0447\u0435\u0440\u0435\u0434\u044c"), "\u041e\u0447\u0435\u0440\u0435\u0434\u044c")

    def test_label_of_exactly_forty_is_untouched(self):
        label = "\u0430" * 40
        self.assertEqual(KB["fit_label"](label), label)

    def test_long_label_is_trimmed_to_the_limit(self):
        self.assertEqual(len(KB["fit_label"]("\u0430" * 200)), 40)

    def test_trimmed_label_ends_with_an_ellipsis(self):
        self.assertTrue(KB["fit_label"]("\u0430" * 200).endswith("\u2026"))

    def test_none_label_does_not_crash(self):
        self.assertEqual(KB["fit_label"](None), "")

    def test_button_builder_trims_long_labels(self):
        button = KB["_btn"]("\u0431" * 120, KB["WHITE"], {"cmd": "noop"})
        self.assertLessEqual(len(button["action"]["label"]), 40)

    def test_button_payload_survives_trimming(self):
        button = KB["_btn"]("\u0431" * 120, KB["WHITE"], {"cmd": "noop"})
        self.assertIn("noop", button["action"]["payload"])

    def test_link_button_trims_long_labels(self):
        button = KB["_link_btn"]("\u0432" * 90, "https://vk.com")
        self.assertLessEqual(len(button["action"]["label"]), 40)
        self.assertEqual(button["action"]["link"], "https://vk.com")

    def test_keyboard_trims_labels_built_by_hand(self):
        raw = [[{"action": {"type": "text", "label": "\u0433" * 100}, "color": "secondary"}]]
        payload = KB["keyboard"](raw)
        self.assertNotIn("\u0433" * 41, payload)

    def test_keyboard_still_rejects_a_broken_button(self):
        with self.assertRaises(ValueError):
            KB["keyboard"]([[{"action": "not-an-object"}]])

    def test_keyboard_row_limit_is_unchanged(self):
        self.assertEqual(KB["VK_REGULAR_MAX_ROWS"], 10)
        self.assertEqual(KB["VK_INLINE_MAX_ROWS"], 6)

    def test_long_driver_name_button_is_deliverable(self):
        name = "\u0410\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 \u041a\u043e\u043d\u0441\u0442\u0430\u043d\u0442\u0438\u043d\u043e\u043f\u043e\u043b\u044c\u0441\u043a\u0438\u0439-\u0417\u0430\u0434\u0443\u043d\u0430\u0439\u0441\u043a\u0438\u0439 \u0417\u0430\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u0448\u0442\u0440\u0430\u0444"
        button = KB["_btn"](name, KB["RED"], {"cmd": "fake_call_pay"})
        self.assertLessEqual(len(button["action"]["label"]), 40)


class ParallelTakeRace(unittest.TestCase):
    """Two fast taps must not reserve two parallel requests."""

    def test_driver_row_is_locked_first(self):
        take = function_source(PARALLEL, "take")
        self.assertIn(
            "session.query(User).filter(User.id == driver.id).with_for_update()",
            take,
        )

    def test_driver_lock_precedes_the_duplicate_check(self):
        take = function_source(PARALLEL, "take")
        self.assertLess(
            take.index("User.id == driver.id"),
            take.index("parallel_driver_id == driver.id"),
        )

    def test_order_row_lock_is_still_there(self):
        take = function_source(PARALLEL, "take")
        self.assertIn(".with_for_update().one_or_none()", take)

    def test_duplicate_parallel_order_is_still_refused(self):
        take = function_source(PARALLEL, "take")
        self.assertIn("\u0423 \u0432\u0430\u0441 \u0443\u0436\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u0430 \u043f\u0430\u0440\u0430\u043b\u043b\u0435\u043b\u044c\u043d\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430", take)


class MaintenanceKeepsBusinessData(unittest.TestCase):
    """Only technical bookkeeping expires; nothing of value is deleted."""

    def test_finished_jobs_are_cleaned(self):
        self.assertIn("ScheduledJob.status != \"pending\"", MAINTENANCE)

    def test_login_attempts_are_cleaned(self):
        self.assertIn("LoginAttempt.created_at", MAINTENANCE)

    def test_retention_windows(self):
        self.assertIn("FINISHED_JOB_RETENTION_DAYS = 30", MAINTENANCE)
        self.assertIn("LOGIN_ATTEMPT_RETENTION_DAYS = 180", MAINTENANCE)

    def test_reviews_are_never_deleted(self):
        self.assertNotIn("Review", MAINTENANCE)

    def test_users_are_never_deleted(self):
        self.assertNotIn("session.query(User)", MAINTENANCE)

    def test_orders_are_never_deleted(self):
        self.assertNotIn("session.query(Order).filter", MAINTENANCE)

    def test_admin_logs_are_never_deleted(self):
        self.assertNotIn("AdminLog", MAINTENANCE)

    def test_fake_calls_and_commissions_are_never_deleted(self):
        self.assertNotIn("FakeCall", MAINTENANCE)
        self.assertNotIn("DispatcherCommission", MAINTENANCE)

    def test_pending_outbox_is_still_protected(self):
        self.assertIn("OutboxMessage.status.in_((\"sent\", \"cancelled\"))", MAINTENANCE)

    def test_deletes_stay_batched(self):
        self.assertIn("_BATCH_SIZE = 10_000", MAINTENANCE)

    def test_and_helper_is_imported(self):
        self.assertIn("from sqlalchemy import and_", MAINTENANCE)


class BackgroundWorkerLoad(unittest.TestCase):
    def test_away_notice_poll_interval_relaxed(self):
        self.assertIn("POLL_SECONDS = 5.0", AWAY)

    def test_away_notice_text_unchanged(self):
        self.assertIn("\u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u043d\u0435 \u0432\u0437\u044f\u043b\u0438 \u0432\u043e\u0434\u0438\u0442\u0435\u043b\u0438", AWAY)



if __name__ == "__main__":
    unittest.main()
