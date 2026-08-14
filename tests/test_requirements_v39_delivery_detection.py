import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

class DeliveryV39Tests(unittest.TestCase):
    def src(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_changed_modules_parse(self):
        for rel in (
            "common/settings_service.py", "common/db_migrate.py",
            "bot/delivery_service.py", "bot/handlers.py",
            "migrations/versions/0037_delivery_price_template_fix.py",
        ):
            ast.parse(self.src(rel), filename=rel)

    def test_red_velvet_requests_are_deliveries(self):
        # V74: название магазина распознаётся отдельно от слова «доставка»:
        # вместе они дают доставку от партнёров.
        source = self.src("bot/delivery_service.py")
        self.assertIn('def is_delivery_text', source)
        self.assertIn('_DELIVERY_WORD', source)
        self.assertIn('_PARTNER_PATTERNS', source)
        self.assertIn('def is_partner_delivery_text', source)
        handlers = self.src("bot/handlers.py")
        self.assertIn('delivery_service.is_delivery_text(raw)', handlers)
        self.assertIn('delivery_service.is_partner_delivery_text(raw)', handlers)

    def test_historical_regular_row_is_reclassified(self):
        source = self.src("bot/delivery_service.py")
        fn = source.split("def is_delivery(order", 1)[1].split("def request_price", 1)[0]
        self.assertIn('order.order_type = "delivery"', fn)
        self.assertIn('is_delivery_text(_request_text(order))', fn)

    def test_bad_template_falls_back_instead_of_leaking_placeholder(self):
        source = self.src("common/settings_service.py")
        fn = source.split("def msg", 1)[1]
        self.assertIn('fallback = DEFAULTS.get(key, "")', fn)
        self.assertIn('fallback.format(**fmt)', fn)
        delivery = self.src("bot/delivery_service.py")
        self.assertIn('price=price, amount=price', delivery)

if __name__ == "__main__":
    unittest.main()
