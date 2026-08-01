import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class DriverConsideringNoticeV52Tests(unittest.TestCase):
    def test_passenger_is_notified_after_driver_offer_is_queued(self):
        source = (ROOT / "bot/order_service.py").read_text("utf-8")
        ast.parse(source, filename="bot/order_service.py")
        offer = source.split("def offer_to_next_driver", 1)[1].split("def _accept_timeout", 1)[0]
        send_pos = offer.index("order.offer_outbox_id = vk.send_tracked_message")
        notice_pos = offer.index("Водитель указывает время, через сколько прибудет.")
        self.assertLess(send_pos, notice_pos)
        self.assertIn("if order.offer_outbox_id and creator and not _is_dispatcher_order(order):", offer)
        self.assertIn("keyboard=kb.passenger_waiting_keyboard()", offer)

if __name__ == "__main__": unittest.main()
