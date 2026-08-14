import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReviewDeleteRecalculateV54Tests(unittest.TestCase):
    def test_delete_recalculates_both_rating_directions(self):
        source = (ROOT / "web/app.py").read_text("utf-8")
        ast.parse(source, filename="web/app.py")
        helper = source.split("def _recalculate_rating_after_review_delete", 1)[1].split('@app.route("/reviews/<int:review_id>/delete"', 1)[0]
        self.assertIn('review.kind == "driver_to_passenger"', helper)
        self.assertIn('Review.kind == "passenger_to_driver"', helper)
        self.assertIn("target.passenger_rating_sum", helper)
        self.assertIn("target.passenger_rating_count", helper)
        self.assertIn("target.rating_sum", helper)
        self.assertIn("target.rating_count", helper)
        self.assertIn("Review.id != review.id", helper)
        self.assertIn("order.rating = None", helper)

    def test_route_recalculates_before_delete_and_commit(self):
        source = (ROOT / "web/app.py").read_text("utf-8")
        route = source.split("def delete_review(review_id):", 1)[1].split("#  False calls", 1)[0]
        recalc = route.index("_recalculate_rating_after_review_delete(s, r)")
        delete = route.index("s.delete(r)")
        commit = route.index("s.commit()")
        self.assertLess(recalc, delete)
        self.assertLess(delete, commit)
        self.assertIn("рейтинг пересчитан", route)

    def test_startup_repairs_historical_rating_drift(self):
        guard = self.src("common/db_migrate.py") if hasattr(self, "src") else (ROOT / "common/db_migrate.py").read_text("utf-8")
        self.assertIn("UPDATE users u SET rating_sum=COALESCE", guard)
        self.assertIn("UPDATE users u SET passenger_rating_sum=COALESCE", guard)
        self.assertIn("passenger_to_driver", guard)
        self.assertIn("driver_to_passenger", guard)

if __name__ == "__main__": unittest.main()
