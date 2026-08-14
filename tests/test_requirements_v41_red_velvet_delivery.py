import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RedVelvetDeliveryV41Tests(unittest.TestCase):
    def source(self):
        return (ROOT / "bot/delivery_service.py").read_text("utf-8")

    def test_changed_modules_parse(self):
        ast.parse(self.source(), filename="bot/delivery_service.py")
        ast.parse((ROOT / "bot/handlers.py").read_text("utf-8"), filename="bot/handlers.py")

    def test_red_velvet_forms_are_recognized(self):
        source = self.source()
        # V74: формы названия покрываются обобщёнными шаблонами партнёров.
        for token in (
            "красн",
            "бар[хш]ат",
            "бар[хш]от",
            "кр\\.?\\s*бар[хш]",
            "гал{1,2}акт",
        ):
            self.assertIn(token, source)

    def test_shop_match_is_used_for_all_free_form_orders(self):
        handlers = (ROOT / "bot/handlers.py").read_text("utf-8")
        self.assertIn("delivery_service.is_delivery_text(raw)", handlers)
        historical = self.source().split("def is_delivery(order", 1)[1].split("def request_price", 1)[0]
        self.assertIn("is_delivery_text(_request_text(order))", historical)
        self.assertIn('order.order_type = "delivery"', historical)

    def test_broad_unrequested_keywords_were_removed(self):
        source = self.source()
        self.assertNotIn("привез(?:ти", source)
        self.assertNotIn("курьер[а-яё]", source)
        self.assertNotIn("забрать|забери", source)

if __name__ == "__main__":
    unittest.main()
