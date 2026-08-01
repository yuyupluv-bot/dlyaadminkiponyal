"""Authentication helpers for the Flask admin (login/password -> hash)."""
from __future__ import annotations

from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from common.config import config
from common.models import AdminUser


def verify_admin(db_session, login: str, password: str) -> AdminUser | None:
    admin = db_session.query(AdminUser).filter(AdminUser.login == login).one_or_none()
    if admin and admin.password_hash and check_password_hash(admin.password_hash, password):
        return admin

    # Fallback to environment-configured credentials so the admin can sign in
    # right after deploy by setting ADMIN_LOGIN + ADMIN_PASSWORD (plaintext) in
    # the hosting env, without pre-generating a hash or running the seed script.
    # On success the admin row is created/updated so the stored hash also works.
    env_login = (config.ADMIN_LOGIN or "").strip()
    env_password = (getattr(config, "ADMIN_PASSWORD", "") or "").strip()
    env_hash = (config.ADMIN_PASSWORD_HASH or "").strip()
    if not env_login or login != env_login:
        return None
    ok = False
    if env_password and password == env_password:
        ok = True
    elif env_hash:
        try:
            ok = check_password_hash(env_hash, password)
        except Exception:
            ok = False
    if not ok:
        return None
    if admin is None:
        admin = AdminUser(login=login, password_hash=generate_password_hash(password))
        db_session.add(admin)
    else:
        admin.password_hash = generate_password_hash(password)
    db_session.flush()
    return admin


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped
