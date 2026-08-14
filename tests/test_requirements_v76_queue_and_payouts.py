# -*- coding: utf-8 -*-
"""V76 requirements."""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class FrontOfQueueNotice(unittest.TestCase):
    def test_queue_row_stores_the_notice_id(self):
        self.assertIn("front_notice_outbox_id = Column(Integer)", read("common/models.py"))

    def test_startup_ddl_adds_the_column(self):
        self.assertIn(
            "drivers_queue ADD COLUMN IF NOT EXISTS front_notice_outbox_id",
            read("common/db_migrate.py"),
        )

    def test_migration_exists(self):
        migration = read("migrations/versions/0041_front_notice_tracking.py")
        self.assertIn('down_revision = "0040_offer_notice_tracking"', migration)

    def test_notice_is_tracked(self):
        source = function_source(read("bot/queue_service.py"), "_notify_fronts")
        self.assertIn("front_notice_outbox_id = _vk.send_tracked_message", source)

    def test_notice_is_removed_when_driver_is_not_front(self):
        source = function_source(read("bot/queue_service.py"), "_notify_fronts")
        self.assertEqual(source.count("_clear_front_notice(session, e)"), 3)
        clear = function_source(read("bot/queue_service.py"), "_clear_front_notice")
        self.assertIn("outbox_service.cancel_or_delete", clear)
        self.assertIn("entry.front_notified = False", clear)
        self.assertIn("entry.front_notice_outbox_id = None", clear)

    def test_busy_driver_loses_the_notice(self):
        source = function_source(read("bot/queue_service.py"), "_notify_fronts")
        marker = "if drv and _driver_has_active_work(session, drv.id):"
        self.assertIn(marker, source)
        after = source.split(marker, 1)[1]
        self.assertIn("_clear_front_notice(session, e)", after.split("continue", 1)[0])


class DriverFoundNotice(unittest.TestCase):
    def test_old_wording_is_gone(self):
        self.assertNotIn(
            "Водитель указывает время, через сколько прибудет.",
            read("bot/order_service.py"),
        )

    def test_new_wording(self):
        source = function_source(read("bot/order_service.py"), "notify_driver_found")
        self.assertIn(
            "Нашёлся водитель, он указывает время прибытия и выезжает к вам.",
            source,
        )
        self.assertIn("send_tracked_message", source)
        self.assertIn("_is_dispatcher_order(order)", source)

    def test_sent_after_the_driver_accepts(self):
        accept = function_source(read("bot/handlers.py"), "driver_accept")
        self.assertIn("order_service.notify_driver_found(session, order)", accept)

    def test_notice_is_still_cleaned_up_on_reoffer(self):
        clear = function_source(read("bot/order_service.py"), "clear_offer_notice")
        self.assertIn("offer_notice_outbox_id", clear)


if __name__ == "__main__":
    unittest.main()
