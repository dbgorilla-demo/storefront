"""customer loyalty tiers

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27

Loyalty moves from a points balance on the customer to a tier the points
resolve to. The tier carries the multiplier the checkout applies, so pricing
no longer needs to know how points are earned.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE storefront.loyalty_tiers (
            tier_id        smallserial PRIMARY KEY,
            name           text NOT NULL UNIQUE,
            min_points     integer NOT NULL CHECK (min_points >= 0),
            multiplier     numeric(4,2) NOT NULL DEFAULT 1.00 CHECK (multiplier >= 1.00),
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO storefront.loyalty_tiers (name, min_points, multiplier) VALUES
            ('bronze',    0, 1.00),
            ('silver',  500, 1.10),
            ('gold',   2500, 1.25),
            ('platinum', 10000, 1.50)
        """
    )
    op.execute(
        "ALTER TABLE storefront.customers "
        "ADD COLUMN loyalty_tier_id smallint NOT NULL DEFAULT 1 "
        "REFERENCES storefront.loyalty_tiers(tier_id)"
    )
    op.execute("CREATE INDEX ix_customers_loyalty_tier ON storefront.customers(loyalty_tier_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS storefront.ix_customers_loyalty_tier")
    op.execute("ALTER TABLE storefront.customers DROP COLUMN IF EXISTS loyalty_tier_id")
    op.execute("DROP TABLE IF EXISTS storefront.loyalty_tiers")
