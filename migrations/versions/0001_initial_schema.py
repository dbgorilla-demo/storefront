"""initial storefront schema

Revision ID: 0001
Revises:
Create Date: 2026-07-13

An e-commerce OLTP schema:
  - orders.customer_id has a btree index (the hot order-history index)
  - products.name is searched with ILIKE from the catalog search box
  - page_views is high-churn: every browse writes a row
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS storefront")
    op.execute("SET search_path TO storefront, public")

    op.execute(
        """
        CREATE TABLE categories (
            category_id   serial PRIMARY KEY,
            name          text NOT NULL,
            slug          text NOT NULL UNIQUE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE products (
            product_id    serial PRIMARY KEY,
            sku           text NOT NULL UNIQUE,
            name          text NOT NULL,
            description   text,
            category_id   integer NOT NULL REFERENCES categories(category_id),
            price_cents   integer NOT NULL CHECK (price_cents >= 0),
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_products_category ON products(category_id)")

    op.execute(
        """
        CREATE TABLE inventory (
            product_id          integer PRIMARY KEY REFERENCES products(product_id),
            quantity_available  integer NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
            updated_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE customers (
            customer_id   serial PRIMARY KEY,
            email         text NOT NULL UNIQUE,
            full_name     text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE orders (
            order_id      serial PRIMARY KEY,
            customer_id   integer NOT NULL REFERENCES customers(customer_id),
            status        text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','paid','shipped','cancelled')),
            total_cents   integer NOT NULL DEFAULT 0,
            placed_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The hot index. Customer order-history lookups ride this all day.
    # drops it to show the sequential-scan regression under load.
    op.execute("CREATE INDEX ix_orders_customer ON orders(customer_id)")
    op.execute("CREATE INDEX ix_orders_placed_at ON orders(placed_at)")

    op.execute(
        """
        CREATE TABLE order_items (
            order_item_id     serial PRIMARY KEY,
            order_id          integer NOT NULL REFERENCES orders(order_id),
            product_id        integer NOT NULL REFERENCES products(product_id),
            quantity          integer NOT NULL CHECK (quantity > 0),
            unit_price_cents  integer NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_order_items_order ON order_items(order_id)")

    # High-churn table. Every browse writes a row.
    # autovacuum and churns this until dead tuples dominate.
    op.execute(
        """
        CREATE TABLE page_views (
            page_view_id  bigserial PRIMARY KEY,
            customer_id   integer REFERENCES customers(customer_id),
            product_id    integer REFERENCES products(product_id),
            viewed_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_page_views_product ON page_views(product_id)")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS storefront CASCADE")
