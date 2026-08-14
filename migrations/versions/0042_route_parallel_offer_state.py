"""Persist one-minute route-parallel offer state per active trip.

Revision ID: 0042_route_parallel_offer_state
Revises: 0041_front_notice_tracking
"""
from alembic import op

revision = "0042_route_parallel_offer_state"
down_revision = "0041_front_notice_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_driver_id "
        "INTEGER REFERENCES users(id)"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_trip_id "
        "INTEGER REFERENCES orders(id)"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_outbox_id INTEGER"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_auto_excluded_driver_ids TEXT"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_route_fallback "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_auto_offers_disabled "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "UPDATE orders SET parallel_route_fallback=TRUE "
        "WHERE last_decline_reason='route_parallel_fallback' "
        "AND parallel_route_fallback=FALSE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_parallel_offer_driver_id "
        "ON orders(parallel_offer_driver_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_parallel_offer_trip_id "
        "ON orders(parallel_offer_trip_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orders_parallel_offer_trip_id")
    op.execute("DROP INDEX IF EXISTS ix_orders_parallel_offer_driver_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_auto_offers_disabled")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_route_fallback")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_auto_excluded_driver_ids")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_offer_outbox_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_offer_trip_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parallel_offer_driver_id")
