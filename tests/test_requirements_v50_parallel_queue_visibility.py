import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ParallelQueueVisibilityV50Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_changed_modules_parse(self):
        for rel in ("bot/parallel_orders.py", "bot/handlers.py", "bot/keyboards.py"):
            ast.parse(self.src(rel), filename=rel)

    def test_parallel_card_keeps_linked_passenger_and_eta_context(self):
        source = self.src("bot/parallel_orders.py")
        take = source.split("def take", 1)[1].split("def save_eta", 1)[0]
        save = source.split("def save_eta", 1)[1].split("def add_eta", 1)[0]
        self.assertIn("Пассажир: {passenger_label}", take)
        self.assertIn("[id{passenger.vk_id}|", take)
        self.assertIn("Вы выбрали параллельную заявку", take)
        self.assertIn("Ваша заявка", take)
        self.assertIn("Через сколько вы будете у клиента", take)
        self.assertIn("Выбрано время прибытия", save)
        # The card is edited, not resent: repeating the full block would
        # print the same request twice in the driver's chat.
        self.assertNotIn("Вы выбрали параллельную заявку", save)

    def test_dispatcher_has_same_queue_view(self):
        handlers = self.src("bot/handlers.py")
        dispatcher = handlers.split("def handle_dispatcher", 1)[1].split("def disp_show_bookings", 1)[0]
        self.assertIn('if cmd == "queue"', dispatcher)
        self.assertIn("show_queue(session, user, dispatcher_view=True)", dispatcher)
        self.assertIn("dispatcher_view: bool = False", handlers)
        self.assertIn("Очередь водителей", self.src("bot/keyboards.py"))

    def test_passengers_see_away_drivers(self):
        handlers = self.src("bot/handlers.py")
        view = handlers.split("def show_free_drivers", 1)[1].split("def show_all_drivers", 1)[0]
        self.assertIn('statuses.get(driver.id) == "away"', view)
        self.assertIn("visible = free + busy + away", view)
        self.assertIn('status = "Отлучился"', view)


if __name__ == "__main__":
    unittest.main()
