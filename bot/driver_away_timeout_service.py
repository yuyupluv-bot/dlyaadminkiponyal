"""Persistent 30-minute limit for drivers in the ``away`` state."""
from __future__ import annotations

from common import audit
from common.database import session_scope
from common.logger import get_logger
from common.models import ScheduledJob, User

from . import keyboards as kb
from . import timers
from .roles import can_switch_role
from .states_service import States, reset
from .vk_client import vk

log = get_logger("bot.driver_away_timeout")
TIMER_KIND = "driver_away"
TIMEOUT_SECONDS = 30 * 60
TIMEOUT_MESSAGE = (
    "☕ Вы отлучились более чем на 30 минут, поэтому вас сняли с линии. "
    "Выберите линию заново."
)


def schedule(driver_id: int) -> None:
    """Start or replace the persistent away timer for one driver."""
    driver_id = int(driver_id)
    timers.schedule(
        TIMER_KIND,
        driver_id,
        TIMEOUT_SECONDS,
        lambda: expire(driver_id),
    )


def cancel(driver_id: int) -> None:
    """Cancel the away timer when the driver leaves the away state."""
    timers.cancel(TIMER_KIND, int(driver_id))


def schedule_missing_for_away_drivers() -> int:
    """Cover drivers who were already away when this release started."""
    scheduled = 0
    with session_scope() as session:
        existing_ids = {
            int(object_id)
            for (object_id,) in session.query(ScheduledJob.object_id).filter(
                ScheduledJob.kind == TIMER_KIND,
                ScheduledJob.status == "pending",
            ).all()
        }
        away_ids = [
            int(driver_id)
            for (driver_id,) in session.query(User.id).filter(
                User.driver_status == "away",
            ).all()
        ]
        for driver_id in away_ids:
            if driver_id in existing_ids:
                continue
            schedule(driver_id)
            scheduled += 1
    if scheduled:
        log.info("Scheduled missing away timeouts at startup: %s", scheduled)
    return scheduled


def expire(driver_id: int) -> bool:
    """Remove an unchanged away driver from the line and show the main menu."""
    with session_scope() as session:
        driver = (
            session.query(User)
            .filter(User.id == int(driver_id))
            .with_for_update()
            .one_or_none()
        )
        # A stale callback must never remove a driver who already returned,
        # took an order or left the line on their own.
        if driver is None or driver.driver_status != "away":
            return False

        from . import queue_service

        queue_service.leave_queue(session, driver)
        driver.is_on_line = False
        reset(session, driver.vk_id, States.D_MENU)
        vk.send_message(
            driver.vk_id,
            TIMEOUT_MESSAGE,
            keyboard=kb.driver_menu(
                on_line=False,
                show_role_switch=can_switch_role(driver),
            ),
        )
        audit.record(session, "driver_away_timeout", f"driver={driver.id}")
        log.info("Driver removed from line after 30-minute away timeout: %s", driver.id)
        return True
