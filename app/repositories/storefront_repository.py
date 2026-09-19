"""Postgres adapter implementing StorefrontRepository.

The only module (with its sibling adapters) allowed to hold a connection pool
and write SQL. It maps rows to the frozen dataclasses in models.py; nothing
psycopg-shaped escapes.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.repositories.models import (
    OrderLine,
    OrderReceipt,
    ProductSummary,
    RevenueBucket,
    WorkloadSample,
)


class PostgresStorefrontRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def sample_workload_ids(self, limit: int) -> WorkloadSample:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT customer_id FROM storefront.customers "
                "TABLESAMPLE SYSTEM (2) LIMIT %s",
                (limit,),
            )
            customer_ids = tuple(r[0] for r in await cur.fetchall())
            cur = await conn.execute(
                "SELECT product_id FROM storefront.products "
                "TABLESAMPLE SYSTEM (2) LIMIT %s",
                (limit,),
            )
            product_ids = tuple(r[0] for r in await cur.fetchall())
            cur = await conn.execute("SELECT category_id FROM storefront.categories")
            category_ids = tuple(r[0] for r in await cur.fetchall())
            cur = await conn.execute(
                "SELECT split_part(name, ' ', 2) FROM storefront.products "
                "TABLESAMPLE SYSTEM (2) LIMIT %s",
                (limit,),
            )
            search_terms = tuple(r[0] for r in await cur.fetchall() if r[0])
        # Guard against an empty sample on a freshly-seeded db.
        if not customer_ids:
            customer_ids = (1,)
        if not product_ids:
            product_ids = (1,)
        if not category_ids:
            category_ids = (1,)
        if not search_terms:
            search_terms = ("Widget",)
        return WorkloadSample(
            customer_ids=customer_ids,
            product_ids=product_ids,
            category_ids=category_ids,
            search_terms=search_terms,
        )

    async def browse_category(
        self, category_id: int, limit: int
    ) -> list[ProductSummary]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.price_cents,
                       c.name, COALESCE(i.quantity_available, 0)
                  FROM storefront.products p
                  JOIN storefront.categories c ON c.category_id = p.category_id
                  LEFT JOIN storefront.inventory i ON i.product_id = p.product_id
                 WHERE p.category_id = %s
                 ORDER BY p.created_at DESC
                 LIMIT %s
                """,
                (category_id, limit),
            )
            return [self._to_product(r) for r in await cur.fetchall()]

    async def search_products(self, term: str, limit: int) -> list[ProductSummary]:
        # Catalog search for the storefront search box.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.price_cents,
                       c.name, COALESCE(i.quantity_available, 0)
                  FROM storefront.products p
                  JOIN storefront.categories c ON c.category_id = p.category_id
                  LEFT JOIN storefront.inventory i ON i.product_id = p.product_id
                 WHERE p.name ILIKE %s
                 ORDER BY p.name
                 LIMIT %s
                """,
                (f"%{term}%", limit),
            )
            return [self._to_product(r) for r in await cur.fetchall()]

    async def get_product(self, product_id: int) -> ProductSummary | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.price_cents,
                       c.name, COALESCE(i.quantity_available, 0)
                  FROM storefront.products p
                  JOIN storefront.categories c ON c.category_id = p.category_id
                  LEFT JOIN storefront.inventory i ON i.product_id = p.product_id
                 WHERE p.product_id = %s
                """,
                (product_id,),
            )
            row = await cur.fetchone()
            return self._to_product(row) if row else None

    async def record_page_view(self, customer_id: int, product_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO storefront.page_views (customer_id, product_id) "
                "VALUES (%s, %s)",
                (customer_id, product_id),
            )

    async def place_order(
        self, customer_id: int, lines: list[OrderLine]
    ) -> OrderReceipt:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO storefront.orders (customer_id, status) "
                    "VALUES (%s, 'pending') RETURNING order_id, placed_at",
                    (customer_id,),
                )
                order_id, placed_at = await cur.fetchone()
                total = 0
                for ln in lines:
                    # Row lock on inventory — this is where lock storms bite.
                    await conn.execute(
                        "UPDATE storefront.inventory "
                        "SET quantity_available = quantity_available - %s, "
                        "    updated_at = now() "
                        "WHERE product_id = %s AND quantity_available >= %s",
                        (ln.quantity, ln.product_id, ln.quantity),
                    )
                    await conn.execute(
                        "INSERT INTO storefront.order_items "
                        "(order_id, product_id, quantity, unit_price_cents) "
                        "VALUES (%s, %s, %s, %s)",
                        (order_id, ln.product_id, ln.quantity, ln.unit_price_cents),
                    )
                    total += ln.quantity * ln.unit_price_cents
                await conn.execute(
                    "UPDATE storefront.orders SET status='paid', total_cents=%s "
                    "WHERE order_id=%s",
                    (total, order_id),
                )
        return OrderReceipt(
            order_id=order_id,
            customer_id=customer_id,
            status="paid",
            total_cents=total,
            placed_at=placed_at,
            lines=tuple(lines),
        )

    async def get_order(self, order_id: int) -> OrderReceipt | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT order_id, customer_id, status, total_cents, placed_at "
                "FROM storefront.orders WHERE order_id = %s",
                (order_id,),
            )
            head = await cur.fetchone()
            if not head:
                return None
            cur = await conn.execute(
                "SELECT oi.product_id, p.sku, oi.quantity, oi.unit_price_cents "
                "FROM storefront.order_items oi "
                "JOIN storefront.products p ON p.product_id = oi.product_id "
                "WHERE oi.order_id = %s",
                (order_id,),
            )
            lines = tuple(
                OrderLine(
                    product_id=r[0], sku=r[1], quantity=r[2], unit_price_cents=r[3]
                )
                for r in await cur.fetchall()
            )
        return OrderReceipt(
            order_id=head[0],
            customer_id=head[1],
            status=head[2],
            total_cents=head[3],
            placed_at=head[4],
            lines=lines,
        )

    async def customer_order_history(
        self, customer_id: int, limit: int
    ) -> list[OrderReceipt]:
        # Customer order-history lookups; rides ix_orders_customer.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT order_id, customer_id, status, total_cents, placed_at "
                "FROM storefront.orders WHERE customer_id = %s "
                "ORDER BY placed_at DESC LIMIT %s",
                (customer_id, limit),
            )
            return [
                OrderReceipt(
                    order_id=r[0],
                    customer_id=r[1],
                    status=r[2],
                    total_cents=r[3],
                    placed_at=r[4],
                    lines=(),
                )
                for r in await cur.fetchall()
            ]

    async def revenue_report(self, days: int) -> list[RevenueBucket]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT date_trunc('day', placed_at)::date AS day,
                       count(*), COALESCE(sum(total_cents), 0)
                  FROM storefront.orders
                 WHERE placed_at > now() - make_interval(days => %s)
                   AND status IN ('paid','shipped')
                 GROUP BY 1 ORDER BY 1
                """,
                (days,),
            )
            return [
                RevenueBucket(day=r[0], order_count=r[1], revenue_cents=r[2])
                for r in await cur.fetchall()
            ]

    async def recent_order_ids(self, days: int, limit: int) -> list[int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT order_id FROM storefront.orders "
                "WHERE placed_at > now() - make_interval(days => %s) "
                "ORDER BY placed_at DESC LIMIT %s",
                (days, limit),
            )
            return [r[0] for r in await cur.fetchall()]

    async def sales_by_month(self, year: int) -> list[RevenueBucket]:
        # EXTRACT() wraps the indexed column -> ix_orders_placed_at is unusable.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT date_trunc('month', placed_at)::date AS month,
                       count(*), COALESCE(sum(total_cents), 0)
                  FROM storefront.orders
                 WHERE EXTRACT(year FROM placed_at) = %s
                 GROUP BY 1
                 ORDER BY sum(total_cents) DESC
                """,
                (year,),
            )
            return [
                RevenueBucket(day=r[0], order_count=r[1], revenue_cents=r[2])
                for r in await cur.fetchall()
            ]

    async def awaiting_shipment_count(self, days: int) -> int:
        # Customers who have ordered but never had an order ship -- a
        # fulfilment health check for the ops dashboard.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT count(*) FROM storefront.customers c
                 WHERE c.created_at > now() - make_interval(days => %s)
                   AND c.customer_id NOT IN (
                       SELECT customer_id FROM storefront.orders
                        WHERE status = 'shipped')
                """,
                (days,),
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def inventory_reorder_audit(self, limit: int) -> list[ProductSummary]:
        # Ops audit: products low on stock or flagged for review.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.price_cents,
                       c.name, COALESCE(i.quantity_available, 0)
                  FROM storefront.products p
                  JOIN storefront.categories c ON c.category_id = p.category_id
                  LEFT JOIN storefront.inventory i ON i.product_id = p.product_id
                 WHERE i.quantity_available < 30
                    OR p.description ILIKE '%%discontinued%%'
                    OR round(p.price_cents / 100.0) = 99
                 ORDER BY i.quantity_available
                 LIMIT %s
                """,
                (limit,),
            )
            return [self._to_product(r) for r in await cur.fetchall()]

    async def customer_lifetime_value(self, customer_id: int) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT storefront.fn_customer_lifetime_value(%s)", (customer_id,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def apply_loyalty_points(self, customer_id: int) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT storefront.fn_apply_loyalty_points(%s)", (customer_id,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def search_products_proc(self, term: str) -> list[ProductSummary]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT product_id, name, price_cents FROM storefront.fn_find_products(%s)",
                (term,),
            )
            # The proc returns a slimmer row than ProductSummary; fill the rest.
            return [
                ProductSummary(
                    product_id=r[0],
                    sku="",
                    name=r[1],
                    price_cents=r[2],
                    category_name="",
                    quantity_available=0,
                )
                for r in await cur.fetchall()
            ]

    @staticmethod
    def _to_product(row) -> ProductSummary:
        return ProductSummary(
            product_id=row[0],
            sku=row[1],
            name=row[2],
            price_cents=row[3],
            category_name=row[4],
            quantity_available=row[5],
        )
