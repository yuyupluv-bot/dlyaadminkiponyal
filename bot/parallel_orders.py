"""Waiting orders that a busy driver can reserve for the next ride."""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from common import time_utils
from common.models import Order, OutboxMessage, PassengerQueue, User

from . import keyboards as kb, queue_service, timers
from .states_service import States, set_state
from .vk_client import vk

ACTIVE = ("assigned", "arrived", "in_progress")
# A request may briefly remain ``created`` after a restart/dispatch failure or
# use the legacy ``no_drivers`` marker. All three states mean: no driver has
# been assigned, so a busy driver may reserve it as a parallel request.
PARALLEL_CANDIDATE_STATUSES = ("created", "queued", "no_drivers")
ROUTE_FALLBACK_REASON = "route_parallel_fallback"
ROUTE_OFFER_SECONDS = 60
ROUTE_OFFER_TIMEOUT_TEXT = (
    "Вам была предложена параллельная заявка, но вы не ответили в течение минуты. "
    "До завершения текущей поездки новые параллельные заявки не будут приходить "
    "автоматически. Актуальные заявки можно посмотреть и взять вручную в разделе "
    "„🔀 Параллельные заявки“."
)


def is_route_fallback(order: Order) -> bool:
    """Whether the village request has permanently entered its Gorno stage."""
    return bool(
        order.parallel_route_fallback
        or order.last_decline_reason == ROUTE_FALLBACK_REASON
    )


def _json_id_set(raw: str | None) -> set[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    result: set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _exclude_from_auto_offer(order: Order, driver_id: int) -> None:
    excluded = _json_id_set(order.parallel_auto_excluded_driver_ids)
    excluded.add(int(driver_id))
    order.parallel_auto_excluded_driver_ids = json.dumps(sorted(excluded))


def _clear_route_offer_state(order: Order) -> None:
    order.parallel_offer_driver_id = None
    order.parallel_offer_trip_id = None
    order.parallel_offer_outbox_id = None
    order.parallel_notified_driver_ids = None


def _city_first_two(text: str | None) -> str | None:
    words = re.findall(r"[а-яё]+", (text or "").casefold())[:2]
    for word in words:
        if word in ("пашия", "пашии"):
            return "Пашия"
        if word in ("кусья", "кусьи"):
            return "Кусья"
    return None


def _destination_city(text: str | None) -> str | None:
    value = (text or "").casefold()
    if re.search(r"\bдо\s+паши(?:я|и)\b", value):
        return "Пашия"
    if re.search(r"\bдо\s+кусь(?:я|и)\b", value):
        return "Кусья"
    return None


def _origin_city(text: str | None) -> str | None:
    """Recognize an explicit departure from Pashiya or Kusya."""
    value = (text or "").casefold()
    if re.search(r"\bиз\s+паши(?:я|и)\b", value):
        return "Пашия"
    if re.search(r"\bиз\s+кусь(?:я|и)\b", value):
        return "Кусья"
    return None


def route_priority_city(order: Order) -> str | None:
    """Recognize «из города» or a city in the first two words as pickup."""
    text = order.route_text or order.address_from
    return _origin_city(text) or _city_first_two(text)


def free_line_city(order: Order) -> str | None:
    """Line for ordinary free-driver dispatch, strictly from word 1 or 2."""
    return _city_first_two(order.route_text or order.address_from)


def _has_return_intent(text: str | None) -> bool:
    """A destination ride already includes its own return trip."""
    return bool(re.search(
        r"\b(?:с\s+обратом|и\s+обрат|и\s+обратно)\b",
        (text or "").casefold(),
    ))


def _eligible_departed_orders_to_city(session: Session, city: str | None) -> list[Order]:
    if not city:
        return []
    current_orders = session.query(Order).filter(
        Order.status.in_(ACTIVE),
        Order.driver_id.isnot(None),
        Order.driver_departed_at.isnot(None),
    ).all()
    result = []
    for current in current_orders:
        route = current.route_text or current.address_to
        # A village named as the origin means that this is not a trip from
        # Gornozavodsk to that village.  Routes with no explicit village origin
        # are the legacy/local Gornozavodsk form and stay compatible.
        origin = _origin_city(route) or _city_first_two(route)
        if (
            _destination_city(route) == city
            and origin is None
            and not _has_return_intent(route)
        ):
            result.append(current)
    return result


def has_departed_driver_to_city(session: Session, city: str | None) -> bool:
    """Whether a busy driver has already departed toward ``city``.

    Only a real departure (ETA saved) activates this priority. Merely accepting
    an order without pressing a departure/ETA button must not hide return
    requests from free drivers.
    """
    return bool(_eligible_departed_orders_to_city(session, city))


def must_bypass_free_drivers(session: Session, order: Order) -> bool:
    """Return requests from Pashiya/Kusya go straight to parallel orders."""
    return has_departed_driver_to_city(session, route_priority_city(order))


def _destination_restricted_orders(current: Order, orders: list[Order]) -> list[Order]:
    """Apply the route-only parallel rule for Pashiya and Kusya.

    A driver whose current route explicitly ends in Pashiya/Kusya may reserve
    only requests whose first or second word identifies that same city. Other
    destinations keep the normal, unrestricted parallel list.
    """
    destination = _destination_city(current.route_text or current.address_to)
    if not destination:
        return orders
    if _has_return_intent(current.route_text or current.address_to):
        return []
    return [order for order in orders if route_priority_city(order) == destination]


def _free_driver_has_priority(session: Session, order: Order) -> bool:
    """Parallel offers are forbidden while a free driver can handle the order."""
    if order.offered_driver_id or order.offer_outbox_id:
        return True
    from . import order_service
    city = route_priority_city(order)
    if city:
        # Initial village dispatch is strict: own line, then the matching busy
        # route driver.  Only after a decline/timeout does Gornozavodsk become
        # the free-driver destination.
        if is_route_fallback(order):
            return queue_service.has_waiting_driver(
                session, "Горнозаводск", line_scope="exact"
            )
        return queue_service.has_waiting_driver(session, city, line_scope="exact")
    return order_service.has_eligible_waiting_driver(session, order)


def available(session: Session) -> list[Order]:
    """Return every unassigned request eligible for a parallel reservation.

    This deliberately does not depend on PassengerQueue. Passenger and
    dispatcher requests take different creation/recovery paths; a FIFO row is
    only an implementation detail for ordinary free-driver dispatch. A request
    is visible here whenever it has no assigned/parallel driver and is waiting
    in any durable unassigned status.
    """
    rows = (session.query(Order)
            .filter(
                Order.status.in_(PARALLEL_CANDIDATE_STATUSES),
                Order.driver_id.is_(None),
                Order.parallel_driver_id.is_(None),
            )
            .order_by(Order.created_at.asc()).all())
    return [order for order in rows if not _free_driver_has_priority(session, order)]


def _parallel_candidate_filter(query):
    """Apply the same atomic eligibility rules used by ``available``."""
    return query.filter(
        Order.status.in_(PARALLEL_CANDIDATE_STATUSES),
        Order.driver_id.is_(None),
        Order.parallel_driver_id.is_(None),
    )

def has_available_for_current(session: Session, current: Order) -> bool:
    """Whether the active driver's menu should show a green parallel indicator."""
    return bool(_destination_restricted_orders(current, available(session)))


def has_eligible_busy_driver_for_order(session: Session, order: Order) -> bool:
    """Whether a busy driver can reserve this order after free-driver priority."""
    if order.parallel_offer_driver_id:
        return True
    if _free_driver_has_priority(session, order):
        return False
    city = route_priority_city(order)
    if not city or is_route_fallback(order):
        return False
    current_orders = _eligible_departed_orders_to_city(session, city)
    reserved_driver_ids = {
        driver_id for (driver_id,) in session.query(Order.parallel_driver_id).filter(
            Order.status == "parallel_assigned",
            Order.parallel_driver_id.isnot(None),
        ).all()
        if driver_id is not None
    }
    offered_driver_ids = {
        driver_id for (driver_id,) in session.query(Order.parallel_offer_driver_id).filter(
            Order.parallel_offer_driver_id.isnot(None),
        ).all()
        if driver_id is not None
    }
    excluded_driver_ids = _json_id_set(order.parallel_auto_excluded_driver_ids)
    return any(
        current.driver_id not in reserved_driver_ids
        and current.driver_id not in offered_driver_ids
        and current.driver_id not in excluded_driver_ids
        and not current.parallel_auto_offers_disabled
        for current in current_orders
    )


def _update_existing_driver_menu(session: Session, driver: User, keyboard: str) -> None:
    """Replace the keyboard on the latest active-ride message without notifying."""
    # The driver's normal ride keyboard has both commands.  This excludes
    # parallel-list pages, ETA pickers, and unrelated messages.
    rows = session.query(OutboxMessage).filter(
        OutboxMessage.peer_id == driver.vk_id,
        OutboxMessage.keyboard.isnot(None),
        OutboxMessage.status.in_(("pending", "sending", "sent")),
    ).order_by(OutboxMessage.id.desc()).limit(20).all()
    menu = next(
        (row for row in rows if '"cmd":"parallel_orders"' in (row.keyboard or "")
         and '"cmd":"driver_cancel_active"' in (row.keyboard or "")),
        None,
    )
    if not menu:
        return
    # Pending messages have not reached VK yet; changing the stored keyboard is
    # enough. For delivered messages, edit the existing message in place.
    menu.keyboard = keyboard
    if menu.status != "sent":
        return
    marker = menu.last_error or ""
    message_id = marker.split(":", 1)[1] if marker.startswith("vk_message_id:") else ""
    if message_id.isdigit():
        vk.edit_message_keyboard(driver.vk_id, int(message_id), keyboard)


def refresh_busy_driver_menus(session: Session, exclude_driver_ids: set[int] | None = None) -> None:
    """Refresh active-driver parallel indicators without sending a message."""
    excluded = exclude_driver_ids or set()
    active_orders = session.query(Order).filter(
        Order.status.in_(("arrived", "in_progress")),
        Order.driver_id.isnot(None),
    ).all()
    from .handlers import _driver_ride_kb
    for current in active_orders:
        if current.driver_id in excluded:
            continue
        driver = session.get(User, current.driver_id)
        if driver:
            _update_existing_driver_menu(session, driver, _driver_ride_kb(session, current))

def _delete_driver_notices(session: Session, driver_id: int) -> None:
    """Delete the driver's previous aggregate parallel notification."""
    from . import outbox_service

    rows = session.query(Order).filter(
        Order.status.in_(PARALLEL_CANDIDATE_STATUSES),
        Order.parallel_notified_driver_ids.isnot(None),
    ).all()
    for row in rows:
        try:
            stored = json.loads(row.parallel_notified_driver_ids or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(stored, dict):
            continue
        outbox_id = stored.pop(str(driver_id), stored.pop(driver_id, None))
        if outbox_id:
            outbox_service.cancel_or_delete(session, int(outbox_id))
        row.parallel_notified_driver_ids = (
            json.dumps(stored, sort_keys=True) if stored else None
        )


def notify_after_arrival(session: Session, driver: User) -> None:
    """Release the aggregate parallel alert after «Подъехал»."""
    current = session.query(Order).filter(
        Order.driver_id == driver.id,
        Order.status.in_(("arrived", "in_progress")),
    ).first()
    if not current:
        return
    waiting_orders = available(session)
    if waiting_orders:
        notify_busy_drivers(session, waiting_orders[-1])


def _auto_offer_trip_is_eligible(
    session: Session,
    current: Order,
    order: Order,
    city: str,
    excluded_driver_ids: set[int],
) -> bool:
    driver_id = current.driver_id
    if (
        current.status not in ACTIVE
        or not driver_id
        or current.driver_departed_at is None
        or current.parallel_auto_offers_disabled
        or driver_id in excluded_driver_ids
    ):
        return False
    route = current.route_text or current.address_to
    origin = _origin_city(route) or _city_first_two(route)
    if (
        _destination_city(route) != city
        or origin is not None
        or _has_return_intent(route)
    ):
        return False
    if session.query(Order.id).filter(
        Order.parallel_driver_id == driver_id,
        Order.status == "parallel_assigned",
    ).first():
        return False
    if session.query(Order.id).filter(
        Order.id != order.id,
        Order.parallel_offer_driver_id == driver_id,
    ).first():
        return False
    return True


def _claim_auto_offer_trip(session: Session, order: Order) -> Order | None:
    """Lock one matching active trip without blocking another order worker."""
    city = route_priority_city(order)
    if not city:
        return None
    excluded = _json_id_set(order.parallel_auto_excluded_driver_ids)
    candidates = _eligible_departed_orders_to_city(session, city)
    candidates.sort(key=lambda row: (row.driver_departed_at, row.created_at, row.id))
    for candidate in candidates:
        current = (
            session.query(Order)
            .filter(Order.id == candidate.id)
            .with_for_update(skip_locked=True)
            .one_or_none()
        )
        if current and _auto_offer_trip_is_eligible(
            session, current, order, city, excluded
        ):
            return current
    return None


def _route_offer_card_text(session: Session, order: Order) -> str:
    """Build the same useful request context as an ordinary driver offer."""
    from . import order_service

    text = (
        f"🔀 Новая параллельная заявка #{order.id} "
        f"({order_service.order_type_label(order)})\n"
        f"Ваша заявка: {order_service.order_text(order)}"
    )
    passenger = session.get(User, order.passenger_id) if order.passenger_id else None
    if passenger and not order.dispatcher_id:
        name = passenger.full_name or f"id{passenger.vk_id}"
        text += (
            f"\n👤 От кого: [id{passenger.vk_id}|{name}] "
            f"{order_service.passenger_rating_text(passenger)}"
        )
    if order.comment:
        text += f"\n💬 Комментарий: {order.comment}"
    if order.dispatcher_id:
        if order.customer_name:
            text += f"\n👤 Пассажир: {order.customer_name}"
        if order.customer_phone:
            text += f"\n📞 Телефон: {order.customer_phone}"
        text += "\n🎧 Заявку создал диспетчер"
    text += order_service._extras_summary(session, order)
    if order.night_surcharge:
        text += "\n🌙 Применён ночной тариф"
    text += "\n\nНа ответ даётся 60 секунд."
    return text


def notify_busy_drivers(session: Session, order: Order) -> bool:
    """Create one durable 60-second offer for one matching busy driver."""
    if (
        order.parallel_offer_driver_id
        and order.parallel_offer_trip_id
        and order.status in PARALLEL_CANDIDATE_STATUSES
    ):
        return True
    if _free_driver_has_priority(session, order):
        return False
    refresh_busy_driver_menus(session)
    city = route_priority_city(order)
    if not city or is_route_fallback(order):
        return False
    current = _claim_auto_offer_trip(session, order)
    if not current:
        return False
    locked_order = (
        session.query(Order)
        .filter(Order.id == order.id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if not locked_order:
        # Another claimant owns the source-of-truth row.  Treat the request as
        # being handled and let that transaction decide the outcome.
        return True
    order = locked_order
    if (
        order.status not in PARALLEL_CANDIDATE_STATUSES
        or order.driver_id is not None
        or order.parallel_driver_id is not None
    ):
        return True
    if order.parallel_offer_driver_id:
        return True
    driver = session.get(User, current.driver_id)
    if not driver:
        return False
    prepared_voice = vk.prepare_voice_attachment(driver.vk_id, order.voice_attachment)
    if prepared_voice:
        order.voice_attachment = prepared_voice
    outbox_id = vk.send_tracked_message(
        driver.vk_id,
        _route_offer_card_text(session, order),
        keyboard=kb.route_parallel_offer_keyboard(order.id),
        attachment=prepared_voice,
    )
    if not outbox_id:
        return False
    order.parallel_offer_driver_id = driver.id
    order.parallel_offer_trip_id = current.id
    order.parallel_offer_outbox_id = int(outbox_id)
    # Keep the legacy map populated so old cancellation/administration code
    # remains able to find the tracked card during rolling deployments.
    order.parallel_notified_driver_ids = json.dumps(
        {str(driver.id): int(outbox_id)}, sort_keys=True
    )
    timers.schedule(
        "route_parallel_offer", order.id, ROUTE_OFFER_SECONDS,
        lambda: _route_offer_timeout(order.id),
    )
    return True

def _remove_notifications(session: Session, order: Order) -> None:
    """Silently remove every obsolete «new parallel order» notification."""
    from . import outbox_service

    timers.cancel("route_parallel_offer", order.id)
    outbox_ids: set[int] = set()
    if order.parallel_offer_outbox_id:
        outbox_ids.add(int(order.parallel_offer_outbox_id))
    try:
        stored = json.loads(order.parallel_notified_driver_ids or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        stored = {}
    if isinstance(stored, dict):
        for outbox_id in stored.values():
            if outbox_id:
                outbox_ids.add(int(outbox_id))
    for outbox_id in outbox_ids:
        outbox_service.cancel_or_delete(session, outbox_id)
    _clear_route_offer_state(order)


def notify_assigned_to_free_driver(session: Session, order: Order, free_driver: User) -> None:
    """Remove stale alerts and repaint indicators after a free-driver assignment."""
    _remove_notifications(session, order)
    refresh_busy_driver_menus(session)


def show(session: Session, driver: User, current: Order, page=1) -> None:
    reserved = session.query(Order).filter(
        Order.parallel_driver_id == driver.id,
        Order.status == "parallel_assigned",
    ).first()
    if reserved:
        route = reserved.route_text or f"{reserved.address_from} — {reserved.address_to}"
        return vk.send_message(
            driver.vk_id,
            f"У вас уже закреплена параллельная заявка #{reserved.id}:\n{route}",
            keyboard=kb.parallel_reserved_keyboard(reserved.id),
            attachment=reserved.voice_attachment,
        )
    rows = _destination_restricted_orders(current, available(session))
    if not rows:
        from .handlers import _driver_ride_kb
        return vk.send_message(driver.vk_id, "Свободных параллельных заявок пока нет.",
                               keyboard=_driver_ride_kb(session, current))
    # Keep the VK message and keyboard within their limits even with 50+ rows.
    per_page = 8
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    text = [f"🔀 Параллельные заявки: {total} (страница {page}/{total_pages})"]
    choices = []
    for order in page_rows:
        route = order.route_text or f"{order.address_from} — {order.address_to}"
        text.append(f"#{order.id} — {route}")
        choices.append((order.id, route))
    vk.send_message(
        driver.vk_id,
        "\n".join(text),
        keyboard=kb.parallel_orders_keyboard(choices, page=page, total_pages=total_pages),
    )


def take(
    session: Session,
    driver: User,
    order_id: int,
    *,
    require_live_offer: bool = False,
) -> None:
    # Two fast taps arrive as two events in two workers. Locking the driver
    # row first makes the "already has a parallel order" check below
    # reliable instead of racy.
    session.query(User).filter(User.id == driver.id).with_for_update().first()
    current = (
        session.query(Order)
        .filter(Order.driver_id == driver.id, Order.status.in_(ACTIVE))
        .with_for_update()
        .first()
    )
    if not current:
        message = (
            "Это автоматическое предложение уже недоступно. "
            "Актуальные заявки откройте в разделе „🔀 Параллельные заявки“."
            if require_live_offer else "Сначала нужна активная заявка."
        )
        return vk.send_message(driver.vk_id, message)
    existing = session.query(Order).filter(
        Order.parallel_driver_id == driver.id, Order.status == "parallel_assigned").first()
    if existing:
        return vk.send_message(driver.vk_id, f"У вас уже выбрана параллельная заявка #{existing.id}.")
    order = _parallel_candidate_filter(
        session.query(Order).filter(Order.id == int(order_id or 0))
    ).with_for_update().one_or_none()
    if not order:
        message = (
            "Это автоматическое предложение уже недоступно. "
            "Проверьте актуальные заявки в разделе „🔀 Параллельные заявки“."
            if require_live_offer else
            "Эта заявка уже отменена или её взял другой водитель."
        )
        return vk.send_message(driver.vk_id, message)
    if require_live_offer and (
        order.parallel_offer_driver_id != driver.id
        or order.parallel_offer_trip_id != current.id
        or current.parallel_auto_offers_disabled
    ):
        return vk.send_message(
            driver.vk_id,
            "Это автоматическое предложение уже недоступно. "
            "Проверьте актуальные заявки в разделе „🔀 Параллельные заявки“.",
        )
    if _free_driver_has_priority(session, order):
        return vk.send_message(
            driver.vk_id,
            "Заявка передаётся свободному водителю и больше недоступна как параллельная.",
        )
    allowed = _destination_restricted_orders(current, [order])
    if not allowed:
        return vk.send_message(
            driver.vk_id,
            "По текущему маршруту вам доступны только параллельные заявки из города назначения.",
        )
    timers.cancel("route_parallel_offer", order.id)
    order.parallel_driver_id = driver.id
    order.driver_id = driver.id
    order.status = "parallel_assigned"
    queue_service.mark_assigned(session, driver)
    _remove_notifications(session, order)
    session.query(PassengerQueue).filter(PassengerQueue.order_id == order.id).delete()
    # The candidate disappeared for other drivers: immediately repaint their indicators.
    refresh_busy_driver_menus(session, exclude_driver_ids={driver.id})
    timers.schedule("parallel_eta", order.id, 120, lambda: _eta_timeout(order.id))
    passenger = session.get(User, order.passenger_id)
    route = order.route_text or f"{order.address_from} — {order.address_to}"
    passenger_label = (
        f"[id{passenger.vk_id}|{passenger.full_name or ('id' + str(passenger.vk_id))}]"
        if passenger else "Пассажир не указан"
    )
    eta_prompt = (
        f"Пассажир: {passenger_label}\n"
        f"✅ Вы выбрали параллельную заявку #{order.id}.\n"
        f"Ваша заявка: {route}\n\n"
        "Через сколько вы будете у клиента? Выберите вариант или укажите своё время:"
    )
    # Track the exact parallel ETA message so its inline choices disappear
    # after the driver selects a value. Keep the passenger and request details
    # in the final edited card as well.
    order.offer_outbox_id = vk.send_tracked_message(
        driver.vk_id,
        eta_prompt,
        keyboard=kb.parallel_eta_keyboard(order.id),
        attachment=order.voice_attachment,
    )


def take_route_offer(session: Session, driver: User, order_id: int) -> None:
    """Accept only the still-live card tied to this exact active trip."""
    return take(
        session,
        driver,
        order_id,
        require_live_offer=True,
    )


def save_eta(session: Session, driver: User, order_id: int, minutes: int) -> None:
    order = (
        session.query(Order)
        .filter(Order.id == int(order_id))
        .with_for_update()
        .one_or_none()
    )
    if not order or order.parallel_driver_id != driver.id or order.status != "parallel_assigned":
        return vk.send_message(driver.vk_id, "Параллельная заявка недоступна.")
    minutes = max(1, min(600, int(minutes)))
    timers.cancel("parallel_eta", order.id)
    from . import order_service
    passenger = session.get(User, order.passenger_id)
    route = order.route_text or f"{order.address_from} — {order.address_to}"
    passenger_label = (
        f"[id{passenger.vk_id}|{passenger.full_name or ('id' + str(passenger.vk_id))}]"
        if passenger else "Пассажир не указан"
    )
    # The tracked card already contains the passenger and route lines.
    # Only the chosen time is appended, otherwise the whole block is
    # printed twice in the driver's chat.
    order_service.finalize_offer_message(
        session,
        order,
        f"✅ Выбрано время прибытия: {minutes} мин.",
    )
    order.parallel_eta = minutes
    order.parallel_eta_set_at = time_utils.now()
    if passenger:
        name = driver.full_name or f"id{driver.vk_id}"
        if order.dispatcher_id:
            vk.send_message(passenger.vk_id,
                            f"✅ Для заявки #{order.id} назначен водитель {name}, авто: {driver.car_full}. Водитель завершает текущую поездку и будет у клиента ориентировочно через {minutes} мин.")
        else:
            vk.send_message(passenger.vk_id,
                            f"🚗 Водитель: {name}\nАвто: {driver.car_full}\n"
                            f"Водитель завершает текущую поездку и будет у вас ориентировочно через {minutes} мин.")
    current = session.query(Order).filter(Order.driver_id == driver.id,
              Order.status.in_(ACTIVE)).first()
    if current:
        set_state(session, driver.vk_id, States.D_IN_RIDE, {"order_id": current.id})
        from .handlers import _driver_ride_kb
        vk.send_message(driver.vk_id,
                        f"Параллельная заявка #{order.id} закреплена за вами. Сначала завершите текущую.",
                        keyboard=_driver_ride_kb(session, current))


def add_eta(session: Session, driver: User, order_id: int, minutes: int) -> None:
    """Extend the promised pickup time for one reserved parallel request."""
    order = session.get(Order, int(order_id or 0))
    if (
        not order
        or order.parallel_driver_id != driver.id
        or order.status != "parallel_assigned"
        or not order.parallel_eta
    ):
        return vk.send_message(driver.vk_id, "Параллельная заявка недоступна.")
    if minutes < 1 or minutes > 600:
        return vk.send_message(driver.vk_id, "Укажите целое количество минут от 1 до 600.")

    order.parallel_eta = int(order.parallel_eta) + int(minutes)
    passenger = session.get(User, order.passenger_id)
    if passenger:
        vk.send_message(
            passenger.vk_id,
            f"⏳ Водитель задерживается. Нужно подождать ещё {minutes} мин.",
        )

    current = session.query(Order).filter(
        Order.driver_id == driver.id,
        Order.status.in_(ACTIVE),
    ).first()
    if current:
        set_state(session, driver.vk_id, States.D_IN_RIDE, {"order_id": current.id})
    vk.send_message(
        driver.vk_id,
        f"✅ К времени подачи добавлено {minutes} мин. "
        f"Общее указанное время: {order.parallel_eta} мин.",
        keyboard=kb.parallel_reserved_keyboard(order.id),
    )


def _restore_active_menu_after_parallel_decline(
    session: Session, driver: User, current: Order | None = None
) -> None:
    """Return the full active-ride menu after every parallel refusal path."""
    if current is None:
        current = session.query(Order).filter(
            Order.driver_id == driver.id,
            Order.status.in_(ACTIVE),
        ).first()
    if not current:
        return
    set_state(session, driver.vk_id, States.D_IN_RIDE, {"order_id": current.id})
    # Build the canonical menu so waiting, payment, ETA and parallel indicators
    # are all restored instead of sending a reduced keyboard.
    from .handlers import _driver_ride_kb
    vk.send_message(
        driver.vk_id,
        "Параллельная заявка отклонена. Возвращаемся к активной заявке.",
        keyboard=_driver_ride_kb(session, current),
    )


def decline(session: Session, driver: User, order_id: int) -> None:
    order = session.get(Order, int(order_id or 0))
    if not order or order.parallel_driver_id != driver.id or order.status != "parallel_assigned":
        return vk.send_message(driver.vk_id, "Параллельная заявка уже недоступна.")
    timers.cancel("parallel_eta", order.id)
    from . import order_service
    order_service.finalize_offer_message(
        session, order, "❌ Вы отказались от параллельной заявки."
    )
    city = route_priority_city(order)
    current = session.query(Order).filter(
        Order.driver_id == driver.id,
        Order.status.in_(ACTIVE),
    ).first()
    if city and current and _destination_city(current.route_text or current.address_to) == city:
        order.parallel_driver_id = None
        order.driver_id = None
        order.parallel_eta = None
        order.parallel_eta_set_at = None
        order.status = "queued"
        _fallback_to_free_drivers(session, order, driver)
        _restore_active_menu_after_parallel_decline(session, driver, current)
        return
    _release(session, order, driver, "Водитель отказался от параллельной заявки. Продолжаем поиск.")


def decline_route_offer(session: Session, driver: User, order_id: int) -> None:
    """Decline only a live route offer; future offers for this trip stay on."""
    session.query(User).filter(User.id == driver.id).with_for_update().first()
    snapshot = session.get(Order, int(order_id or 0))
    trip_id = snapshot.parallel_offer_trip_id if snapshot else None
    current = (
        session.query(Order)
        .filter(Order.id == trip_id)
        .with_for_update()
        .one_or_none()
        if trip_id else None
    )
    order = _parallel_candidate_filter(
        session.query(Order).filter(Order.id == int(order_id or 0))
    ).with_for_update().one_or_none()
    if (
        not order
        or not current
        or order.parallel_offer_driver_id != driver.id
        or order.parallel_offer_trip_id != current.id
        or current.driver_id != driver.id
        or current.status not in ACTIVE
    ):
        return vk.send_message(driver.vk_id, "Параллельная заявка уже недоступна.")
    city = route_priority_city(order)
    if (
        not city
        or _destination_city(current.route_text or current.address_to) != city
        or _has_return_intent(current.route_text or current.address_to)
    ):
        return vk.send_message(driver.vk_id, "Эта параллельная заявка вам недоступна.")
    _exclude_from_auto_offer(order, driver.id)
    _finalize_route_offer_card(
        session,
        order,
        "Вы отказались от параллельной заявки.",
        replace_text=True,
    )
    _fallback_to_free_drivers(
        session, order, driver, offer_already_closed=True
    )
    _restore_active_menu_after_parallel_decline(session, driver, current)


def _route_offer_outbox_id(order: Order) -> int | None:
    if order.parallel_offer_outbox_id:
        return int(order.parallel_offer_outbox_id)
    try:
        stored = json.loads(order.parallel_notified_driver_ids or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(stored, dict):
        for value in stored.values():
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _finalize_route_offer_card(
    session: Session,
    order: Order,
    text: str,
    *,
    replace_text: bool = False,
) -> None:
    """Persistently remove buttons; business state never depends on VK edit."""
    from . import outbox_service

    timers.cancel("route_parallel_offer", order.id)
    outbox_id = _route_offer_outbox_id(order)
    if outbox_id:
        outbox_service.finalize_tracked_message(
            session,
            outbox_id,
            text,
            attachment=order.voice_attachment,
            replace_text=replace_text,
        )
    _clear_route_offer_state(order)


def cancel_route_offer(
    session: Session,
    order: Order,
    text: str = "Параллельная заявка больше недоступна.",
) -> bool:
    """Close a pending card when another workflow cancels or claims its order."""
    if not (
        order.parallel_offer_driver_id
        or order.parallel_offer_outbox_id
        or order.parallel_notified_driver_ids
    ):
        return False
    _finalize_route_offer_card(session, order, text, replace_text=True)
    return True


def _fallback_to_free_drivers(
    session: Session,
    order: Order,
    driver: User | None = None,
    *,
    offer_already_closed: bool = False,
    recheck_village: bool = False,
) -> None:
    """After a parallel decline, immediately hand the order to a free driver.

    The own village line was already checked before the automatic offer.  This
    second stage is therefore strictly the FIFO Gornozavodsk line.  If nobody
    is free, the durable passenger-queue row is kept for later promotion.
    """
    from . import order_service, passenger_queue

    locked_order = (
        session.query(Order)
        .filter(Order.id == order.id)
        .with_for_update()
        .one_or_none()
    )
    if (
        not locked_order
        or locked_order.status not in PARALLEL_CANDIDATE_STATUSES
        or locked_order.driver_id is not None
        or locked_order.parallel_driver_id is not None
    ):
        return
    order = locked_order
    timers.cancel("route_parallel_offer", order.id)
    if offer_already_closed:
        _clear_route_offer_state(order)
    else:
        _remove_notifications(session, order)
    order.parallel_driver_id = None
    order.driver_id = None
    order.parallel_eta = None
    order.parallel_eta_set_at = None
    order.status = "queued"
    entry = passenger_queue.enqueue(session, order)
    entry.status = "waiting"

    # Initial dispatch may race with a driver joining the village line. Give
    # that exact FIFO line one final chance before entering the irreversible
    # post-offer Gornozavodsk stage. Explicit decline/timeout callers leave
    # this disabled, as required.
    city = route_priority_city(order)
    if (
        recheck_village
        and city
        and not is_route_fallback(order)
        and queue_service.has_waiting_driver(session, city, line_scope="exact")
    ):
        passenger_queue.remove(session, order.id)
        order.status = "searching"
        order_service.offer_to_next_driver(
            session, order, line_scope="exact", line_name=city
        )
        refresh_busy_driver_menus(
            session, exclude_driver_ids={driver.id} if driver else None
        )
        return

    order.parallel_route_fallback = True
    # This is a system routing transition, not a refusal by a driver.  Keep
    # its durable flag separate from the user-facing refusal history.  Clear
    # only the legacy technical marker; real refusal reasons remain visible.
    if order.last_decline_reason == ROUTE_FALLBACK_REASON:
        order.last_decline_reason = None

    if not queue_service.has_waiting_driver(
        session, "Горнозаводск", line_scope="exact"
    ):
        refresh_busy_driver_menus(
            session, exclude_driver_ids={driver.id} if driver else None
        )
        return

    passenger_queue.remove(session, order.id)
    order.status = "searching"
    order_service.offer_to_next_driver(
        session,
        order,
        line_scope="exact",
        line_name="Горнозаводск",
    )
    refresh_busy_driver_menus(session, exclude_driver_ids={driver.id} if driver else None)


def _route_offer_timeout(order_id: int) -> None:
    """Expire exactly one still-live offer and suppress only its active trip."""
    from common.database import session_scope

    with session_scope() as session:
        snapshot = session.get(Order, order_id)
        if not snapshot:
            return
        driver_id = snapshot.parallel_offer_driver_id
        trip_id = snapshot.parallel_offer_trip_id
        if not driver_id or not trip_id:
            # Rolling-upgrade compatibility: old v82 offers stored only a JSON
            # outbox map. Close them safely and continue with Gornozavodsk,
            # without applying a trip restriction we cannot identify.
            if (
                snapshot.status in PARALLEL_CANDIDATE_STATUSES
                and snapshot.parallel_notified_driver_ids
                and snapshot.driver_id is None
                and snapshot.parallel_driver_id is None
            ):
                _fallback_to_free_drivers(session, snapshot)
            return
        session.query(User).filter(User.id == driver_id).with_for_update().first()
        current = (
            session.query(Order)
            .filter(Order.id == trip_id)
            .with_for_update()
            .one_or_none()
        )
        order = (
            session.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            not order
            or order.parallel_offer_driver_id != driver_id
            or order.parallel_offer_trip_id != trip_id
        ):
            return
        if (
            order.status not in PARALLEL_CANDIDATE_STATUSES
            or order.driver_id is not None
            or order.parallel_driver_id is not None
        ):
            _finalize_route_offer_card(
                session, order, "Параллельная заявка уже недоступна.",
                replace_text=True,
            )
            return
        still_same_active_trip = bool(
            current
            and current.driver_id == driver_id
            and current.status in ACTIVE
        )
        if still_same_active_trip:
            current.parallel_auto_offers_disabled = True
            final_text = ROUTE_OFFER_TIMEOUT_TEXT
        else:
            final_text = (
                "Автоматическое предложение закрыто, потому что текущая поездка "
                "уже завершена. Актуальные заявки доступны в разделе "
                "„🔀 Параллельные заявки“."
            )
        _exclude_from_auto_offer(order, driver_id)
        _finalize_route_offer_card(
            session, order, final_text, replace_text=True
        )
        _fallback_to_free_drivers(
            session, order, offer_already_closed=True
        )
        from . import passenger_queue
        passenger_queue.try_promote(session)


def release_route_offers_for_trip(session: Session, current: Order) -> int:
    """Close offers tied to a trip that ended before their one-minute timer."""
    if not current.id:
        return 0
    if current.driver_id:
        session.query(User).filter(
            User.id == current.driver_id
        ).with_for_update().first()
    session.query(Order).filter(Order.id == current.id).with_for_update().first()
    rows = (
        session.query(Order)
        .filter(
            Order.parallel_offer_trip_id == current.id,
            Order.parallel_offer_driver_id.isnot(None),
        )
        .order_by(Order.id.asc())
        .with_for_update()
        .all()
    )
    released = 0
    for order in rows:
        if order.status not in PARALLEL_CANDIDATE_STATUSES:
            _finalize_route_offer_card(
                session, order, "Параллельная заявка уже недоступна.",
                replace_text=True,
            )
            continue
        _finalize_route_offer_card(
            session,
            order,
            "Автоматическое предложение закрыто: текущая поездка завершена. "
            "Актуальные заявки доступны в разделе „🔀 Параллельные заявки“.",
            replace_text=True,
        )
        _fallback_to_free_drivers(
            session, order, offer_already_closed=True
        )
        released += 1
    return released


def reconcile_route_offers() -> int:
    """Repair legacy or orphaned live offers after a process restart.

    Normal v83 offers already have a pending ``ScheduledJob`` and are restored
    by ``timers.restore_persistent``.  This guard only handles rolling-upgrade
    v82 cards (JSON map without trip identity), stale terminal rows, and the
    defensive case where live offer state exists without its durable timer.
    """
    from common.database import session_scope
    from common.models import ScheduledJob

    orphan_ids: list[int] = []
    repaired = 0
    with session_scope() as session:
        legacy = session.query(Order).filter(
            Order.status.in_(PARALLEL_CANDIDATE_STATUSES),
            Order.parallel_notified_driver_ids.isnot(None),
            Order.parallel_offer_driver_id.is_(None),
        ).all()
        for order in legacy:
            if route_priority_city(order):
                _fallback_to_free_drivers(session, order)
                repaired += 1

        live = session.query(Order).filter(
            Order.parallel_offer_driver_id.isnot(None),
        ).all()
        for order in live:
            if (
                order.status not in PARALLEL_CANDIDATE_STATUSES
                or order.driver_id is not None
                or order.parallel_driver_id is not None
            ):
                cancel_route_offer(session, order)
                repaired += 1
                continue
            job = session.query(ScheduledJob.id).filter(
                ScheduledJob.job_key == f"route_parallel_offer:{order.id}",
                ScheduledJob.status == "pending",
            ).first()
            if not job:
                orphan_ids.append(order.id)

    # Run callbacks after the reconciliation transaction commits.  Each one
    # re-locks and revalidates its rows, so a simultaneous button press still
    # has one deterministic winner.
    for order_id in orphan_ids:
        _route_offer_timeout(order_id)
        repaired += 1
    return repaired


def _release(session: Session, order: Order, driver: User | None, passenger_text: str) -> None:
    from . import passenger_queue

    order.parallel_driver_id = None
    order.parallel_eta = None
    order.parallel_eta_set_at = None
    order.driver_id = None
    order.status = "queued"
    entry = passenger_queue.enqueue(session, order)
    entry.status = "waiting"
    passenger = session.get(User, order.passenger_id)
    if passenger and not order.dispatcher_id:
        vk.send_message(passenger.vk_id, passenger_text, keyboard=kb.passenger_waiting_keyboard())
    if driver:
        current = session.query(Order).filter(
            Order.driver_id == driver.id, Order.status.in_(ACTIVE)).first()
        if current:
            _restore_active_menu_after_parallel_decline(session, driver, current)
    passenger_queue.try_promote(session)
    refresh_busy_driver_menus(session)


def release_reserved(session: Session, driver: User, passenger_text: str = "Водитель больше не может выполнить параллельную заявку. Продолжаем поиск.") -> bool:
    order = session.query(Order).filter(
        Order.parallel_driver_id == driver.id,
        Order.status == "parallel_assigned",
    ).first()
    if not order:
        return False
    timers.cancel("parallel_eta", order.id)
    _release(session, order, None, passenger_text)
    return True


def _eta_timeout(order_id: int) -> None:
    from common.database import session_scope

    with session_scope() as session:
        order = (
            session.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .one_or_none()
        )
        if not order or order.status != "parallel_assigned" or order.parallel_eta:
            return
        driver = session.get(User, order.parallel_driver_id) if order.parallel_driver_id else None
        from . import order_service
        order_service.finalize_offer_message(
            session, order, "⌛ Время выбора прибытия истекло."
        )
        _release(session, order, driver,
                 "Водитель не указал время прибытия. Параллельная заявка снова ищет водителя.")


def promote_after_current(session: Session, driver: User) -> Order | None:
    order = (session.query(Order).filter(Order.parallel_driver_id == driver.id,
             Order.status == "parallel_assigned").order_by(Order.created_at.asc())
             .with_for_update().first())
    if not order:
        return None
    promised = int(order.parallel_eta or 0)
    elapsed = 0
    if order.parallel_eta_set_at:
        started = order.parallel_eta_set_at
        if started.tzinfo is None:
            import datetime as dt
            started = started.replace(tzinfo=dt.timezone.utc)
        elapsed = max(0, int((time_utils.now() - started).total_seconds() // 60))
    remaining = max(0, promised - elapsed)
    # Convert the reserved parallel assignment into one ordinary active ride.
    # Clearing the parallel-only fields is important: all arrival, finish and
    # price handlers resolve active work through driver_id.
    order.status = "assigned"
    order.driver_id = driver.id
    order.parallel_driver_id = None
    order.arrival_eta = remaining
    order.parallel_eta = None
    order.parallel_eta_set_at = None
    order.driver_accept_time = time_utils.now()
    order.driver_departed_at = time_utils.now()
    set_state(session, driver.vk_id, States.D_IN_RIDE, {"order_id": order.id})
    from . import order_service
    order_service.schedule_prearrival_notice(session, order)
    timing = (
        f"\n⏱ У вас на прибытие осталось: {remaining} мин."
        "\n(Вы можете добавить время прибытия по кнопке в меню.)"
    ) if promised else ""
    from .handlers import _driver_ride_kb
    vk.send_message(driver.vk_id,
                    f"Переходим к заявке #{order.id}:\n"
                    f"{order.route_text or order.address_to}{timing}",
                    keyboard=_driver_ride_kb(session, order))
    passenger = session.get(User, order.passenger_id)
    # A dispatcher has already received assignment, driver/car and ETA details.
    # Do not send a separate "driver is free" notification on transition.
    if passenger and not order.dispatcher_id:
        vk.send_message(passenger.vk_id, "🚕 Водитель освободился и теперь выезжает к вам.",
                        keyboard=kb.passenger_ride_keyboard())
    return order

def has_pending(session: Session, driver: User) -> bool:
    return session.query(Order.id).filter(
        Order.parallel_driver_id == driver.id,
        Order.status == "parallel_assigned",
    ).first() is not None
