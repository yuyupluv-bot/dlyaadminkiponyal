"""Reduce passenger actuality confirmation timeout to one minute.

Revision ID: 0039_passenger_actuality_60
Revises: 0038_driver_chat_45_minutes
"""
from alembic import op

revision = "0039_passenger_actuality_60"
down_revision = "0038_driver_chat_45_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE settings SET value='60' WHERE key='passenger_poll_timeout'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE settings SET value='300' WHERE key='passenger_poll_timeout'"
    )
