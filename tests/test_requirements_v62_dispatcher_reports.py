import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class DispatcherReportsV62Tests(unittest.TestCase):
    def src(self, rel): return (ROOT/rel).read_text("utf-8")

    def test_dispatcher_menu_rows(self):
        source=self.src("bot/keyboards.py")
        menu=source.split("def dispatcher_menu",1)[1].split("def dispatcher_booking_menu",1)[0]
        self.assertIn('''[
            _btn("📅 Бронь дальней поездки", BLUE, {"cmd": "disp_booking_new"}),
            _btn("🗓 Мои брони", BLUE, {"cmd": "disp_bookings"}),
        ]''', menu)
        self.assertIn('''[
            _btn("🚕 Очередь водителей", WHITE, {"cmd": "queue"}),
            _btn("🏷 Прайс", WHITE, {"cmd": "price"}),
        ]''', menu)
        self.assertNotIn('"👥 Водители"', menu)

    def test_income_contains_all_requested_debt_periods(self):
        source=self.src("bot/dispatcher_report_service.py")
        ast.parse(source, filename="dispatcher_report_service.py")
        for text in ("Водители должны отдать:", "За сегодня", "За вчера", "За неделю", "Задолженностей нет."):
            self.assertIn(text, source)
        self.assertIn("day_start.weekday()", source)
        self.assertIn("week_start + dt.timedelta(days=7)", source)

    def test_sunday_21_report_is_idempotent_and_started(self):
        source=self.src("bot/dispatcher_report_service.py")
        self.assertIn("local.weekday() != 6 or local.hour < 21", source)
        self.assertIn("weekly_dispatcher_report:", source)
        self.assertIn("ProcessedEvent(event_key=event_key)", source)
        self.assertIn("dispatcher.has_role(ROLE_DISPATCHER)", source)
        main=self.src("bot/main.py")
        self.assertIn("dispatcher_report_service.start_worker()", main)

    def test_income_handler_uses_same_report_formatter(self):
        handlers=self.src("bot/handlers.py")
        income=handlers.split("def disp_show_income",1)[1].split("#  Admin flow",1)[0]
        self.assertIn("dispatcher_report_service.dispatcher_income_text(session, user.id)", income)

if __name__=="__main__": unittest.main()
