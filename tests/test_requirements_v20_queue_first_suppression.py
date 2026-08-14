import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "bot/queue_service.py"


class QueueFirstSuppressionV20Tests(unittest.TestCase):
    def test_queue_service_still_parses(self):
        ast.parse(QUEUE.read_text("utf-8"), filename=str(QUEUE))

    def test_waiting_order_suppresses_first_in_queue_message(self):
        source = QUEUE.read_text("utf-8")
        helper = source.split("def _line_has_waiting_assignment", 1)[1].split("def _notify_fronts", 1)[0]
        notify = source.split("def _notify_fronts", 1)[1]

        self.assertIn("parallel_orders.available(session)", helper)
        self.assertIn('line_name == "горнозаводск"', helper)
        self.assertIn("parallel_orders.free_line_city(order)", helper)
        self.assertIn("parallel_orders.is_route_fallback(order)", helper)

        guard_pos = notify.index("if _line_has_waiting_assignment(session, e.city_id):")
        send_pos = notify.index('_vk.send_tracked_message(')
        self.assertLess(guard_pos, send_pos)
        guarded_block = notify[guard_pos:send_pos]
        self.assertIn("continue", guarded_block)
        # V76: the notice is tracked, so suppressing it also deletes an
        # already delivered message instead of just clearing the flag.
        self.assertIn("_clear_front_notice(session, e)", guarded_block)


if __name__ == "__main__":
    unittest.main()
