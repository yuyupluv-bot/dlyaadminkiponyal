"""Export statistics to an .xlsx workbook using openpyxl."""
from __future__ import annotations

import io
import datetime as dt
from common import time_utils

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from common.models import Order, User


def _header(ws, headers: list[str]) -> None:
    fill = PatternFill("solid", fgColor="0D6EFD")
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill


def build_stats_workbook(session: Session) -> io.BytesIO:
    wb = Workbook()

    # --- Orders sheet ---
    ws = wb.active
    ws.title = "Заказы"
    _header(ws, ["ID", "Статус", "Откуда", "Куда", "Цена", "Ожидание", "Км", "Создан"])
    for o in session.query(Order).order_by(Order.created_at.desc()).all():
        ws.append([
            o.id, o.status, o.address_from, o.address_to,
            float(o.price or 0), float(o.waiting_fee or 0),
            o.distance_km, time_utils.format_local(o.created_at, "%Y-%m-%d %H:%M") if o.created_at else "",
        ])

    # --- Drivers sheet ---
    ws2 = wb.create_sheet("Водители")
    # Requirement 10: the «Заработано» column was removed together with the
    # ``total_earned`` DB field. Show rating + number of reviews instead.
    _header(ws2, ["ID", "Имя", "VK ID", "Авто", "Рейтинг", "Отзывов"])
    for d in session.query(User).filter(User.role == "driver").all():
        ws2.append([
            d.id, d.full_name, d.vk_id,
            f"{d.car_model or ''} {d.car_number or ''}".strip(),
            d.rating, int(d.rating_count or 0),
        ])

    # --- Summary sheet ---
    ws3 = wb.create_sheet("Сводка")
    total_orders = session.query(Order).count()
    completed = session.query(Order).filter(Order.status == "completed").count()
    revenue = sum(float(o.price or 0) for o in session.query(Order).filter(Order.status == "completed"))
    ws3.append(["Метрика", "Значение"])
    ws3.append(["Всего заказов", total_orders])
    ws3.append(["Завершено", completed])
    ws3.append(["Выручка, ₽", round(revenue, 2)])
    ws3.append(["Сформировано", time_utils.now().strftime("%Y-%m-%d %H:%M")])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
