"""orders: drop ix_orders_customer

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27

ix_orders_status_placed (0007) already indexes orders for the fulfilment
queries, so ix_orders_customer is redundant and only slows down checkout
inserts.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX storefront.ix_orders_customer")


def downgrade() -> None:
    op.execute("CREATE INDEX ix_orders_customer ON storefront.orders(customer_id)")
