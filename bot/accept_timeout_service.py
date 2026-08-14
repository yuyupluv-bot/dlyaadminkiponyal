"""Durable watchdog for ordinary driver-offer timeouts.

The in-memory scheduler remains the primary path. This worker replays overdue
persistent ``accept`` jobs so a delayed/lost scheduler wake-up cannot leave a
silent driver on the line indefinitely. Route-parallel offers use another job
kind and are intentionally not handled here.
"""
from __future__ import annotations

import threading
import time

from common import time_utils
from common.database import session_scope
from common.logger import get_logger
from common.models import Order, ScheduledJob

log = get_logger("bot.accept_timeout_watchdog")
INTERVAL_SECONDS = 5
BATCH_SIZE = 100
_started = False
_lock = threading.Lock()


def reconcile_once() -> int:
    """Replay overdue ordinary accept jobs; row locks make this race-safe."""
    due: list[tuple[int, int]] = []
    with session_scope() as session:
        jobs = (
            session.query(ScheduledJob)
            .filter(
                ScheduledJob.kind == "accept",
                ScheduledJob.status == "pending",
                ScheduledJob.run_at <= time_utils.now(),
            )
            .order_by(ScheduledJob.run_at.asc(), ScheduledJob.id.asc())
            .limit(BATCH_SIZE)
            .all()
        )
        for job in jobs:
            order = session.get(Order, job.object_id)
            if (
                order
                and order.status == "searching"
                and order.offered_driver_id is not None
            ):
                due.append((order.id, int(order.offered_driver_id)))

    if not due:
        return 0
    from . import order_service

    repaired = 0
    for order_id, driver_id in due:
        try:
            order_service._accept_timeout(order_id, driver_id)
            repaired += 1
        except Exception as exc:  # noqa: BLE001
            # Keep the persistent row pending. The main scheduler and the next
            # watchdog pass can safely retry the idempotent timeout.
            log.exception(
                "Could not replay accept timeout order=%s driver=%s: %s",
                order_id, driver_id, exc,
            )
    return repaired


def _worker() -> None:
    while True:
        time.sleep(INTERVAL_SECONDS)
        try:
            repaired = reconcile_once()
            if repaired:
                log.warning("Replayed overdue ordinary offers: %s", repaired)
        except Exception as exc:  # noqa: BLE001
            log.exception("Accept-timeout watchdog failed: %s", exc)


def start_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(
            target=_worker,
            name="accept-timeout-watchdog",
            daemon=True,
        ).start()
        _started = True
        log.info("Accept-timeout watchdog started")
