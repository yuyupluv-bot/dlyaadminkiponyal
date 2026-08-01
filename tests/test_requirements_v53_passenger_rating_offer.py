import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class PassengerRatingInOfferV53Tests(unittest.TestCase):
    def test_passenger_rating_is_appended_to_linked_sender(self):
        source = (ROOT / "bot/order_service.py").read_text("utf-8")
        ast.parse(source, filename="bot/order_service.py")
        self.assertIn("def passenger_rating_text", source)
        self.assertIn("passenger.passenger_rating_count", source)
        self.assertIn("passenger.passenger_rating:.1f", source)
        offer = source.split("def offer_to_next_driver", 1)[1].split("def _accept_timeout", 1)[0]
        self.assertIn('От кого: [id%s|%s] %s', offer)
        self.assertIn("passenger_rating_text(creator)", offer)

    def test_russian_review_forms_are_present(self):
        source = (ROOT / "bot/order_service.py").read_text("utf-8")
        for word in ("отзыв", "отзыва", "отзывов", "нет отзывов"):
            self.assertIn(word, source)

if __name__ == "__main__": unittest.main()
