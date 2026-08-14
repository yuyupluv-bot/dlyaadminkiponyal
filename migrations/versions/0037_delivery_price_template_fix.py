"""Repair malformed delivery price template.

Revision ID: 0037_delivery_price_template_fix
Revises: 0036_long_distance_booking_only
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_delivery_price_template_fix"
down_revision = "0036_long_distance_booking_only"
branch_labels = None
depends_on = None

CORRECT = "🚗 Водитель готов выполнить вашу доставку за {price:.0f} ₽ + оплата чека из магазина. Согласны?"

def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE settings SET value=:correct WHERE key='msg_delivery_offer' AND value LIKE '%foume%'"),
        {"correct": CORRECT},
    )

def downgrade() -> None:
    pass
