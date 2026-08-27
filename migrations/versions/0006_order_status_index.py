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
    # CONCURRENTLY cannot run inside a transaction block; env.py wraps every
    # migration in one, so this statement needs to drop out of it.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_status ON storefront.orders (status)")
        op.execute("RESET lock_timeout")

    # int -> bigint is not binary-coercible, so this rewrites the whole table
    # under ACCESS EXCLUSIVE. orders is a live, continuously-written table;
    # fail fast rather than queue behind traffic and stall it.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE storefront.orders ALTER COLUMN total_cents TYPE bigint")
    op.execute("ALTER TABLE storefront.orders ADD COLUMN fulfilment_note text NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute(
        """
        DO $guard$
        DECLARE
            offending bigint;
        BEGIN
            SELECT total_cents INTO offending
            FROM storefront.orders
            WHERE total_cents > 2147483647 OR total_cents < -2147483648
            LIMIT 1;
            IF FOUND THEN
                RAISE EXCEPTION
                    'downgrade aborted: storefront.orders.total_cents has a value (%) outside the integer range; this downgrade is unsafe once such orders exist',
                    offending;
            END IF;
        END
        $guard$;
        """
    )
    op.execute("ALTER TABLE storefront.orders DROP COLUMN fulfilment_note")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE storefront.orders ALTER COLUMN total_cents TYPE integer")

    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_orders_status")
        op.execute("RESET lock_timeout")
