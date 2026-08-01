# -*- coding: utf-8 -*-
"""V69: admin panel tab with per-driver completed requests for three days."""
import datetime as dt
import importlib.util
import os
import unittest

ADMIN = "/data/admin"
SERVICE = os.path.join(ADMIN, "web/statistics_service.py")


def load_service():
    spec = importlib.util.spec_from_file_location("stats_service_v69", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(rel):
    with open(os.path.join(ADMIN, rel), encoding="utf-8") as handle:
        return handle.read()


class FakeDriver:
    def __init__(self, driver_id, name, vk_id):
        self.id = driver_id
        self.full_name = name
        self.vk_id = vk_id
        self.car_full = "Lada Granta"


class FakeOrder:
    def __init__(self, driver, when, status="completed", price=100):
        self.driver = driver
        self.driver_id = driver.id if driver else None
        self.completed_at = when
        self.created_at = when
        self.status = status
        self.price = price


@unittest.skipUnless(os.path.exists(SERVICE), "admin build only")
class DriversTabV69(unittest.TestCase):
    def setUp(self):
        self.service = load_service()
        self.tz = dt.timezone(dt.timedelta(hours=5))
        self.now = dt.datetime(2026, 8, 2, 12, 0, tzinfo=self.tz)

    def stats(self, orders):
        return self.service.build_driver_day_stats(orders, self.now, self.tz)

    def test_three_days_are_reported(self):
        stats = self.stats([])
        labels = [day["label"] for day in stats["days"]]
        self.assertEqual(["\u041f\u043e\u0437\u0430\u0432\u0447\u0435\u0440\u0430", "\u0412\u0447\u0435\u0440\u0430", "\u0421\u0435\u0433\u043e\u0434\u043d\u044f"], labels)
        self.assertEqual("31.07.2026", stats["days"][0]["date"])
        self.assertEqual("01.08.2026", stats["days"][1]["date"])
        self.assertEqual("02.08.2026", stats["days"][2]["date"])

    def test_counts_are_grouped_per_day_and_sorted_desc(self):
        anna = FakeDriver(1, "\u0410\u043d\u043d\u0430", 10)
        boris = FakeDriver(2, "\u0411\u043e\u0440\u0438\u0441", 20)
        day0 = self.now - dt.timedelta(days=2)
        day1 = self.now - dt.timedelta(days=1)
        orders = [
            FakeOrder(anna, day0),
            FakeOrder(anna, day1),
            FakeOrder(anna, self.now),
            FakeOrder(boris, self.now),
            FakeOrder(boris, self.now),
        ]
        stats = self.stats(orders)
        self.assertEqual(["\u0410\u043d\u043d\u0430", "\u0411\u043e\u0440\u0438\u0441"], [row["name"] for row in stats["rows"]])
        self.assertEqual([1, 1, 1], stats["rows"][0]["counts"])
        self.assertEqual([0, 0, 2], stats["rows"][1]["counts"])
        self.assertEqual([1, 2], [row["place"] for row in stats["rows"]])
        self.assertEqual(5, stats["total"])
        self.assertEqual([1, 1, 3], stats["totals"])
        totals = [row["total"] for row in stats["rows"]]
        self.assertEqual(sorted(totals, reverse=True), totals)

    def test_only_completed_orders_in_window_are_counted(self):
        anna = FakeDriver(1, "\u0410\u043d\u043d\u0430", 10)
        orders = [
            FakeOrder(anna, self.now, status="cancelled"),
            FakeOrder(anna, self.now - dt.timedelta(days=5)),
            FakeOrder(None, self.now),
            FakeOrder(anna, self.now),
        ]
        stats = self.stats(orders)
        self.assertEqual(1, stats["total"])
        self.assertEqual(1, len(stats["rows"]))
        self.assertEqual(1, stats["drivers"]) 

    def test_tab_is_registered_in_admin_panel(self):
        app_src = read("web/app.py")
        self.assertIn('@app.route("/drivers")', app_src)
        self.assertIn("def drivers_view():", app_src)
        self.assertIn("build_driver_day_stats(orders, now, time_utils.LOCAL_TZ)", app_src)
        self.assertIn('render_template("drivers.html"', app_src)
        self.assertIn("drivers_view", read("web/templates/base.html"))
        page = read("web/templates/drivers.html")
        # Day columns are rendered from the calculated labels.
        self.assertIn("{% for day in stats.days %}", page)
        self.assertIn("{{ day.label }}", page)
        self.assertIn("\u0412\u0441\u0435\u0433\u043e", page)
        self.assertIn("stats.rows", page)


if __name__ == "__main__":
    unittest.main()
