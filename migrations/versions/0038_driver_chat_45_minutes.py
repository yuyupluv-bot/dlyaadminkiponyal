"""Use one 45-minute auto-cancel window for driver-chat items.

Revision ID: 0038_driver_chat_45_minutes
Revises: 0037_delivery_price_template_fix
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_driver_chat_45_minutes"
down_revision = "0037_delivery_price_template_fix"
branch_labels = None
depends_on = None

KEYS = (
    "driver_chat_timeout", "driver_chat_far_timeout",
    "driver_chat_delivery_timeout", "booking_chat_timeout",
)

def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE settings SET value='2700' WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": list(KEYS)},
    )

def downgrade() -> None:
    pass
