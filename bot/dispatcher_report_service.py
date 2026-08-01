"""Dispatcher debt summaries and Sunday weekly reports."""
from __future__ import annotations

import datetime as dt
import threading
import time

from sqlalchemy import func

from common import time_utils
from common.database import session_scope
from common.logger import get_logger
from common.models import DispatcherCommission, ProcessedEvent, ROLE_DISPATCHER, User

from .vk_client import vk

log = get_logger("bot.dispatcher_reports")
CHECK_INTERVAL_SECONDS = 30
_started = False
_lock = threading.Lock()


def week_bounds(value: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    """Return local Monday 00:00 and next Monday 00:00."""
    local = time_utils.to_local(value) if value is not None else time_utils.now()
    assert local is not None
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - dt.timedelta(days=day_start.weekday())
    return week_start, week_start + dt.timedelta(days=7)


def debt_by_driver(
    session,
    dispatcher_id: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> dict[int, float]:
    query = (
        session.query(
            DispatcherCommission.driver_id,
            func.coalesce(func.sum(DispatcherCommission.amount), 0),
        )
        .filter(
            DispatcherCommission.dispatcher_id == dispatcher_id,
            DispatcherCommission.is_paid.is_(False),
        )
    )
    if start is not None:
        query = query.filter(DispatcherCommission.created_at >= start)
    if end is not None:
        query = query.filter(DispatcherCommission.created_at < end)
    return {
        int(driver_id): float(amount or 0)
        for driver_id, amount in query.group_by(DispatcherCommission.driver_id).all()
    }


def _driver_name(session, driver_id: int) -> str:
    driver = session.get(User, driver_id)
    if driver is None:
        return f"Водитель #{driver_id}"
    return driver.full_name or f"id{driver.vk_id}"


def append_debt_block(session, lines: list[str], title: str, debts: dict[int, float]) -> None:
    lines.append(f"{title}: {sum(debts.values()):.0f} ₽")
    if not debts:
        lines.append("Задолженностей нет.")
        return
    for driver_id, amount in sorted(debts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"• {_driver_name(session, driver_id)} — {amount:.0f} ₽")


def dispatcher_income_text(session, dispatcher_id: int, now: dt.datetime | None = None) -> str:
    local = time_utils.to_local(now) if now is not None else time_utils.now()
    assert local is not None
    today_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + dt.timedelta(days=1)
    yesterday_start = today_start - dt.timedelta(days=1)
    week_start, week_end = week_bounds(local)

    all_debts = debt_by_driver(session, dispatcher_id)
    today_debts = debt_by_driver(session, dispatcher_id, today_start, tomorrow_start)
    yesterday_debts = debt_by_driver(session, dispatcher_id, yesterday_start, today_start)
    week_debts = debt_by_driver(session, dispatcher_id, week_start, week_end)

    lines = [
        "💰 Мои доходы:",
        f"Водители должны отдать: {sum(all_debts.values()):.0f} ₽",
    ]
    append_debt_block(session, lines, "За сегодня", today_debts)
    append_debt_block(session, lines, "За вчера", yesterday_debts)
    lines.append("")
    append_debt_block(
        session,
        lines,
        f"За неделю ({week_start:%d.%m.%Y}–{(week_end - dt.timedelta(days=1)):%d.%m.%Y})",
        week_debts,
    )
    return "\n".join(lines)


def weekly_report_text(session, dispatcher_id: int, now: dt.datetime | None = None) -> str:
    week_start, week_end = week_bounds(now)
    debts = debt_by_driver(session, dispatcher_id, week_start, week_end)
    lines = [
        "📊 Недельный отчёт диспетчера",
        f"Период: {week_start:%d.%m.%Y}–{(week_end - dt.timedelta(days=1)):%d.%m.%Y}",
        f"Водители должны отдать за неделю: {sum(debts.values()):.0f} ₽",
    ]
    if not debts:
        lines.append("Задолженностей нет.")
    else:
        for driver_id, amount in sorted(debts.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"• {_driver_name(session, driver_id)} — {amount:.0f} ₽")
    return "\n".join(lines)


def send_due_weekly_reports(now: dt.datetime | None = None) -> int:
    """Send once on Sunday at/after 21:00 Asia/Yekaterinburg."""
    local = time_utils.to_local(now) if now is not None else time_utils.now()
    assert local is not None
    if local.weekday() != 6 or local.hour < 21:
        return 0
    week_start, _ = week_bounds(local)
    sent = 0
    with session_scope() as session:
        for dispatcher in session.query(User).all():
            if not dispatcher.has_role(ROLE_DISPATCHER):
                continue
            event_key = f"weekly_dispatcher_report:{dispatcher.id}:{week_start.date().isoformat()}"
            exists = session.query(ProcessedEvent.id).filter(
                ProcessedEvent.event_key == event_key
            ).first()
            if exists:
                continue
            session.add(ProcessedEvent(event_key=event_key))
            vk.send_message(
                dispatcher.vk_id,
                weekly_report_text(session, dispatcher.id, now=local),
            )
            sent += 1
    return sent


def _worker() -> None:
    while True:
        try:
            sent = send_due_weekly_reports()
            if sent:
                log.info("Sent weekly dispatcher reports: %s", sent)
        except Exception as exc:  # noqa: BLE001
            log.exception("Weekly dispatcher report failed: %s", exc)
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_worker, name="dispatcher-reports", daemon=True).start()
        _started = True
        log.info("Dispatcher report worker started")
