"""Online indexes for the awaiting-shipment customer anti-join (DBGorilla #13).

Revision ID: 0009
Revises: 0008

Autocommit blocks are required for concurrent DDL. A failed build can leave an
invalid index; IF NOT EXISTS cannot repair it. Check definitions/validity before
running or retrying; see docs/recommendation-13.md. Never drop shared indexes.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customers_created_at "
            "ON storefront.customers (created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_customer_shipped "
            "ON storefront.orders (customer_id) WHERE status = 'shipped'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_orders_customer_shipped"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_customers_created_at"
        )
