from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "web/app.py"
BASE = ROOT / "web/templates/base.html"
GIVEAWAY = ROOT / "web/templates/giveaway.html"
ORDERS = ROOT / "web/templates/orders.html"
CSS = ROOT / "web/static/css/style.css"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(name)


class AdminGiveawayAndOrderSearchV90(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = read(APP)
        cls.base = read(BASE)
        cls.giveaway = read(GIVEAWAY)
        cls.orders = read(ORDERS)
        cls.css = read(CSS)

    def test_01_admin_sources_parse_and_bot_tree_was_not_extended(self) -> None:
        ast.parse(self.app, filename=str(APP))
        self.assertNotIn("\ufffd", self.app + self.base + self.giveaway + self.orders)
        self.assertFalse((ROOT.parent / "bot/web/templates/giveaway.html").exists())

    def test_02_giveaway_tab_and_authenticated_route_exist(self) -> None:
        self.assertIn("('giveaway_view','Розыгрыш','trophy')", self.base)
        self.assertIn('@app.route("/giveaway", methods=["GET", "POST"])', self.app)
        route = function_source(self.app, "giveaway_view")
        self.assertIn("@login_required\ndef giveaway_view():", self.app)
        self.assertIn('render_template(\n        "giveaway.html"', route)

    def test_03_all_requested_and_extra_conditions_exist(self) -> None:
        for field in (
            "condition_order", "condition_completed", "condition_min_completed",
            "condition_no_cancelled", "condition_phone", "condition_verified",
            "condition_rating", "winners_count", "contest_text", "prizes",
            "date_from", "date_to",
        ):
            self.assertIn(f'name="{field}"', self.giveaway)
        for label in (
            "Сделал заявку", "Есть успешная заявка", "Несколько успешных",
            "Без отмен", "Указан телефон", "Проверенный аккаунт",
            "Рейтинг пассажира",
        ):
            self.assertIn(label, self.giveaway)

    def test_04_eligibility_is_unique_scoped_and_excludes_dispatcher_orders(self) -> None:
        body = function_source(self.app, "_giveaway_candidates")
        self.assertIn("Order.dispatcher_id.is_(None)", body)
        self.assertIn(".group_by(Order.passenger_id)", body)
        self.assertIn("User.is_blocked.is_(False)", body)
        self.assertIn("condition_min_completed", body)
        self.assertIn("condition_no_cancelled", body)
        self.assertIn("condition_verified", body)
        self.assertIn("condition_rating", body)

    def test_05_winners_use_secure_random_sample_and_validate_pool(self) -> None:
        route = function_source(self.app, "giveaway_view")
        self.assertIn("secrets.SystemRandom().sample(candidates, winners_count)", route)
        self.assertIn("len(candidates) < winners_count", route)
        self.assertIn("len(prizes) not in (1,)", route)
        self.assertIn("giveaway_draw", route)

    def test_06_result_is_presentable_and_copyable(self) -> None:
        for token in (
            "Поздравляем победителей", "winner-card", "winner-prize",
            "Готовый текст для публикации", "copy-result", "winner.vk_url",
        ):
            self.assertIn(token, self.giveaway)
        self.assertIn(".giveaway-result", self.css)
        self.assertIn(".winner-grid", self.css)

    def test_07_orders_searches_passenger_driver_and_dispatcher_customer(self) -> None:
        route = function_source(self.app, "orders")
        query = function_source(self.app, "_orders_query")
        self.assertIn('q = request.args.get("q", "").strip()', route)
        route += query
        self.assertIn("Order.passenger.has(passenger_name)", route)
        self.assertIn("Order.driver.has(driver_name)", route)
        self.assertIn("Order.customer_name.ilike", route)
        self.assertIn('name="q"', self.orders)
        self.assertIn("Имя или фамилия пассажира / водителя", self.orders)

    def test_08_no_bot_python_file_was_added_for_feature(self) -> None:
        for path in (ROOT.parent / "bot/bot").glob("*.py"):
            self.assertNotIn("giveaway", read(path).casefold(), str(path))


if __name__ == "__main__":
    unittest.main()
