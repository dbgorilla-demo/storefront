"""order fulfilment tracking

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-27

Fulfilment stops living in the ops team's spreadsheet: orders record when
they shipped and by which carrier, the fulfilment queue gets the index it
reads by, and the awaiting-shipment queue becomes a view the dashboard and
the report share.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE storefront.orders ADD COLUMN shipped_at timestamptz")
    op.execute("ALTER TABLE storefront.orders ADD COLUMN carrier text")
    op.execute(
        "ALTER TABLE storefront.orders ADD CONSTRAINT orders_carrier_check "
        "CHECK (carrier IS NULL OR carrier IN ('ups', 'fedex', 'usps', 'dhl'))"
    )
    op.execute(
        "ALTER TABLE storefront.orders ADD CONSTRAINT orders_shipped_at_check "
        "CHECK (shipped_at IS NULL OR shipped_at >= placed_at)"
    )
    op.execute(
        "CREATE INDEX ix_orders_status_placed ON storefront.orders(status, placed_at DESC) "
        "WHERE status IN ('paid', 'packed')"
    )
    op.execute(
        """
        CREATE VIEW storefront.v_awaiting_shipment AS
        SELECT o.order_id, o.customer_id, c.email, o.placed_at, o.total_cents, o.status
          FROM storefront.orders o
          JOIN storefront.customers c ON c.customer_id = o.customer_id
         WHERE o.status IN ('paid', 'packed')
           AND o.shipped_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS storefront.v_awaiting_shipment")
    op.execute("DROP INDEX IF EXISTS storefront.ix_orders_status_placed")
    op.execute("ALTER TABLE storefront.orders DROP CONSTRAINT IF EXISTS orders_shipped_at_check")
    op.execute("ALTER TABLE storefront.orders DROP CONSTRAINT IF EXISTS orders_carrier_check")
    op.execute("ALTER TABLE storefront.orders DROP COLUMN IF EXISTS carrier")
    op.execute("ALTER TABLE storefront.orders DROP COLUMN IF EXISTS shipped_at")
