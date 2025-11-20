"""storefront stored procedures.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13

Three plpgsql functions the app calls in normal operation.

"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Total value of a customer's completed orders.
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

    # Loyalty points earned, one point per whole unit of completed spend.
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

    # Catalog search by name, newest interface for the storefront search box.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION storefront.fn_find_products(p_term text)
        RETURNS TABLE(product_id integer, name text, price_cents integer)
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RETURN QUERY EXECUTE
                'SELECT product_id, name, price_cents FROM storefront.products '
                || 'WHERE name ILIKE ''%' || p_term || '%'' ORDER BY name LIMIT 20';
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS storefront.fn_customer_lifetime_value(integer)")
    op.execute("DROP FUNCTION IF EXISTS storefront.fn_apply_loyalty_points(integer, integer)")
    op.execute("DROP FUNCTION IF EXISTS storefront.fn_find_products(text)")
