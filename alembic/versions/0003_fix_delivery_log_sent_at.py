"""align delivery_log timestamp column with ORM model

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("delivery_log")}


def upgrade() -> None:
    columns = _get_columns()
    if "created_at" in columns and "sent_at" not in columns:
        with op.batch_alter_table("delivery_log") as batch_op:
            batch_op.alter_column(
                "created_at",
                new_column_name="sent_at",
                existing_type=sa.DateTime(),
                existing_nullable=True,
                existing_server_default=sa.text("CURRENT_TIMESTAMP"),
            )


def downgrade() -> None:
    columns = _get_columns()
    if "sent_at" in columns and "created_at" not in columns:
        with op.batch_alter_table("delivery_log") as batch_op:
            batch_op.alter_column(
                "sent_at",
                new_column_name="created_at",
                existing_type=sa.DateTime(),
                existing_nullable=True,
                existing_server_default=sa.text("CURRENT_TIMESTAMP"),
            )
