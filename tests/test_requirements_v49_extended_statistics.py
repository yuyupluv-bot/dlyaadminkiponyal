import ast, datetime as dt, importlib.util, unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
class ExtendedStatisticsTests(unittest.TestCase):
    def src(self,p): return (ROOT/p).read_text('utf-8')
    def test_sources_parse_and_route_exists(self):
        ast.parse(self.src('web/app.py')); ast.parse(self.src('web/statistics_service.py'))
        self.assertIn('@app.route("/statistics")',self.src('web/app.py'))
        self.assertIn("('statistics_view','Статистика','bar-chart-line')",self.src('web/templates/base.html'))
    def test_all_requested_sections_exist(self):
        t=self.src('web/templates/statistics.html')
        for label in ('Выполнено','Отменено','Без водителя','Поиск водителя','Время поездки','Типы обращений','Причины отмен','Средний чек по типам','Сравнение с предыдущей неделей','Топ водителей','Популярные направления','Выручка'):
            self.assertIn(label,t)
    def test_calculations(self):
        spec=importlib.util.spec_from_file_location('stats',ROOT/'web/statistics_service.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        tz=dt.timezone(dt.timedelta(hours=5)); now=dt.datetime(2026,7,30,12,tzinfo=tz)
        driver=SimpleNamespace(full_name='Водитель 1',rating=4.8)
        def order(hours,status='completed',price=500,driver_id=1,cancelled_by=None,otype='regular'):
            created=now-dt.timedelta(hours=hours)
            return SimpleNamespace(created_at=created,status=status,price=price,driver_id=driver_id,driver=driver,driver_accept_time=created+dt.timedelta(minutes=10) if driver_id else None,driver_departed_at=created+dt.timedelta(minutes=15) if driver_id else None,completed_at=created+dt.timedelta(minutes=45) if status=='completed' else None,cancelled_by=cancelled_by,decline_count=0,last_decline_reason=None,declined_driver_ids='[]',order_type=otype,route_text='А → Б',address_from='А',address_to='Б')
        rows=[order(1),order(2,status='cancelled',price=0,driver_id=None,cancelled_by='passenger'),order(3,otype='delivery')]
        bookings=[SimpleNamespace(created_at=now-dt.timedelta(hours=4))]
        s=m.build_statistics(rows,bookings,now,tz)
        self.assertEqual(s['total'],3); self.assertEqual(s['completed'],2); self.assertEqual(s['revenue'],1000); self.assertEqual(s['booking_count'],1)
        self.assertEqual(s['popular_routes'][0]['count'],3); self.assertEqual(s['top_drivers'][0]['completed'],2)
        self.assertEqual(len(s['daily']),7); self.assertEqual(len(s['hourly']),24); self.assertEqual(len(s['comparisons']),5)
if __name__=='__main__': unittest.main()
