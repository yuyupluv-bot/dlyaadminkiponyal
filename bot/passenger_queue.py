"""Passenger waiting queue (requirement 4).

When every driver is busy, the passenger's order is parked in the
``passenger_queue`` table. As soon as a driver frees up we poll the head
passenger with «Ваша заявка ещё актуальна?»:
  * «Да»  → the order is offered to the first free driver;
  * «Нет» / timeout → the passenger is dropped and the next one is polled.

The queue is a strict FIFO ordered by ``position`` (creation order).
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.logger import get_logger
from common import time_utils
from common.models import Order, PassengerQueue, User
from common.settings_service import get_int, msg

from . import keyboards as kb
from . import order_service, outbox_service, queue_service, timers
from .states_service import States, reset, set_state
from .vk_client import vk

log = get_logger("bot.pqueue")
_worker_started = False
_worker_lock = threading.Lock()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _max_position(session: Session) -> int:
    return session.query(func.coalesce(func.max(PassengerQueue.position), 0)).scalar() or 0


def enqueue(session: Session, order: Order) -> PassengerQueue:
    """Park an order in the waiting queue (idempotent per order)."""
    existing = (
        session.query(PassengerQueue)
        .filter(PassengerQueue.order_id == order.id)
        .one_or_none()
    )
    if existing:
        return existing
    entry = PassengerQueue(
        passenger_id=order.passenger_id,
        order_id=order.id,
        city_id=order.city_id,
        # Every unassigned order is immediately visible to busy drivers as a
        # parallel option. Do not hold it behind a passenger confirmation.
        status="waiting",
        position=_max_position(session) + 1,
    )
    session.add(entry)
    order.status = "queued"
    session.flush()
    return entry


def _clear_actuality_prompt(
    session: Session,
    entry: PassengerQueue,
    result_text: str | None = None,
    delete: bool = False,
) -> None:
    outbox_id = entry.actuality_prompt_outbox_id
    if outbox_id:
        if delete:
            outbox_service.cancel_or_delete(session, outbox_id)
        else:
            outbox_service.finalize_tracked_message(session, outbox_id, result_text or "")
        entry.actuality_prompt_outbox_id = None


def pause_actuality_prompts(session: Session, except_order_id: int | None = None) -> None:
    """Delete stale questions while the only available driver is occupied."""
    entries = session.query(PassengerQueue).filter(
        PassengerQueue.status == "polling"
    ).all()
    for entry in entries:
        if except_order_id is not None and entry.order_id == except_order_id:
            continue
        timers.cancel("pqueue", entry.order_id)
        _clear_actuality_prompt(session, entry, delete=True)
        entry.status = "waiting"
        entry.poll_expires_at = None
        passenger = session.get(User, entry.passenger_id)
        if passenger:
            set_state(
                session,
                passenger.vk_id,
                States.P_WAITING,
                {"order_id": entry.order_id},
                merge=False,
            )


def remove(session: Session, order_id: int) -> None:
    order = session.get(Order, order_id)
    if order and (
        order.parallel_offer_driver_id
        or order.parallel_offer_outbox_id
        or order.parallel_notified_driver_ids
    ):
        from . import parallel_orders
        parallel_orders.cancel_route_offer(session, order)
    timers.cancel("pqueue_actual", order_id)
    timers.cancel("pqueue", order_id)
    entry = session.query(PassengerQueue).filter(PassengerQueue.order_id == order_id).one_or_none()
    if entry:
        _clear_actuality_prompt(session, entry, delete=True)
        session.delete(entry)


def position(session: Session, order_id: int) -> int | None:
    entry = session.query(PassengerQueue).filter(PassengerQueue.order_id == order_id).one_or_none()
    if not entry or entry.status not in ("waiting", "awaiting_choice", "polling"):
        return None
    ahead = session.query(PassengerQueue).filter(
        PassengerQueue.status.in_(("waiting", "polling")),
        PassengerQueue.position < entry.position,
    ).count()
    return ahead + 1


def dispatch_new_order(session: Session, order: Order) -> None:
    """Called right after a passenger creates an order.

    If a free driver exists, offer immediately; otherwise park the order and
    tell the passenger that everyone is busy.
    """
    # Village-route priority is strict: the exact Пашия/Кусья FIFO line first,
    # then one matching busy driver travelling from Gornozavodsk, and only then
    # the Gornozavodsk FIFO fallback. This applies to explicit «из Пашии/Кусьи»
    # requests and when Пашия/Кусья is the first or second word of the request.
    from . import parallel_orders

    route_city = parallel_orders.route_priority_city(order)
    free_city = parallel_orders.free_line_city(order)
    if route_city:
        if queue_service.has_waiting_driver(
            session, route_city, line_scope="exact"
        ):
            order_service.offer_to_next_driver(
                session, order, line_scope="exact", line_name=route_city
            )
            return
        enqueue(session, order)
        if parallel_orders.notify_busy_drivers(session, order):
            passenger = session.get(User, order.passenger_id)
            if passenger and not order.dispatcher_id:
                city_form = {"Кусья": "Кусьи", "Пашия": "Пашии"}[route_city]
                vk.send_message(
                    passenger.vk_id,
                    f"У нас есть водитель, который поехал до {city_form}. "
                    "Мы его уведомим, если он сможет, он возьмет вашу заявку, ожидайте.",
                    keyboard=kb.passenger_waiting_keyboard(),
                )
            return
        # No suitable route driver (including a trip-scoped timeout
        # suppression): do not start another one-minute wait. Continue with
        # the strict Gornozavodsk FIFO fallback immediately.
        parallel_orders._fallback_to_free_drivers(
                    session, order, recheck_village=True
                )
        passenger = session.get(User, order.passenger_id)
        if passenger and not order.dispatcher_id and order.status == "queued":
            vk.send_message(
                passenger.vk_id,
                msg(session, "msg_wait_first_free"),
                keyboard=kb.passenger_waiting_keyboard(),
            )
        return

    if order_service.has_eligible_waiting_driver(session, order):
        order_service.offer_to_next_driver(session, order)
        return

    # ``offer_to_next_driver`` parks the order through _handle_no_driver when
    # nobody is free.
    order_service.offer_to_next_driver(session, order)



def _dispatcher_unclaimed_timeout(order_id: int) -> None:
    """Cancel a dispatcher request if no driver took it within 30 minutes."""
    from common.database import session_scope
    from .states_service import reset

    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order or not order.dispatcher_id:
            return
        # An offer being shown is not an acceptance. Assigned and reserved
        # parallel requests have already been taken and must remain active.
        if order.driver_id or order.parallel_driver_id or order.status in (
            "assigned", "parallel_assigned", "arrived", "in_progress", "completed", "cancelled"
        ):
            return
        if order.status not in ("created", "searching", "queued", "chat_search", "no_drivers"):
            return
        offered = session.get(User, order.offered_driver_id) if order.offered_driver_id else None
        if offered:
            queue_service.release_offer(session, offered)
            reset(session, offered.vk_id, States.D_MENU)
            vk.send_message(
                offered.vk_id,
                f"Заявка #{order.id} автоматически отменена: за 30 минут её не взяли.",
                keyboard=kb.driver_menu(on_line=bool(offered.is_on_line)),
            )
        order.offered_driver_id = None
        order.status = "cancelled"
        order.cancelled_at = time_utils.now()
        remove(session, order.id)
        timers.cancel("accept", order.id)
        dispatcher = session.get(User, order.dispatcher_id)
        if dispatcher:
            vk.send_message(
                dispatcher.vk_id,
                f"⏱ Заявка #{order.id} автоматически отменена: за 30 минут водитель её не взял.",
                keyboard=kb.dispatcher_menu(),
            )
        from . import parallel_orders
        parallel_orders.refresh_busy_driver_menus(session)


def _ask_actual_after_wait(order_id: int) -> None:
    """Compatibility callback for old persisted timers.

    Actuality is now requested only when a free or parallel driver is really
    available, so an old three-minute timer merely runs the opportunity check.
    """
    from common.database import session_scope
    with session_scope() as session:
        try_promote(session)


def _head(session: Session) -> PassengerQueue | None:
    return (
        session.query(PassengerQueue)
        .filter(PassengerQueue.status.in_(("waiting", "polling")))
        .order_by(PassengerQueue.position.asc())
        .first()
    )


def try_promote(session: Session) -> None:
    """Promote waiting orders, asking actuality only after a real opportunity."""
    # If the driver became offered/busy while a question was visible, remove
    # the question and ask again only after another driver is truly free.
    active_polls = session.query(PassengerQueue).filter(
        PassengerQueue.status == "polling"
    ).all()
    for polled in active_polls:
        polled_order = session.get(Order, polled.order_id)
        if not polled_order or not order_service.has_eligible_waiting_driver(session, polled_order):
            pause_actuality_prompts(session)
            active_polls = []
            break
    if active_polls:
        return
    waiting = (
        session.query(PassengerQueue)
        .filter(PassengerQueue.status == "waiting")
        .order_by(PassengerQueue.position.asc())
        .all()
    )
    for entry in waiting:
        order = session.get(Order, entry.order_id)
        if order is None:
            continue
        from . import parallel_orders
        free_city = parallel_orders.free_line_city(order)
        route_city = parallel_orders.route_priority_city(order)
        line_scope = "normal"
        line_name = None
        has_driver = order_service.has_eligible_waiting_driver(session, order)
        if route_city or free_city:
            if parallel_orders.is_route_fallback(order):
                # The own village line and route driver were already tried.
                # This stage is strictly the Gornozavodsk FIFO line.
                line_scope = "exact"
                line_name = "Горнозаводск"
                has_driver = queue_service.has_waiting_driver(
                    session, line_name, line_scope="exact"
                )
            elif route_city and queue_service.has_waiting_driver(
                session, route_city, line_scope="exact"
            ):
                line_scope = "exact"
                line_name = route_city
                has_driver = True
            else:
                # Before the route-parallel stage only the exact village line
                # has priority; Gornozavodsk is intentionally not considered.
                has_driver = False
        has_parallel = parallel_orders.has_eligible_busy_driver_for_order(session, order)
        if not has_driver and not has_parallel:
            if route_city and not parallel_orders.is_route_fallback(order):
                parallel_orders._fallback_to_free_drivers(
                    session, order, recheck_village=True
                )
                return
            continue

        if has_driver and request_actuality_for_order(
            session,
            order,
            free_driver_available=True,
        ):
            # Ask one passenger at a time so several available drivers do not
            # trigger a burst of confirmation prompts for the whole queue.
            return

        if has_driver:
            order = (
                session.query(Order)
                .filter(Order.id == order.id)
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if (
                not order
                or order.status not in parallel_orders.PARALLEL_CANDIDATE_STATUSES
                or order.driver_id is not None
                or order.parallel_driver_id is not None
            ):
                return
            remove(session, order.id)
            order.status = "searching"
            offered = order_service.offer_to_next_driver(
                session, order, line_scope=line_scope, line_name=line_name
            )
            if offered and not order.dispatcher_id:
                passenger = session.get(User, order.passenger_id)
                if passenger:
                    order.search_notice_outbox_id = vk.send_tracked_message(
                        passenger.vk_id,
                        "🚕 Нашёлся свободный водитель. Передаём ему вашу заявку.",
                        keyboard=kb.passenger_waiting_keyboard(),
                    )
                    set_state(session, passenger.vk_id, States.P_WAITING,
                              {"order_id": order.id}, merge=False)
            # One free driver receives only one ordinary request.
            return
        if has_parallel:
            if not parallel_orders.notify_busy_drivers(session, order):
                parallel_orders._fallback_to_free_drivers(
                    session, order, recheck_village=True
                )
                return


def _recovery_worker() -> None:
    """Recover old waiting orders even when a driver was already online.

    Previously promotion only ran on a status-change event. After a restart or
    a historical status mismatch, a visibly free driver and old waiting orders
    could remain idle forever. This lightweight poll repairs that state.
    """
    from common.database import session_scope

    while True:
        try:
            with session_scope() as session:
                try_promote(session)
        except Exception as exc:  # noqa: BLE001
            log.exception("Passenger queue recovery failed: %s", exc)
        time.sleep(3)


def start_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(
            target=_recovery_worker,
            name="passenger-queue-recovery",
            daemon=True,
        ).start()
        _worker_started = True
        log.info("Passenger queue recovery worker started")


def _poll(session: Session, entry: PassengerQueue) -> None:
    order = session.get(Order, entry.order_id)
    passenger = session.get(User, entry.passenger_id)
    if order is None or passenger is None or order.status != "queued":
        remove(session, entry.order_id)
        return
    if order.dispatcher_id:
        entry.status = "waiting"
        entry.poll_expires_at = None
        return
    timeout = get_int(session, "passenger_poll_timeout", 60)
    entry.status = "polling"
    entry.poll_expires_at = _now() + dt.timedelta(seconds=timeout)
    entry.actuality_prompt_outbox_id = vk.send_tracked_message(
        passenger.vk_id,
        f"Появился водитель, который может взять вашу заявку. Она ещё актуальна?\n"
        f"{order_service.order_text(order)}",
        keyboard=kb.passenger_repoll_keyboard(order.id),
    )
    set_state(session, passenger.vk_id, States.P_QUEUE_CONFIRM, {"order_id": order.id})
    order_id = order.id
    timers.schedule("pqueue", order.id, timeout, lambda: _poll_timeout(order_id))


def request_actuality_for_order(
    session: Session,
    order: Order,
    free_driver_available: bool = False,
) -> bool:
    """Ask after 3+ minutes only when a free driver is available right now."""
    if not free_driver_available or order.dispatcher_id or order.actuality_confirmed:
        return False
    entry = session.query(PassengerQueue).filter(
        PassengerQueue.order_id == order.id
    ).one_or_none()
    if not entry:
        return False
    created_at = entry.created_at or order.created_at or _now()
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=dt.timezone.utc)
    if (_now() - created_at).total_seconds() < 180:
        return False
    if entry.status == "waiting":
        _poll(session, entry)
    return entry.status == "polling"


def confirm(session: Session, user: User, order_id: int | None, actual: bool) -> None:
    """Handle the passenger's answer to «ещё актуальна?»."""
    if order_id is None:
        return
    # Lock in the same order as the timeout callback. A click at the exact
    # deadline therefore has one deterministic winner.
    entry = (
        session.query(PassengerQueue)
        .filter(PassengerQueue.order_id == order_id)
        .with_for_update()
        .one_or_none()
    )
    order = (
        session.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .one_or_none()
    )
    timers.cancel("pqueue", order_id)
    if entry is None or order is None or order.passenger_id != user.id:
        vk.send_message(user.vk_id, "Заявка уже неактуальна.", keyboard=kb.passenger_menu())
        reset(session, user.vk_id, States.MAIN_MENU)
        return
    if order.dispatcher_id:
        entry.status = "waiting"
        entry.poll_expires_at = None
        return

    _clear_actuality_prompt(
        session,
        entry,
        "✅ Вы ответили: заявка актуальна." if actual else "❌ Вы ответили: заявка неактуальна.",
    )
    if not actual:
        remove(session, order_id)
        order.status = "cancelled"
        order.cancelled_at = time_utils.now()
        vk.send_message(user.vk_id, "Свободных водителей пока нет. Попробуйте заказать чуть позже", keyboard=kb.passenger_after_cancel_keyboard())
        reset(session, user.vk_id, States.MAIN_MENU)
        try_promote(session)
        return

    # «Да»: remember this confirmation. The opportunity may have disappeared
    # while the passenger was answering, so re-run the live selector: a free
    # driver wins; if none is free, eligible busy drivers see it as parallel.
    order.actuality_confirmed = True
    pause_actuality_prompts(session, except_order_id=order.id)
    entry.status = "waiting"
    entry.poll_expires_at = None
    set_state(session, user.vk_id, States.P_WAITING,
              {"order_id": order.id}, merge=False)
    vk.send_message(
        user.vk_id, "✅ Заявка актуальна. Передаём её доступному водителю…",
        keyboard=kb.passenger_waiting_keyboard(),
    )
    try_promote(session)


def _poll_timeout(order_id: int) -> None:
    """Runs in a timer thread when the passenger did not answer in time."""
    from common.database import session_scope

    with session_scope() as session:
        entry = (
            session.query(PassengerQueue)
            .filter(PassengerQueue.order_id == order_id)
            .with_for_update()
            .one_or_none()
        )
        if entry is None or entry.status != "polling":
            return
        order = (
            session.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one_or_none()
        )
        passenger = session.get(User, entry.passenger_id) if order else None
        if order is not None and order.dispatcher_id:
            entry.status = "waiting"
            entry.poll_expires_at = None
            return
        remove(session, order_id)
        if order is not None:
            order.status = "cancelled"
            order.cancelled_at = time_utils.now()
        if passenger is not None:
            vk.send_message(
                passenger.vk_id,
                "Вы не подтвердили актуальность заявки, ваша заявка отменена автоматически",
                keyboard=kb.passenger_menu(),
            )
            reset(session, passenger.vk_id, States.MAIN_MENU)
        try_promote(session)


def wait_choice(session: Session, user: User, wait: bool) -> None:
    order = session.query(Order).filter(Order.passenger_id==user.id, Order.status=="queued").order_by(Order.created_at.desc()).first()
    if not order: return
    if wait:
        entry = session.query(PassengerQueue).filter(
            PassengerQueue.order_id == order.id).one_or_none()
        if entry and entry.status == "awaiting_choice":
            entry.status = "waiting"
        elif entry and entry.status == "waiting":
            return vk.send_message(user.vk_id, "Ожидайте свободного водителя.", keyboard=kb.passenger_waiting_keyboard())
        # Only after the passenger explicitly agrees to wait do we publish the
        # call-to-line notice and alert busy drivers about a parallel order.
        order_service.send_driver_chat_notice(
            session, msg(session, "msg_no_free_drivers_chat")
        )
        from . import parallel_orders
        route_offer_created = parallel_orders.notify_busy_drivers(session, order)
        if parallel_orders.route_priority_city(order) and not route_offer_created:
            parallel_orders._fallback_to_free_drivers(
                    session, order, recheck_village=True
                )
        queue_position = position(session, order.id)
        suffix = f" Ваша позиция в очереди: {queue_position}." if queue_position else ""
        vk.send_message(user.vk_id, "Ожидайте свободного водителя." + suffix, keyboard=kb.passenger_waiting_keyboard())
        return
    remove(session, order.id); order.status="cancelled"; order.cancelled_at=time_utils.now()
    vk.send_message(user.vk_id, "Ваша заявка отменена", keyboard=kb.passenger_menu())
    reset(session, user.vk_id, States.MAIN_MENU)
