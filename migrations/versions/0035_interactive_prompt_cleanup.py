"""Track interactive prompts so obsolete VK keyboards can be removed."""
from alembic import op
import sqlalchemy as sa

revision = "0035_interactive_prompt_cleanup"
down_revision = "0034_temporary_driver_until"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    order_columns = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    for name in ("passenger_rating_prompt_outbox_id", "driver_rating_prompt_outbox_id", "chat_actuality_prompt_outbox_id"):
        if name not in order_columns:
            op.add_column("orders", sa.Column(name, sa.Integer(), nullable=True))
    queue_columns = {c["name"] for c in sa.inspect(bind).get_columns("passenger_queue")}
    if "actuality_prompt_outbox_id" not in queue_columns:
        op.add_column("passenger_queue", sa.Column("actuality_prompt_outbox_id", sa.Integer(), nullable=True))
    op.execute("UPDATE settings SET value='300' WHERE key='passenger_poll_timeout' AND value='120'")

def downgrade():
    bind = op.get_bind()
    queue_columns = {c["name"] for c in sa.inspect(bind).get_columns("passenger_queue")}
    if "actuality_prompt_outbox_id" in queue_columns:
        op.drop_column("passenger_queue", "actuality_prompt_outbox_id")
    order_columns = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    for name in ("chat_actuality_prompt_outbox_id", "driver_rating_prompt_outbox_id", "passenger_rating_prompt_outbox_id"):
        if name in order_columns:
            op.drop_column("orders", name)
