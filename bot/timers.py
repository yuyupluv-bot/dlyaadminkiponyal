"""Scalable non-blocking timer scheduler.

A single scheduler thread replaces one ``threading.Timer`` thread per order.
Callbacks run in a small bounded worker pool, so hundreds of simultaneous
accept/waiting timers do not create hundreds of OS threads.
"""
from __future__ import annotations

import datetime as dt
import heapq
import importlib.util
import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from common.config import config
from common.logger import get_logger

log = get_logger("bot.timers")
_DB_AVAILABLE = importlib.util.find_spec("sqlalchemy") is not None

# key -> (token, callback). Old heap records are ignored after cancel/reschedule.
_entries: dict[str, tuple[int, Callable[[], None]]] = {}
_heap: list[tuple[float, int, str]] = []
_counter = itertools.count(1)
_condition = threading.Condition()
_executor = ThreadPoolExecutor(
    max_workers=config.TIMER_WORKERS,
    thread_name_prefix="timer-callback",
)


def _key(kind: str, order_id: int) -> str:
    return f"{kind}:{order_id}"


def _delete_persistent(kind: str, order_id: int) -> None:
    if not _DB_AVAILABLE:
        return
    try:
        from common.database import session_scope
        from common.models import ScheduledJob
        with session_scope() as session:
            session.query(ScheduledJob).filter(ScheduledJob.job_key == _key(kind, order_id)).delete()
    except Exception as exc:  # noqa: BLE001
        log.error("Could not clear persistent timer %s:%s: %s", kind, order_id, exc)


def _persist_retry(kind: str, order_id: int, delay: float, error: Exception) -> None:
    """Keep a failed callback durable even outside a request transaction."""
    if not _DB_AVAILABLE:
        return
    try:
        from common.database import session_scope
        from common.models import ScheduledJob
        key = _key(kind, order_id)
        run_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)
        with session_scope() as session:
            row = session.query(ScheduledJob).filter(
                ScheduledJob.job_key == key
            ).one_or_none()
            if row is None:
                row = ScheduledJob(
                    job_key=key,
                    kind=kind,
                    object_id=order_id,
                    run_at=run_at,
                    status="pending",
                )
                session.add(row)
            else:
                row.run_at = run_at
                row.status = "pending"
            row.last_error = f"callback retry: {str(error)[:500]}"
    except Exception as retry_exc:  # noqa: BLE001
        log.exception(
            "Could not persist retry for timer %s:%s: %s",
            kind, order_id, retry_exc,
        )


def _safe_run(kind: str, order_id: int, callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception as exc:  # noqa: BLE001
        log.exception("Timer %s for order %s failed: %s", kind, order_id, exc)
        # A transient DB/VK failure must not consume the durable job forever.
        # Re-run the same idempotent callback after a short delay.
        retry_delay = 15.0
        schedule(kind, order_id, retry_delay, callback, _persist=False)
        _persist_retry(kind, order_id, retry_delay, exc)
    else:
        # Keep a same-key timer scheduled while this callback was running.
        # Holding the scheduler lock closes the tiny race between checking the
        # in-memory generation and deleting its durable row: a later schedule
        # waits, then persists a fresh row after this deletion.
        with _condition:
            if _key(kind, order_id) not in _entries:
                _delete_persistent(kind, order_id)


def _scheduler() -> None:
    while True:
        with _condition:
            while not _heap:
                _condition.wait()
            deadline, token, key = _heap[0]
            current = _entries.get(key)
            if current is None or current[0] != token:
                heapq.heappop(_heap)
                continue
            remaining = deadline - time.monotonic()
            if remaining > 0:
                _condition.wait(timeout=remaining)
                continue
            heapq.heappop(_heap)
            _entries.pop(key, None)
        kind, raw_order_id = key.split(":", 1)
        _executor.submit(_safe_run, kind, int(raw_order_id), current[1])


threading.Thread(target=_scheduler, name="timer-scheduler", daemon=True).start()


def schedule(kind: str, order_id: int, delay: float, callback: Callable[[], None], *, _persist: bool = True) -> None:
    """Schedule or replace a timer for ``(kind, order_id)``."""
    key = _key(kind, order_id)
    token = next(_counter)
    deadline = time.monotonic() + max(0.0, float(delay))
    with _condition:
        _entries[key] = (token, callback)
        heapq.heappush(_heap, (deadline, token, key))
        _condition.notify()
    if _persist and _DB_AVAILABLE:
        try:
            from common.database import current_session
            from common.models import ScheduledJob
            db = current_session()
            if db is not None:
                now = dt.datetime.now(dt.timezone.utc)
                run_at = now + dt.timedelta(seconds=max(0.0, float(delay)))

                # SessionLocal deliberately disables autoflush.  Two calls to
                # schedule() in one event therefore used to add two new ORM
                # rows with the same key: the second SELECT could not see the
                # first unflushed row and the commit rolled the entire event
                # back with a UniqueViolation.  Check the session's pending
                # objects before querying the database.
                row = next(
                    (
                        candidate
                        for candidate in db.new
                        if isinstance(candidate, ScheduledJob)
                        and candidate.job_key == key
                    ),
                    None,
                )
                if row is None:
                    row = db.query(ScheduledJob).filter(
                        ScheduledJob.job_key == key
                    ).one_or_none()
                if row is not None:
                    row.run_at, row.status, row.last_error = run_at, "pending", None
                elif getattr(getattr(db.bind, "dialect", None), "name", "") == "postgresql":
                    # A second process can make the same decision between the
                    # SELECT above and INSERT.  PostgreSQL upsert makes the
                    # persistent timer a true one-row-per-key operation.
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    stmt = pg_insert(ScheduledJob).values(
                        job_key=key,
                        kind=kind,
                        object_id=order_id,
                        run_at=run_at,
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    ).on_conflict_do_update(
                        index_elements=[ScheduledJob.job_key],
                        set_={
                            "kind": kind,
                            "object_id": order_id,
                            "run_at": run_at,
                            "status": "pending",
                            "last_error": None,
                            "updated_at": now,
                        },
                    )
                    db.execute(stmt)
                else:
                    # The development/test fallback keeps the same in-session
                    # idempotency without relying on a PostgreSQL dialect.
                    db.add(
                        ScheduledJob(
                            job_key=key,
                            kind=kind,
                            object_id=order_id,
                            run_at=run_at,
                            status="pending",
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            log.error("Could not persist timer %s: %s", key, exc)
    log.debug("Scheduled timer %s for order %s in %.0fs", kind, order_id, delay)


def cancel(kind: str, order_id: int) -> None:
    key = _key(kind, order_id)
    with _condition:
        removed = _entries.pop(key, None)
        _condition.notify()
    if removed is not None:
        log.debug("Cancelled timer %s for order %s", kind, order_id)
    if not _DB_AVAILABLE:
        return
    try:
        from common.database import current_session
        from common.models import ScheduledJob
        db = current_session()
        if db is not None:
            db.query(ScheduledJob).filter(ScheduledJob.job_key == key).delete()
    except Exception as exc:  # noqa: BLE001
        log.error("Could not cancel persistent timer %s: %s", key, exc)


def cancel_all_for_order(order_id: int) -> None:
    for kind in (
        "accept", "waiting", "payment", "delivery", "fakecall", "pqueue", "pqueue_actual", "chat_actual",
        "driver_chat", "parallel_eta", "booking_chat", "eta_prearrival", "dispatcher_unclaimed",
        "route_parallel_offer",
    ):
        cancel(kind, order_id)


def pending_count() -> int:
    """Exposed for diagnostics/tests."""
    with _condition:
        return len(_entries)


def _restored_callback(kind: str, object_id: int):
    def run():
        if kind == "accept":
            from common.database import session_scope
            from common.models import Order
            from . import order_service
            with session_scope() as session:
                order = session.get(Order, object_id)
                driver_id = order.offered_driver_id if order else None
            if driver_id:
                order_service._accept_timeout(object_id, driver_id)
        elif kind == "delivery":
            from .delivery_service import _confirm_timeout
            _confirm_timeout(object_id)
        elif kind == "fakecall":
            from .fake_calls_service import _remind
            _remind(object_id)
        elif kind == "booking_chat":
            from .booking_service import expire_unclaimed_booking
            expire_unclaimed_booking(object_id)
        elif kind == "driver_chat":
            from .order_service import _driver_chat_timeout
            _driver_chat_timeout(object_id)
        elif kind == "parallel_eta":
            from .parallel_orders import _eta_timeout
            _eta_timeout(object_id)
        elif kind == "route_parallel_offer":
            from .parallel_orders import _route_offer_timeout
            _route_offer_timeout(object_id)
        elif kind == "pqueue":
            from .passenger_queue import _poll_timeout
            _poll_timeout(object_id)
        elif kind == "pqueue_actual":
            from .passenger_queue import _ask_actual_after_wait
            _ask_actual_after_wait(object_id)
        elif kind == "chat_actual":
            from .handlers import chat_actuality_timeout
            chat_actuality_timeout(object_id)
        elif kind == "eta_prearrival":
            from .order_service import _prearrival_notice
            _prearrival_notice(object_id)
        elif kind == "dispatcher_unclaimed":
            from .passenger_queue import _dispatcher_unclaimed_timeout
            _dispatcher_unclaimed_timeout(object_id)
        elif kind == "driver_away":
            from .driver_away_timeout_service import expire
            expire(object_id)
    return run


def restore_persistent() -> None:
    """Restore timers committed before a process restart."""
    if not _DB_AVAILABLE:
        return
    try:
        from common.database import session_scope
        from common.models import ScheduledJob
        with session_scope() as session:
            # The durable row is kept pending until its callback succeeds. If
            # a process dies during execution, restart safely replays it.
            rows = session.query(ScheduledJob).filter(
                ScheduledJob.status == "pending"
            ).all()
            jobs = [(r.kind, r.object_id, r.run_at) for r in rows]
        for kind, object_id, run_at in jobs:
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=dt.timezone.utc)
            delay = max(0.0, (run_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
            schedule(kind, object_id, delay, _restored_callback(kind, object_id), _persist=False)
        if jobs:
            log.info("Restored %s persistent timers", len(jobs))
    except Exception as exc:  # noqa: BLE001
        log.exception("Persistent timer recovery failed: %s", exc)
