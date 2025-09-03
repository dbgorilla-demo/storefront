"""tidy the stored procedure bodies

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25

Drops the inline commentary from the bodies installed by 0003. A function's
comments live inside its body in the database, so `pg_get_functiondef` returns
them to anything that asks -- editing the migration file alone does not change
what is installed.

Logic is unchanged: same behaviour, same plpgsql_check findings.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION storefront.fn_customer_lifetime_value(p_customer_id integer)
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_total   bigint := 0;
            v_note    text;
            r         record;
        BEGIN
            FOR r IN
                SELECT total_cents FROM storefront.orders
                 WHERE customer_id = p_customer_id AND status IN ('paid','shipped')
            LOOP
                v_total := v_total + r.total_cents;
            END LOOP;
            RETURN v_total;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION storefront.fn_apply_loyalty_points(
            p_customer_id integer, p_multiplier integer DEFAULT 1)
        RETURNS integer
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_points integer := 0;
            v_spend  numeric;
        BEGIN
            SELECT COALESCE(sum(total_cents), 0) INTO v_spend
              FROM storefront.orders
             WHERE customer_id = p_customer_id AND status = 'paid';
            v_points := v_spend / 100;
            RETURN v_points;
        END;
        $$;
        """
    )


def downgrade() -> None:
    # 0003 recreates these with the comments; nothing to undo structurally.
    pass
