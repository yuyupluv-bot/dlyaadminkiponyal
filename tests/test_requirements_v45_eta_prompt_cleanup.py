import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class EtaPromptCleanupV45Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_changed_modules_parse(self):
        for rel in ("bot/handlers.py", "bot/delivery_service.py", "bot/parallel_orders.py"):
            ast.parse(self.src(rel), filename=rel)

    def test_normal_eta_prompt_is_tracked_and_finalized(self):
        handlers = self.src("bot/handlers.py")
        menu = handlers.split("def _show_eta_menu", 1)[1].split("def driver_show_eta_menu", 1)[0]
        self.assertIn("order.offer_outbox_id = vk.send_tracked_message", menu)
        apply = handlers.split("def _apply_eta", 1)[1].split("def driver_set_eta", 1)[0]
        self.assertIn("order_service.finalize_offer_message", apply)
        self.assertIn("Выбрано время прибытия", apply)

    def test_delivery_eta_prompt_is_tracked(self):
        delivery = self.src("bot/delivery_service.py")
        confirm = delivery.split("def _confirm", 1)[1].split("def _decline", 1)[0]
        self.assertIn("order.offer_outbox_id = vk.send_tracked_message", confirm)

    def test_parallel_eta_prompt_is_tracked_and_finalized(self):
        parallel = self.src("bot/parallel_orders.py")
        take = parallel.split("def take", 1)[1].split("def save_eta", 1)[0]
        self.assertIn("order.offer_outbox_id = vk.send_tracked_message", take)
        save = parallel.split("def save_eta", 1)[1].split("def add_eta", 1)[0]
        self.assertIn("order_service.finalize_offer_message", save)
        self.assertIn("Выбрано время прибытия", save)

if __name__ == "__main__":
    unittest.main()
