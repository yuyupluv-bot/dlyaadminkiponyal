"""Production launcher for the Flask admin panel on BotHost."""
import os
import sys

from web.app import app


if __name__ == "__main__":
    # BotHost assigns the public HTTP port through PORT. Starting gunicorn
    # here means the hosting startup file can simply be ``admin_main.py``.
    port = os.getenv("PORT", "8000")
    os.execvp(sys.executable, [
        sys.executable, "-m", "gunicorn", "wsgi:app",
        "--bind", f"0.0.0.0:{port}",
        "--workers", os.getenv("WEB_WORKERS", "1"),
        "--threads", os.getenv("WEB_THREADS", "4"),
        "--timeout", "120",
        "--access-logfile", "-",
        "--error-logfile", "-",
    ])
