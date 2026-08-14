# -*- coding: utf-8 -*-
"""V67: parallel card text, kept voice attachment, notice cleanup, queue-front."""
import os
import unittest

ROOTS = ["/data/bot", "/data/admin"]


def read(rel):
    out = []
    for root in ROOTS:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                out.append((path, handle.read()))
    assert out, "missing file everywhere: " + rel
    return out


def block(src, marker, size=2500):
    index = src.index(marker)
    return src[index:index + size]


class ParallelCardText(unittest.TestCase):
    def test_finalized_card_appends_only_selected_time(self):
        for path, src in read("bot/parallel_orders.py"):
            body = block(src, "def save_eta(")
            self.assertIn("Выбрано время прибытия", body, path)
            final = body[body.index("finalize_offer_message"):]
            final = final[:final.index(")\n")]
            self.assertNotIn("Вы выбрали параллельную заявку", final, path)
            self.assertNotIn("Через сколько вы будете у клиента", final, path)


class VoiceSurvivesEdit(unittest.TestCase):
    def test_edit_message_accepts_attachment(self):
        for path, src in read("bot/vk_client.py"):
            body = block(src, "    def edit_message(")
            self.assertIn("attachment: str | None = None,", body, path)
            self.assertIn('params["attachment"] = attachment', body, path)

    def test_finalize_keeps_original_attachment(self):
        for path, src in read("bot/outbox_service.py"):
            body = block(src, "def finalize_tracked_message(")
            self.assertIn("row.attachment", body, path)
            worker = block(src, "def _finalize_one(")
            self.assertIn("row.attachment", worker, path)


class StaleNoticesRemoved(unittest.TestCase):
    def test_order_tracks_notice_ids(self):
        for path, src in read("common/models.py"):
            self.assertIn("offer_notice_outbox_id = Column(Integer", src, path)
            self.assertIn("search_notice_outbox_id = Column(Integer", src, path)
        for path, src in read("common/db_migrate.py"):
            self.assertIn("ADD COLUMN IF NOT EXISTS offer_notice_outbox_id", src, path)
            self.assertIn("ADD COLUMN IF NOT EXISTS search_notice_outbox_id", src, path)

    def test_notices_are_tracked_messages(self):
        for path, src in read("bot/order_service.py"):
            self.assertIn(
                "order.offer_notice_outbox_id = vk.send_tracked_message(",
                src,
                path,
            )
            index = src.rindex("Водитель указывает время")
            self.assertIn(
                "order.offer_notice_outbox_id = vk.send_tracked_message(",
                src[max(0, index - 600):index],
                path,
            )
        for path, src in read("bot/passenger_queue.py"):
            index = src.rindex("Нашёлся свободный водитель")
            self.assertIn(
                "order.search_notice_outbox_id = vk.send_tracked_message(",
                src[max(0, index - 600):index],
                path,
            )

    def test_reoffer_clears_previous_notices(self):
        for path, src in read("bot/order_service.py"):
            self.assertIn("def clear_offer_notice(", src, path)
            self.assertIn("outbox_service.cancel_or_delete(session, outbox_id)", src, path)
            body = block(src, "def offer_to_next_driver(", 3500)
            self.assertIn("clear_offer_notice(session, order)", body, path)
            no_driver = block(src, "def _handle_no_driver(", 1500)
            self.assertIn("clear_offer_notice(session, order)", no_driver, path)


class QueueFrontNotice(unittest.TestCase):
    def test_busy_driver_is_not_first_in_queue(self):
        for path, src in read("bot/queue_service.py"):
            self.assertIn("def _driver_has_active_work(", src, path)
            body = block(src, "def _notify_fronts(", 3000)
            self.assertGreaterEqual(body.count("_driver_has_active_work(session"), 2, path)
            self.assertIn("msg_queue_first", body, path)

    def test_active_work_covers_offer_and_parallel(self):
        for path, src in read("bot/queue_service.py"):
            body = block(src, "def _driver_has_active_work(", 1500)
            self.assertIn("Order.offered_driver_id == driver_id", body, path)
            self.assertIn("Order.parallel_driver_id == driver_id", body, path)
            self.assertIn('"in_progress"', body, path)


if __name__ == "__main__":
    unittest.main()
