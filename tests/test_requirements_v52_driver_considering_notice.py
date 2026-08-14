import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class DriverConsideringNoticeV52Tests(unittest.TestCase):
    def test_passenger_is_notified_after_driver_offer_is_queued(self):
        source = (ROOT / "bot/order_service.py").read_text("utf-8")
        ast.parse(source, filename="bot/order_service.py")
        # V76: the passenger is notified only when the driver presses the
        # accept button, and the wording names the found driver.
        notice = source.split("def notify_driver_found", 1)[1].split("def clear_offer_notice", 1)[0]
        self.assertIn("order.offer_notice_outbox_id = vk.send_tracked_message", notice)
        self.assertIn(
            "Нашёлся водитель, он указывает время прибытия и выезжает к вам.",
            notice,
        )
        self.assertIn("_is_dispatcher_order(order)", notice)
        self.assertIn("keyboard=kb.passenger_waiting_keyboard()", notice)
        handlers = (ROOT / "bot/handlers.py").read_text("utf-8")
        self.assertIn("order_service.notify_driver_found(session, order)", handlers)

if __name__ == "__main__": unittest.main()
