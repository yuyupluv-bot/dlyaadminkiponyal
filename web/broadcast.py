"""Background broadcast worker using a plain daemon thread.

Celery would be overkill and cannot run on Vercel serverless, so broadcasts
are dispatched on a background thread. Progress is stored in-memory. For a
long-running production setup, move this to a dedicated worker on bothost.ru
and enqueue jobs via the DB.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import vk_api
from vk_api.utils import get_random_id

from common.config import config
from common.database import session_scope
from common.logger import get_logger
from common.models import User

log = get_logger("web.broadcast")


@dataclass
class BroadcastJob:
    total: int = 0
    sent: int = 0
    failed: int = 0
    done: bool = False
    error: str | None = None
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, BroadcastJob] = {}


def get_job(job_id: str) -> BroadcastJob | None:
    return _jobs.get(job_id)


def start_broadcast(text: str, target_role: str, attachment: str | None = None) -> str:
    job_id = str(get_random_id())
    job = BroadcastJob()
    _jobs[job_id] = job

    thread = threading.Thread(
        target=_run, args=(job, text, target_role, attachment), daemon=True
    )
    thread.start()
    return job_id


def _run(job: BroadcastJob, text: str, target_role: str, attachment: str | None) -> None:
    try:
        vk_session = vk_api.VkApi(token=config.VK_TOKEN, api_version=config.VK_API_VERSION)
        api = vk_session.get_api()
        with session_scope() as db:
            query = db.query(User).filter(User.is_blocked.is_(False))
            users = query.all()
            if target_role in ("passenger", "driver"):
                recipients = [u.vk_id for u in users if u.has_role(target_role)]
            else:
                recipients = [u.vk_id for u in users]
        job.total = len(recipients)
        batch_size = 25
        for start in range(0, len(recipients), batch_size):
            for vk_id in recipients[start:start + batch_size]:
                delivered = False
                for attempt in range(3):
                    try:
                        params = {"peer_id": vk_id, "message": text, "random_id": get_random_id()}
                        if attachment:
                            params["attachment"] = attachment
                        api.messages.send(**params)
                        job.sent += 1
                        delivered = True
                        break
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Broadcast to %s attempt %s failed: %s", vk_id, attempt + 1, exc)
                        time.sleep(0.4 * (attempt + 1))
                if not delivered:
                    job.failed += 1
                time.sleep(0.05)
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        job.error = str(exc)
        log.exception("Broadcast job crashed: %s", exc)
    finally:
        job.done = True
