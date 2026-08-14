"""Track passenger notices bound to the current driver offer.

Revision ID: 0040_offer_notice_tracking
Revises: 0039_passenger_actuality_60
"""
from alembic import op

revision = "0040_offer_notice_tracking"
down_revision = "0039_passenger_actuality_60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS offer_notice_outbox_id INTEGER"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS search_notice_outbox_id INTEGER"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS offer_notice_outbox_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS search_notice_outbox_id")
