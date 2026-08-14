"""Atomic cancellation initiated from the web admin panel.

The web UI must cancel dispatch side effects, not only change orders.status.
Otherwise already queued VK outbox cards can still reach drivers after the
administrator has clicked «Отменить».
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets

from sqlalchemy.orm import Session

from common import time_utils
from common.models import (
    ROLE_DISPATCHER,
    DriverQueue,
    Order,
    OutboxMessage,
    PassengerQueue,
    ScheduledJob,
    User,
)

from . import keyboards as kb
from . import outbox_service
from .states_service import States, reset

_ORDER_ACTION_COMMANDS = {
    "accept", "decline", "decline_reason", "pending_take",
    "chat_take", "chat_no_driver", "parallel_take",
    "parallel_route_decline", "queue_yes", "queue_no",
    "chat_order_actual_yes", "chat_order_actual_no",
    "departure_wait", "departure_cancel",
}


def _payloads(keyboard: str | None):
    if not keyboard:
        return
    try:
        data = json.loads(keyboard)
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    for row in data.get("buttons") or []:
        for button in row or []:
            action = button.get("action") if isinstance(button, dict) else None
            payload = action.get("payload") if isinstance(action, dict) else None
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = None
            if isinstance(payload, dict):
                yield payload


def _belongs_to_order(row: OutboxMessage, order_id: int) -> bool:
    for payload in _payloads(row.keyboard):
        try:
            payload_order_id = int(payload.get("order_id"))
        except (TypeError, ValueError):
            continue
        if payload_order_id == order_id and payload.get("cmd") in _ORDER_ACTION_COMMANDS:
            return True
    # Compatibility for historical ordinary cards whose outbox id was not
    # stored on the order row.
    return bool(
        (row.text or "").startswith("🔔 Новая заявка #")
        and re.search(rf"#\s*{order_id}(?!\d)", row.text or "")
    )


def _enqueue(session: Session, peer_id: int, text: str, keyboard: str | None = None) -> None:
    if not peer_id:
        return
    session.add(OutboxMessage(
        peer_id=peer_id,
        text=text[:4000],
        keyboard=keyboard,
        random_id=secrets.randbits(62) or 1,
        status="pending",
        priority=1000,
        attempts=0,
        next_attempt_at=time_utils.now(),
    ))


def cancel_from_admin(session: Session, order: Order, reason: str = "") -> dict:
    """Cancel one order and every queued/sent dispatch artifact atomically."""
    # The web route may have loaded this object before a bot transaction
    # changed it. Lock and refresh the row before making any decision.
    order = (
        session.query(Order)
        .filter(Order.id == order.id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if order is None:
        return {"changed": False, "cancelled_messages": 0}
    if order.status in ("completed", "cancelled"):
        return {"changed": False, "cancelled_messages": 0}

    previous_status = order.status
    offered = session.get(User, order.offered_driver_id) if order.offered_driver_id else None
    assigned = session.get(User, order.driver_id) if order.driver_id else None
    parallel = session.get(User, order.parallel_driver_id) if order.parallel_driver_id else None
    passenger = session.get(User, order.passenger_id) if order.passenger_id else None
    from . import parallel_orders

    # A route offer is scoped to this exact active trip. Close and redistribute
    # it before freeing the driver instead of leaving the card alive for up to
    # another minute after an administrative cancellation.
    parallel_orders.release_route_offers_for_trip(session, order)

    # Status is changed first: bot timer/recovery callbacks now fail their live
    # status checks even if they wake up during this transaction.
    order.status = "cancelled"
    order.cancelled_at = time_utils.now()
    order.cancelled_by = "admin"

    # Stop persistent work. In-memory callbacks in the bot are also harmless
    # because they re-read the now-cancelled status before dispatching.
    session.query(ScheduledJob).filter(ScheduledJob.object_id == order.id).delete(
        synchronize_session=False
    )
    session.query(PassengerQueue).filter(PassengerQueue.order_id == order.id).delete(
        synchronize_session=False
    )

    # Release a driver without invoking live VK/network hooks from the web
    # process. The existing queue position is preserved because administrative
    # cancellation is not the driver's fault.
    def restore_driver(driver: User | None) -> None:
        if not driver:
            return
        entry = session.query(DriverQueue).filter(
            DriverQueue.driver_id == driver.id
        ).one_or_none()
        if entry and driver.is_on_line:
            entry.status = "waiting"
            driver.driver_status = "online"
        elif not entry:
            driver.driver_status = "offline"
        reset(session, driver.vk_id, States.D_MENU)

    promoted_order = None
    restore_driver(offered)
    if assigned and assigned.id != (offered.id if offered else None):
        if previous_status in ("assigned", "arrived", "in_progress"):
            if parallel_orders.has_pending(session, assigned):
                promoted_order = parallel_orders.promote_after_current(session, assigned)
            else:
                restore_driver(assigned)
    order.offered_driver_id = None
    order.parallel_driver_id = None

    # Cancel every explicitly tracked card plus historical cards discovered by
    # their payload. This covers pending, sending and already delivered rows;
    # cancel_or_delete turns a race with the worker into cancel_requested.
    tracked_ids = {
        getattr(order, name, None)
        for name in (
            "offer_outbox_id", "departure_prompt_outbox_id",
            "chat_notice_outbox_id", "chat_actuality_prompt_outbox_id",
            "passenger_rating_prompt_outbox_id", "driver_rating_prompt_outbox_id",
            "parallel_offer_outbox_id",
        )
    }
    try:
        stored = json.loads(order.parallel_notified_driver_ids or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        stored = {}
    if isinstance(stored, dict):
        tracked_ids.update(stored.values())
    order.parallel_notified_driver_ids = None
    order.parallel_offer_driver_id = None
    order.parallel_offer_trip_id = None
    order.parallel_offer_outbox_id = None

    candidate_cutoff = (order.created_at or time_utils.now()) - dt.timedelta(minutes=5)
    candidates = session.query(OutboxMessage).filter(
        OutboxMessage.created_at >= candidate_cutoff,
        OutboxMessage.status.in_((
            "pending", "failed", "sending", "sent", "cancel_requested",
            "delete_requested", "finalize_requested", "finalizing",
        )),
    ).all()
    tracked_ids.update(row.id for row in candidates if _belongs_to_order(row, order.id))
    cancelled_messages = 0
    for outbox_id in {int(value) for value in tracked_ids if value}:
        if outbox_service.cancel_or_delete(session, outbox_id):
            cancelled_messages += 1

    for name in (
        "offer_outbox_id", "departure_prompt_outbox_id", "chat_notice_outbox_id",
        "chat_actuality_prompt_outbox_id", "passenger_rating_prompt_outbox_id",
        "driver_rating_prompt_outbox_id",
    ):
        if hasattr(order, name):
            setattr(order, name, None)

    # Replace stale controls with clear final notifications through the same
    # transactional outbox consumed by the VK bot application.
    suffix = f" Причина: {reason}." if reason else ""
    if passenger:
        passenger_keyboard = (
            kb.dispatcher_menu(False)
            if passenger.role == ROLE_DISPATCHER
            else kb.passenger_menu(False)
        )
        reset(
            session,
            passenger.vk_id,
            States.DISP_MENU if passenger.role == ROLE_DISPATCHER else States.MAIN_MENU,
        )
        _enqueue(
            session,
            passenger.vk_id,
            f"❌ Ваша заявка #{order.id} отменена администратором.{suffix}",
            passenger_keyboard,
        )

    notified_driver_ids: set[int] = set()
    for driver in (offered, assigned, parallel):
        if not driver or driver.id in notified_driver_ids:
            continue
        notified_driver_ids.add(driver.id)
        driver_keyboard = (
            kb.driver_ride_keyboard(
                "assigned",
                eta_set=True,
                driver_gender=driver.driver_gender,
            )
            if promoted_order is not None and driver.id == assigned.id
            else kb.driver_menu(on_line=bool(driver.is_on_line))
        )
        _enqueue(
            session,
            driver.vk_id,
            f"❌ Заявка #{order.id} отменена администратором.{suffix}",
            driver_keyboard,
        )

    return {
        "changed": True,
        "cancelled_messages": cancelled_messages,
        "previous_status": previous_status,
    }
