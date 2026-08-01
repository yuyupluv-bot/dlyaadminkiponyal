"""Use one direct long-distance booking flow for passengers and dispatchers."""
from alembic import op

revision = "0036_long_distance_booking_only"
down_revision = "0035_interactive_prompt_cleanup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE settings SET value='📅 Бронь дальние поездки' "
        "WHERE key='btn_booking' AND value IN "
        "('📅 Забронировать поездку', 'Забронировать поездку', '📅 Бронь', 'Бронь')"
    )


def downgrade():
    op.execute(
        "UPDATE settings SET value='📅 Забронировать поездку' "
        "WHERE key='btn_booking' AND value='📅 Бронь дальние поездки'"
    )
