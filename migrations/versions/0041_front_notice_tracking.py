"""Track the first-in-queue notice so it can be deleted.

Revision ID: 0041_front_notice_tracking
Revises: 0040_offer_notice_tracking
"""
from alembic import op

revision = "0041_front_notice_tracking"
down_revision = "0040_offer_notice_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE drivers_queue ADD COLUMN IF NOT EXISTS front_notice_outbox_id INTEGER"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE drivers_queue DROP COLUMN IF EXISTS front_notice_outbox_id")
