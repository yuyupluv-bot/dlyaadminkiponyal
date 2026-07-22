"""Flask admin application factory + all routes.

Run locally:
    flask --app web.app run --debug
Or via gunicorn (bothost.ru / any VPS):
    gunicorn web.app:app
On Vercel it is imported by api/index.py as a serverless WSGI handler.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import sys

# Ensure the project root is importable when the app is launched directly
# (e.g. `python web/app.py` or `gunicorn web.app:app` from inside web/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from flask import (  # noqa: E402
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import joinedload

from common.config import config
from common.database import get_session
from common.logger import get_logger
from common.models import (
    AdminLog,
    AdminUser,
    BotMessage,
    LoginAttempt,
    BlockedUser,
    City,
    DriverQueue,
    FakeCall,
    Order,
    Promocode,
    Promotion,
    Review,
    User,
)
from common import price_service as ps
from common import bot_messages_service as bm
from common import time_utils
from common.settings_service import DEFAULTS, active_text_keys, ensure_defaults, get_all_settings, get_setting, set_setting

from . import broadcast
from .auth import login_required, verify_admin
from .excel_export import build_stats_workbook

log = get_logger("web.app")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_COOKIE_SECURE", "1") != "0",
)

# Keep the DB schema in sync (auto-applies any pending Alembic migrations).
try:
    from common.db_migrate import ensure_schema  # noqa: E402
    ensure_schema()
except Exception:  # noqa: BLE001 - never block app import on migration issues
    pass


# --------------------------------------------------------------------------- #
#  Request-scoped DB session                                                   #
# --------------------------------------------------------------------------- #
@app.before_request
def _open_session() -> None:
    request.db = get_session()  # type: ignore[attr-defined]


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def _security_context():
    return {"csrf_token": _csrf_token}


@app.before_request
def _protect_post_requests():
    if request.method != "POST":
        return None
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(str(expected), str(supplied)):
        abort(400, "Недействительный CSRF-токен")
    return None


@app.teardown_request
def _close_session(exc) -> None:
    db = getattr(request, "db", None)
    if db is not None:
        if exc is not None:
            db.rollback()
        db.close()


@app.get("/health")
def health():
    """BotHost health check: verifies both HTTP and PostgreSQL."""
    try:
        request.db.execute(text("SELECT 1"))  # type: ignore[attr-defined]
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "database_unavailable"}, 503


def db():
    return request.db  # type: ignore[attr-defined]


@app.route("/healthz")
def healthz():
    """Unauthenticated deployment check for Vercel/Bothost diagnostics."""
    try:
        db().execute(text("SELECT 1"))
        return jsonify({"ok": True, "service": "taxi-admin"})
    except Exception as exc:  # noqa: BLE001
        log.error("Admin health check failed: %s", exc)
        return jsonify({"ok": False, "error": "database unavailable"}), 503


def log_action(action: str, details: str = "") -> None:
    admin_id = session.get("admin_id")
    db().add(AdminLog(admin_id=admin_id, action=action, details=details))
    db().commit()


# --------------------------------------------------------------------------- #
#  Auth                                                                        #
# --------------------------------------------------------------------------- #
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_value = request.form["login"].strip()
        ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "") or "").split(",")[0].strip()[:64]
        cutoff = time_utils.now() - dt.timedelta(minutes=15)
        try:
            failures = db().query(func.count(LoginAttempt.id)).filter(
                LoginAttempt.ip_address == ip,
                LoginAttempt.login == login_value,
                LoginAttempt.success.is_(False),
                LoginAttempt.created_at >= cutoff,
            ).scalar() or 0
        except ProgrammingError:
            # A serverless instance can briefly receive a request while its
            # deployment migration is still catching up. Create just this
            # security table and retry instead of returning a 500 login page.
            db().rollback()
            db().execute(text("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id SERIAL PRIMARY KEY, ip_address VARCHAR(64) NOT NULL,
                    login VARCHAR(120) NOT NULL,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            db().execute(text("""
                CREATE INDEX IF NOT EXISTS ix_login_attempts_lookup
                ON login_attempts(ip_address, login, created_at DESC)
            """))
            db().commit()
            failures = 0
        if failures >= 5:
            return render_template("login.html", blocked=True), 429
        admin = verify_admin(db(), login_value, request.form["password"])
        db().add(LoginAttempt(ip_address=ip, login=login_value, success=bool(admin)))
        db().commit()
        if admin:
            session["admin_id"] = admin.id
            session["admin_login"] = admin.login
            log_action("login", f"admin {admin.login} вошёл")
            return redirect(url_for("dashboard"))
        flash("Неверный логин или пароль", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- #
#  Dashboard                                                                   #
# --------------------------------------------------------------------------- #
@app.route("/")
@login_required
def dashboard():
    s = db()
    status_counts = dict(s.query(Order.status, func.count(Order.id)).group_by(Order.status).all())
    week_start = time_utils.now() - dt.timedelta(days=7)
    orders_7_days = s.query(func.count(Order.id)).filter(Order.created_at >= week_start).scalar() or 0
    total_users, total_drivers = s.query(
        func.count(User.id),
        func.count(User.id).filter(User.granted_roles.like("%driver%")),
    ).one()
    online_drivers = s.query(func.count(DriverQueue.id)).filter(DriverQueue.status == "waiting").scalar() or 0
    stats = {
        "total_users": total_users or 0,
        "total_drivers": total_drivers or 0,
        "online_drivers": online_drivers,
        "new_orders": sum(status_counts.get(x, 0) for x in ("created", "queued")),
        "waiting_driver": sum(status_counts.get(x, 0) for x in ("searching", "chat_search", "no_drivers")),
        "with_driver": sum(status_counts.get(x, 0) for x in ("assigned", "arrived", "in_progress")),
        "orders_7_days": orders_7_days,
    }
    return render_template("dashboard.html", stats=stats)


@app.route("/api/new-orders-count")
@login_required
def new_orders_count():
    """Lightweight endpoint polled by the dashboard for new-order notifications."""
    cnt = db().query(func.count(Order.id)).filter(
        Order.status.in_(["created", "searching"])
    ).scalar()
    return jsonify({"pending": cnt or 0})


# --------------------------------------------------------------------------- #
#  Users                                                                       #
# --------------------------------------------------------------------------- #
@app.route("/users")
@login_required
def users():
    s = db()
    role = request.args.get("role", "")
    q = request.args.get("q", "").strip()
    query = s.query(User)
    if role:
        query = query.filter(User.role == role)
    if q:
        query = query.filter(
            (User.full_name.ilike(f"%{q}%")) | (User.phone.ilike(f"%{q}%"))
        )
    users_list = query.order_by(User.created_at.desc()).limit(200).all()
    return render_template("users.html", users=users_list, role=role, q=q)


@app.route("/users/<int:user_id>/toggle-block", methods=["POST"])
@login_required
def toggle_block(user_id):
    s = db()
    user = s.get(User, user_id)
    if not user:
        abort(404)
    user.is_blocked = not user.is_blocked
    if user.is_blocked:
        if not s.query(BlockedUser).filter(BlockedUser.vk_id == user.vk_id).one_or_none():
            s.add(BlockedUser(vk_id=user.vk_id, reason="Заблокирован администратором"))
    else:
        s.query(BlockedUser).filter(BlockedUser.vk_id == user.vk_id).delete()
    s.commit()
    log_action("toggle_block", f"user {user_id} -> blocked={user.is_blocked}")
    flash("Статус блокировки обновлён", "success")
    return redirect(request.referrer or url_for("users"))


def _resolve_vk_user(link_or_name):
    """Requirement 8: resolve a VK link/username to (vk_id, full_name).

    Accepts vk.com/id123, vk.com/durov, @durov, id123 or a raw numeric id.
    Screen names are resolved through the VK API (users.get)."""
    raw = (link_or_name or "").strip()
    if not raw:
        return None, None
    m = re.search(r"vk\.com/([^/?#\s]+)", raw)
    token_str = (m.group(1) if m else raw).lstrip("@").strip()
    direct = None
    m2 = re.fullmatch(r"id(\d+)", token_str)
    if m2:
        direct = int(m2.group(1))
    elif token_str.isdigit():
        direct = int(token_str)
    user_ids = str(direct) if direct is not None else token_str
    if not config.VK_TOKEN:
        return direct, None
    try:
        resp = requests.get(
            "https://api.vk.com/method/users.get",
            params={
                "user_ids": user_ids,
                "fields": "first_name,last_name",
                "access_token": config.VK_TOKEN,
                "v": config.VK_API_VERSION,
            },
            timeout=10,
        )
        data = resp.json()
        items = data.get("response") or []
        if items:
            it = items[0]
            name = ("%s %s" % (it.get("first_name", ""), it.get("last_name", ""))).strip() or None
            return int(it["id"]), name
    except Exception as exc:  # noqa: BLE001
        log.error("VK id resolution failed for %s: %s", user_ids, exc)
    return direct, None


_ROLE_CHOICES = ("passenger", "driver", "dispatcher", "admin")


@app.route("/users/assign-role", methods=["POST"])
@login_required
def assign_role_by_link():
    """Requirement 8: grant a role to a user identified by a VK link."""
    s = db()
    link = request.form.get("vk_link", "").strip()
    role = request.form.get("role", "").strip()
    if role not in _ROLE_CHOICES:
        flash("Некорректная роль", "warning")
        return redirect(url_for("users"))
    vk_id, name = _resolve_vk_user(link)
    if not vk_id:
        flash("Не удалось определить VK ID. Укажите vk.com/id123 или короткое имя.", "danger")
        return redirect(url_for("users"))
    user = s.query(User).filter(User.vk_id == vk_id).one_or_none()
    if user is None:
        user = User(vk_id=vk_id, role="passenger", granted_roles="passenger")
        if name:
            user.full_name = name
        s.add(user)
        s.flush()
    elif name and not user.full_name:
        user.full_name = name
    user.grant_role(role)
    user.role = role
    s.commit()
    log_action("assign_role_by_link", "vk_id=%s role=%s" % (vk_id, role))
    flash("Роль «%s» назначена пользователю id%s" % (role, vk_id), "success")
    return redirect(url_for("users"))


@app.route("/users/revoke-role", methods=["POST"])
@login_required
def revoke_role_by_link():
    """Requirement 8: revoke a role from a user identified by a VK link."""
    s = db()
    link = request.form.get("vk_link", "").strip()
    role = request.form.get("role", "").strip()
    vk_id, _name = _resolve_vk_user(link)
    if not vk_id:
        flash("Не удалось определить VK ID.", "danger")
        return redirect(url_for("users"))
    user = s.query(User).filter(User.vk_id == vk_id).one_or_none()
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("users"))
    user.revoke_role(role)
    s.commit()
    log_action("revoke_role_by_link", "vk_id=%s role=%s" % (vk_id, role))
    flash("Роль «%s» снята у id%s" % (role, vk_id), "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/revoke-role", methods=["POST"])
@login_required
def revoke_user_role(user_id):
    s = db()
    user = s.get(User, user_id)
    if not user:
        abort(404)
    role = request.form.get("role", "")
    if role not in ("driver", "dispatcher") or not user.has_role(role):
        flash("Эту роль снять нельзя", "warning")
        return redirect(request.referrer or url_for("users"))
    user.revoke_role(role)
    s.commit()
    log_action("revoke_user_role", f"user {user_id} role={role}")
    flash("Роль снята", "success")
    return redirect(request.referrer or url_for("users"))


@app.route("/users/<int:user_id>/set-role", methods=["POST"])
@login_required
def set_role(user_id):
    s = db()
    user = s.get(User, user_id)
    if not user:
        abort(404)
    new_role = request.form["role"]
    user.grant_role(new_role)
    user.role = new_role
    s.commit()
    log_action("set_role", f"user {user_id} -> {user.role}")
    flash("Роль обновлена", "success")
    return redirect(request.referrer or url_for("users"))


@app.route("/users/<int:user_id>/reset-driver-block", methods=["POST"])
@login_required
def reset_driver_block(user_id):
    """Requirement 5: manually clear a driver's cancel-block + violation counter."""
    s = db()
    user = s.get(User, user_id)
    if not user:
        abort(404)
    user.driver_blocked_until = None
    user.driver_cancel_after_accept_count = 0
    user.driver_last_violation_at = None
    s.commit()
    log_action("reset_driver_block", f"user {user_id}")
    flash("Блокировка водителя сброшена", "success")
    return redirect(request.referrer or url_for("users"))


# --------------------------------------------------------------------------- #
#  All reviews (admin can delete any review)                                  #
# --------------------------------------------------------------------------- #
@app.route("/reviews")
@login_required
def reviews_all():
    s = db()
    kind = request.args.get("kind") or ""
    q = s.query(Review).order_by(Review.created_at.desc())
    if kind in ("passenger_to_driver", "driver_to_passenger"):
        q = q.filter(Review.kind == kind)
    rows = q.limit(500).all()
    items = []
    for r in rows:
        driver = s.get(User, r.driver_id) if r.driver_id else None
        passenger = s.get(User, r.passenger_id) if r.passenger_id else None
        if r.kind == "driver_to_passenger":
            author, target = driver, passenger
        else:
            author, target = passenger, driver
        items.append({
            "id": r.id,
            "kind": r.kind,
            "stars": r.stars or 0,
            "text": r.text or "",
            "created": time_utils.format_local(r.created_at) if r.created_at else "",
            "author": (author.full_name if author else None) or "-",
            "author_vk": author.vk_id if author else None,
            "target": (target.full_name if target else None) or "-",
            "target_vk": target.vk_id if target else None,
        })
    return render_template("reviews.html", items=items, kind=kind)


@app.route("/reviews/<int:review_id>/delete", methods=["POST"])
@login_required
def delete_review(review_id):
    s = db()
    r = s.get(Review, review_id)
    if not r:
        abort(404)
    s.delete(r)
    s.commit()
    log_action("delete_review", f"review {review_id}")
    flash("Отзыв удалён", "success")
    return redirect(request.referrer or url_for("reviews_all"))


# --------------------------------------------------------------------------- #
#  False calls (requirements 6-7)                                              #
# --------------------------------------------------------------------------- #
@app.route("/fake-calls")
@login_required
def fake_calls():
    s = db()
    status = request.args.get("status", "")
    query = s.query(FakeCall).options(joinedload(FakeCall.passenger), joinedload(FakeCall.driver))
    if status:
        query = query.filter(FakeCall.status == status)
    rows = query.order_by(FakeCall.created_at.desc()).limit(300).all()
    items = []
    for fc in rows:
        passenger = fc.passenger
        driver = fc.driver
        items.append({
            "fc": fc,
            "passenger": passenger,
            "driver": driver,
            "passenger_link": f"https://vk.com/id{passenger.vk_id}" if passenger else "",
            "driver_link": f"https://vk.com/id{driver.vk_id}" if driver else "",
        })
    return render_template("fake_calls.html", items=items, status=status)


@app.route("/fake-calls/<int:fc_id>/mark-paid", methods=["POST"])
@login_required
def fake_call_mark_paid(fc_id):
    s = db()
    fc = s.get(FakeCall, fc_id)
    if not fc:
        abort(404)
    fc.status = "paid"
    fc.paid_at = time_utils.now()
    passenger = s.get(User, fc.passenger_id)
    if passenger:
        other = s.query(FakeCall).filter(
            FakeCall.passenger_id == passenger.id,
            FakeCall.status == "pending",
            FakeCall.id != fc.id,
        ).first()
        if other is None:
            passenger.passenger_fake_call_blocked = False
            passenger.passenger_fake_call_blocked_until = None
    s.commit()
    log_action("fake_call_mark_paid", f"fake_call {fc_id}")
    flash("Ложный вызов отмечен оплаченным", "success")
    return redirect(request.referrer or url_for("fake_calls"))


# --------------------------------------------------------------------------- #
#  Orders                                                                      #
# --------------------------------------------------------------------------- #
@app.route("/orders")
@login_required
def orders():
    s = db()
    status = request.args.get("status", "")
    query = s.query(Order).options(joinedload(Order.passenger), joinedload(Order.driver))
    if status:
        query = query.filter(Order.status == status)
    orders_list = query.order_by(Order.created_at.desc()).limit(200).all()
    drivers = s.query(User).filter(User.granted_roles.like("%driver%"), User.is_blocked.is_(False)).all()
    return render_template("orders.html", orders=orders_list, status=status, drivers=drivers)


@app.route("/orders/<int:order_id>/assign", methods=["POST"])
@login_required
def assign_driver(order_id):
    s = db()
    order = s.get(Order, order_id)
    if not order:
        abort(404)
    driver_id = request.form.get("driver_id")
    if driver_id:
        order.driver_id = int(driver_id)
        order.status = "assigned"
        s.commit()
        log_action("assign_driver", f"order {order_id} -> driver {driver_id}")
        flash("Водитель назначен", "success")
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def cancel_order(order_id):
    s = db()
    order = s.get(Order, order_id)
    if not order:
        abort(404)
    reason = request.form.get("reason", "").strip()
    order.status = "cancelled"
    order.cancelled_at = time_utils.now()
    s.commit()
    log_action("cancel_order", f"order {order_id} reason={reason or '—'}")
    flash("Заказ отменён" + (f" ({reason})" if reason else ""), "success")
    return redirect(url_for("orders"))


# --------------------------------------------------------------------------- #
#  Broadcast                                                                   #
# --------------------------------------------------------------------------- #
@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast_view():
    if request.method == "POST":
        text = request.form["text"].strip()
        target = request.form.get("target", "all")
        attachment = request.form.get("attachment", "").strip() or None
        if not text:
            flash("Введите текст рассылки", "warning")
        else:
            job_id = broadcast.start_broadcast(text, target, attachment)
            log_action("broadcast", f"target={target}")
            flash("Рассылка запущена в фоновом режиме", "success")
            return redirect(url_for("broadcast_status", job_id=job_id))
    return render_template("broadcast.html")


@app.route("/broadcast/status/<job_id>")
@login_required
def broadcast_status(job_id):
    job = broadcast.get_job(job_id)
    return render_template("broadcast.html", job=job, job_id=job_id)


@app.route("/api/broadcast/<job_id>")
@login_required
def broadcast_progress(job_id):
    job = broadcast.get_job(job_id)
    if not job:
        return jsonify({"found": False})
    return jsonify({
        "found": True, "total": job.total, "sent": job.sent,
        "failed": job.failed, "done": job.done, "error": job.error,
    })


# --------------------------------------------------------------------------- #
#  Promotions + Promocodes                                                     #
# --------------------------------------------------------------------------- #
@app.route("/promotions")
@login_required
def promotions():
    s = db()
    promos = s.query(Promotion).order_by(Promotion.created_at.desc()).all()
    codes = s.query(Promocode).order_by(Promocode.id.desc()).all()
    return render_template("promotions.html", promos=promos, codes=codes)


@app.route("/promotions/create", methods=["POST"])
@login_required
def create_promotion():
    s = db()
    s.add(Promotion(title=request.form["title"], text=request.form["text"]))
    s.commit()
    log_action("create_promotion", request.form["title"])
    flash("Акция создана", "success")
    return redirect(url_for("promotions"))


@app.route("/promotions/<int:promo_id>/delete", methods=["POST"])
@login_required
def delete_promotion(promo_id):
    s = db()
    obj = s.get(Promotion, promo_id)
    if obj:
        s.delete(obj)
        s.commit()
        log_action("delete_promotion", str(promo_id))
    return redirect(url_for("promotions"))


@app.route("/promocodes/create", methods=["POST"])
@login_required
def create_promocode():
    s = db()
    valid_until = request.form.get("valid_until") or None
    code = Promocode(
        code=request.form["code"].strip().upper(),
        discount=float(request.form["discount"]),
        discount_type=request.form.get("discount_type", "percent"),
        usage_limit=int(request.form["usage_limit"]) if request.form.get("usage_limit") else None,
        valid_until=dt.datetime.fromisoformat(valid_until) if valid_until else None,
    )
    s.add(code)
    s.commit()
    log_action("create_promocode", code.code)
    flash("Промокод создан", "success")
    return redirect(url_for("promotions"))


@app.route("/promocodes/<int:code_id>/delete", methods=["POST"])
@login_required
def delete_promocode(code_id):
    s = db()
    obj = s.get(Promocode, code_id)
    if obj:
        s.delete(obj)
        s.commit()
        log_action("delete_promocode", str(code_id))
    return redirect(url_for("promotions"))


# --------------------------------------------------------------------------- #
#  Settings                                                                    #
# --------------------------------------------------------------------------- #
@app.route("/bot-texts")
@login_required
def bot_texts_view():
    """Searchable, paginated editor containing only active outgoing texts."""
    s = db()
    bm.ensure_defaults(s)
    s.commit()
    query = (request.args.get("q") or "").strip().casefold()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 40
    items = [{
        "kind": "bot", "key": row.key, "title": bm.title_for(row.key),
        "text": row.text or "", "default": bm.DEFAULTS[row.key][1],
    } for row in s.query(BotMessage).order_by(BotMessage.key.asc()).all()]
    items.extend({
        "kind": "setting", "key": key, "title": key,
        "text": get_setting(s, key, DEFAULTS[key]), "default": DEFAULTS[key],
    } for key in active_text_keys())
    items = [item for item in items if not query
             or query in item["key"].casefold()
             or query in item["title"].casefold()
             or query in item["text"].casefold()]
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    visible = items[(page - 1) * per_page:page * per_page]
    return render_template(
        "bot_texts.html",
        items=visible,
        q=request.args.get("q", ""),
        page=page,
        pages=pages,
        total=total,
    )


@app.route("/bot-texts/<kind>/<path:key>/update", methods=["POST"])
@login_required
def update_bot_text(kind: str, key: str):
    if kind == "bot" and key not in bm.DEFAULTS:
        abort(404)
    if kind == "setting" and key not in active_text_keys():
        abort(404)
    s = db()
    value = request.form.get("text", "")[:4000]
    if kind == "bot":
        bm.set_message(s, key, text=value)
    else:
        set_setting(s, key, value)
    s.commit()
    log_action("update_bot_text", key)
    flash("Текст сохранён", "success")
    return redirect(url_for(
        "bot_texts_view",
        q=request.form.get("q", ""),
        page=request.form.get("page", "1"),
    ))


@app.route("/bot-texts/<kind>/<path:key>/reset", methods=["POST"])
@login_required
def reset_bot_text(kind: str, key: str):
    if kind == "bot" and key not in bm.DEFAULTS:
        abort(404)
    if kind == "setting" and key not in active_text_keys():
        abort(404)
    s = db()
    if kind == "bot":
        bm.set_message(s, key, text=bm.DEFAULTS[key][1])
    else:
        set_setting(s, key, DEFAULTS[key])
    s.commit()
    log_action("reset_bot_text", key)
    flash("Возвращён исходный текст", "info")
    return redirect(url_for(
        "bot_texts_view",
        q=request.form.get("q", ""),
        page=request.form.get("page", "1"),
    ))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():
    s = db()
    ensure_defaults(s)
    s.commit()
    if request.method == "POST":
        allowed = set(DEFAULTS)
        changed = []
        for key, value in request.form.items():
            if key not in allowed:
                continue
            old_value = get_setting(s, key)
            if str(old_value or "") != value:
                set_setting(s, key, value)
                changed.append(key)
        s.commit()
        if changed:
            log_action("update_settings", "изменено: " + ", ".join(changed))
            flash("Сохранено: " + ", ".join(changed), "success")
        else:
            flash("Изменений нет", "info")
        return redirect(url_for("settings_view"))
    current = get_all_settings(s)
    cities = s.query(City).order_by(City.name).all()
    return render_template("settings.html", settings=current, cities=cities)


@app.route("/cities/create", methods=["POST"])
@login_required
def create_city():
    s = db()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Название города не может быть пустым", "danger")
        return redirect(url_for("settings_view"))
    existing = s.query(City).filter(func.lower(City.name) == name.lower()).one_or_none()
    if existing:
        existing.is_active = True
        flash("Город уже существовал и снова включён", "info")
    else:
        s.add(City(name=name, is_active=True))
    s.commit()
    log_action("create_city", name)
    return redirect(url_for("settings_view"))


@app.route("/cities/<int:city_id>/update", methods=["POST"])
@login_required
def update_city(city_id: int):
    """Rename/toggle a recognition city and keep denormalized line names valid."""
    s = db()
    city = s.get(City, city_id)
    if city is None:
        abort(404)
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("Название города не может быть пустым", "danger")
        return redirect(url_for("settings_view"))
    duplicate = (
        s.query(City)
        .filter(City.id != city.id, func.lower(City.name) == new_name.lower())
        .one_or_none()
    )
    if duplicate:
        flash("Город с таким названием уже существует", "danger")
        return redirect(url_for("settings_view"))

    old_name = city.name
    city.name = new_name
    city.is_active = request.form.get("is_active") == "1"
    # Driver/current order fields intentionally keep the human-readable line
    # name, so update them atomically with the city row.
    s.query(User).filter(User.current_line == old_name).update(
        {User.current_line: new_name}, synchronize_session=False
    )
    s.query(Order).filter(Order.line == old_name).update(
        {Order.line: new_name}, synchronize_session=False
    )
    s.query(Order).filter(Order.pickup_city == old_name).update(
        {Order.pickup_city: new_name}, synchronize_session=False
    )
    s.commit()
    log_action("update_city", f"{old_name} -> {new_name}; active={city.is_active}")
    flash("Город сохранён", "success")
    return redirect(url_for("settings_view"))


# --------------------------------------------------------------------------- #
#  Price sections («Прайс» -> «Самые популярные направления»)                  #
# --------------------------------------------------------------------------- #
@app.route("/price-sections", methods=["GET", "POST"])
@login_required
def price_sections_view():
    s = db()
    ps.ensure_defaults(s)
    if request.method == "POST":
        key = request.form.get("section_key", "")
        if key not in ps.all_keys():
            abort(404)
        new_title = request.form.get("title", "").strip()
        new_content = request.form.get("content", "").strip()
        # Capture the previous values so we can detect whether a line changed
        # and only then notify the drivers (avoids spam on a no-op save).
        old = ps.get_section(s, key)
        old_title = (old.title if old else "") or ""
        old_content = (old.content if old else "") or ""
        ps.set_section(
            s,
            key,
            title=new_title,
            content=new_content,
            image_url=request.form.get("image_url", "").strip() or None,
            update_image_url=True,
        )
        s.commit()
        log_action("update_price_section", key)
        # New requirement: when a price line actually changes, every driver gets
        # a notification (default ON). The admin can opt out by ticking «Не
        # уведомлять водителей» (sends no_notify="1").
        changed_parts = []
        if new_title != old_title:
            changed_parts.append(f"Название: {new_title or '—'}")
        if new_content != old_content:
            changed_lines = ps.changed_content_lines(old_content, new_content)
            changed_parts.extend(changed_lines or ["Текст удалён"])
        changed = bool(changed_parts)
        notify_off = request.form.get("no_notify") == "1"
        if changed and not notify_off:
            section_name = new_title or ps.title_for(key)
            text = f"🏷 Изменение прайса — «{section_name}»\n" + "\n".join(changed_parts)
            broadcast.start_broadcast(text, "driver")
            flash(
                f"Раздел «{ps.title_for(key)}» сохранён. Уведомление отправляется водителям…",
                "success",
            )
        else:
            flash(f"Раздел «{ps.title_for(key)}» сохранён", "success")
        return redirect(url_for("price_sections_view"))

    root = ps.get_section(s, ps.ROOT_KEY)
    children = [ps.get_section(s, k) for k in ps.children_keys()]
    return render_template("price_sections.html", root=root, children=children)


@app.route("/price-sections/<string:section_key>/clear-photo", methods=["POST"])
@login_required
def clear_price_section_photo(section_key):
    s = db()
    if section_key not in ps.all_keys():
        abort(404)
    ps.set_section(s, section_key, file_id=None, update_file=True)
    s.commit()
    log_action("clear_price_section_photo", section_key)
    flash("Фото удалено", "success")
    return redirect(url_for("price_sections_view"))


# --------------------------------------------------------------------------- #
#  Admin logs                                                                  #
# --------------------------------------------------------------------------- #
@app.route("/logs")
@login_required
def logs():
    s = db()
    entries = s.query(AdminLog).order_by(AdminLog.created_at.desc()).limit(300).all()
    return render_template("logs.html", logs=entries)


# --------------------------------------------------------------------------- #
#  Excel export                                                                #
# --------------------------------------------------------------------------- #
@app.route("/export/stats.xlsx")
@login_required
def export_stats():
    buffer = build_stats_workbook(db())
    log_action("export_stats", "")
    filename = f"stats_{time_utils.now().date().isoformat()}.xlsx"
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.template_filter("dt")
def _fmt_dt(value) -> str:
    if not value:
        return ""
    return time_utils.format_local(value)


def _review_word(n: int) -> str:
    """Russian pluralization for 'отзыв' (mirrors bot/roles.py)."""
    n = abs(int(n))
    if 10 <= n % 100 <= 20:
        return "отзывов"
    last = n % 10
    if last == 1:
        return "отзыв"
    if 2 <= last <= 4:
        return "отзыва"
    return "отзывов"


ROLE_LABELS = {"passenger": "Пассажир", "driver": "Водитель", "dispatcher": "Диспетчер", "admin": "Администратор"}
ORDER_STATUS_LABELS = {"created": "Новая", "queued": "В очереди", "searching": "Ожидает водителя", "chat_search": "Поиск в чате", "assigned": "Водитель назначен", "arrived": "Водитель прибыл", "in_progress": "В пути", "completed": "Выполнен", "cancelled": "Отменён", "no_drivers": "Нет водителей"}


@app.context_processor
def _ui_labels():
    return {"role_labels": ROLE_LABELS, "order_status_labels": ORDER_STATUS_LABELS}


@app.template_filter("rating")
def _fmt_rating(user) -> str:
    """Render a driver rating as '⭐ 4.5 (12 отзывов)'. Accepts a User object."""
    count = getattr(user, "rating_count", 0) or 0
    if not count:
        return "⭐ — (нет отзывов)"
    avg = getattr(user, "rating", 0) or 0
    return f"⭐ {avg:.1f} ({count} {_review_word(count)})"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
