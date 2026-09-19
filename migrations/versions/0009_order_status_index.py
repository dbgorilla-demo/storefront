"""index orders by status; widen total_cents for high-value orders

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_orders_status ON storefront.orders (status)")
    op.execute("ALTER TABLE storefront.orders ALTER COLUMN total_cents TYPE bigint")
    op.execute("ALTER TABLE storefront.orders ADD COLUMN fulfilment_note text NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE storefront.orders DROP COLUMN fulfilment_note")
    op.execute("ALTER TABLE storefront.orders ALTER COLUMN total_cents TYPE integer")
    op.execute("DROP INDEX IF EXISTS storefront.ix_orders_status")
