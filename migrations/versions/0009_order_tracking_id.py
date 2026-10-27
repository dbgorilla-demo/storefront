"""orders: carrier tracking id

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27

Every order gets a tracking id up front, so the carrier integration can look an
order up without exposing our sequential order ids.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE storefront.orders "
        "ADD COLUMN tracking_id uuid NOT NULL DEFAULT gen_random_uuid()"
    )
    op.execute("CREATE UNIQUE INDEX ix_orders_tracking_id ON storefront.orders(tracking_id)")


def downgrade() -> None:
    op.execute("DROP INDEX storefront.ix_orders_tracking_id")
    op.execute("ALTER TABLE storefront.orders DROP COLUMN tracking_id")
