"""seed the storefront with a realistic catalog and customer base

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13

Volumes chosen so query plans are non-trivial (seq scan vs index scan actually
diverge) but seeding still finishes in a few seconds:
  - 12 categories
  - 5,000 products
  - 8,000 customers
  - 40,000 historical orders with 1-4 items each
Traffic generation grows orders/page_views from here at runtime.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET search_path TO storefront, public")

    op.execute(
        """
        INSERT INTO categories (name, slug)
        SELECT c.name, lower(replace(c.name, ' ', '-'))
        FROM (VALUES
            ('Electronics'), ('Home & Kitchen'), ('Books'), ('Apparel'),
            ('Toys & Games'), ('Sports'), ('Beauty'), ('Grocery'),
            ('Automotive'), ('Garden'), ('Office'), ('Pet Supplies')
        ) AS c(name)
        """
    )

    # 5,000 products spread across the categories.
    op.execute(
        """
        INSERT INTO products (sku, name, description, category_id, price_cents)
        SELECT
            'SKU-' || lpad(g::text, 6, '0'),
            (ARRAY['Deluxe','Compact','Premium','Basic','Pro','Eco','Ultra','Vintage'])[1 + (g % 8)]
                || ' ' ||
            (ARRAY['Widget','Gadget','Blender','Novel','Jersey','Puzzle','Serum','Snack',
                   'Wrench','Trowel','Stapler','Chew Toy'])[1 + (g % 12)]
                || ' ' || g,
            'Auto-generated demo product ' || g,
            1 + (g % 12),
            499 + (g * 37) % 20000
        FROM generate_series(1, 5000) AS g
        """
    )

    op.execute(
        """
        INSERT INTO inventory (product_id, quantity_available)
        SELECT product_id, 25 + (product_id * 7) % 500 FROM products
        """
    )

    op.execute(
        """
        INSERT INTO customers (email, full_name)
        SELECT
            'customer' || g || '@example.com',
            (ARRAY['Alex','Sam','Jordan','Taylor','Morgan','Casey','Riley','Jamie'])[1 + (g % 8)]
                || ' ' ||
            (ARRAY['Smith','Jones','Lee','Patel','Garcia','Nguyen','Khan','OBrien'])[1 + (g % 8)]
        FROM generate_series(1, 8000) AS g
        """
    )

    # 40,000 historical orders spread across the last 180 days.
    op.execute(
        """
        INSERT INTO orders (customer_id, status, placed_at)
        SELECT
            1 + (g % 8000),
            (ARRAY['paid','paid','paid','shipped','cancelled'])[1 + (g % 5)],
            now() - (make_interval(days => (g % 180))) - make_interval(mins => (g % 1440))
        FROM generate_series(1, 40000) AS g
        """
    )

    # 1-4 items per order.
    op.execute(
        """
        INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
        SELECT
            o.order_id,
            1 + ((o.order_id * s.n * 13) % 5000),
            1 + (s.n % 4),
            p.price_cents
        FROM orders o
        CROSS JOIN LATERAL generate_series(1, 1 + (o.order_id % 4)) AS s(n)
        JOIN products p ON p.product_id = 1 + ((o.order_id * s.n * 13) % 5000)
        """
    )

    op.execute(
        """
        UPDATE orders o
        SET total_cents = sub.total
        FROM (
            SELECT order_id, sum(quantity * unit_price_cents) AS total
            FROM order_items GROUP BY order_id
        ) sub
        WHERE o.order_id = sub.order_id
        """
    )

    op.execute("ANALYZE storefront.orders")
    op.execute("ANALYZE storefront.order_items")
    op.execute("ANALYZE storefront.products")


def downgrade() -> None:
    op.execute("SET search_path TO storefront, public")
    op.execute("TRUNCATE page_views, order_items, orders, inventory, products, customers, categories RESTART IDENTITY CASCADE")
