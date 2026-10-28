"""Run with python -m unittest discover -s tests -v.

Optional PostgreSQL tests require TEST_DATABASE_URL pointing at a disposable,
empty database. They create storefront inside a transaction and always roll it
back; an existing storefront schema causes setup to fail rather than overwrite it.
"""

from contextlib import asynccontextmanager, contextmanager
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import psycopg

from app.repositories.storefront_repository import PostgresStorefrontRepository


class ConnectionPool:
    def __init__(self, connection):
        self.conn = connection

    @asynccontextmanager
    async def connection(self):
        yield self.conn


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_days_shipped_predicate_and_integer_count(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone.return_value = (3,)
        conn.execute.return_value = cursor
        repo = PostgresStorefrontRepository(ConnectionPool(conn))
        for days in (1, 90, 0, -1):
            self.assertEqual(await repo.awaiting_shipment_count(days), 3)
            sql, params = conn.execute.call_args.args
            sql = " ".join(sql.split())
            self.assertEqual(params, (days,))
            self.assertIn("c.created_at > now() - make_interval(days => %s)", sql)
            self.assertIn("NOT EXISTS", sql)
            self.assertNotIn("NOT IN", sql)
            self.assertIn("FROM storefront.customers c", sql)
            self.assertIn("SELECT 1 FROM storefront.orders o", sql)
            self.assertIn("o.customer_id = c.customer_id", sql)
            self.assertIn("o.status = 'shipped'", sql)

    async def test_zero_and_missing_row_fallback(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        conn.execute.return_value = cursor
        repo = PostgresStorefrontRepository(ConnectionPool(conn))
        for row in ((0,), None):
            cursor.fetchone.return_value = row
            self.assertEqual(await repo.awaiting_shipment_count(90), 0)


class MigrationTests(unittest.TestCase):
    def test_online_upgrade_and_rollback(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/0009_customer_antijoin_indexes.py"
        spec = importlib.util.spec_from_file_location("antijoin_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        in_autocommit = False

        @contextmanager
        def autocommit():
            nonlocal in_autocommit
            in_autocommit = True
            try:
                yield
            finally:
                in_autocommit = False

        def execute(sql):
            self.assertTrue(in_autocommit, "concurrent DDL must use autocommit")
            statements.append(sql)

        with patch.object(migration.op, "get_context") as context, patch.object(migration.op, "execute", execute):
            context.return_value.autocommit_block.side_effect = autocommit
            migration.upgrade()
            migration.downgrade()
        self.assertEqual(migration.down_revision, "0008")
        self.assertEqual(statements, [
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customers_created_at ON storefront.customers (created_at)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_customer_shipped ON storefront.orders (customer_id) WHERE status = 'shipped'",
            "DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_orders_customer_shipped",
            "DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_customers_created_at",
        ])


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "requires disposable empty PostgreSQL database")
class PostgresSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"])
        self.addAsyncCleanup(self.conn.close)
        self.addAsyncCleanup(self.conn.rollback)
        # No IF NOT EXISTS: refuse to use an existing application's schema.
        await self.conn.execute("CREATE SCHEMA storefront")
        await self.conn.execute("""
            CREATE TABLE storefront.customers (
                customer_id integer PRIMARY KEY,
                created_at timestamptz NOT NULL
            )
        """)
        await self.conn.execute("""
            CREATE TABLE storefront.orders (
                order_id integer PRIMARY KEY,
                customer_id integer NOT NULL REFERENCES storefront.customers,
                status text NOT NULL
            )
        """)
        # now() is stable for the entire transaction: the exact cutoff is not
        # subject to wall-clock drift between fixture insertion and query.
        await self.conn.execute("""
            INSERT INTO storefront.customers VALUES
                (1, now() - interval '1 day'),
                (2, now() - interval '1 day'),
                (3, now() - interval '1 day'),
                (4, now() - interval '1 day'),
                (5, now() - interval '10 days'),
                (6, now() - interval '11 days'),
                (7, now() - interval '5 days')
        """)
        await self.conn.execute("""
            INSERT INTO storefront.orders VALUES
                (1, 2, 'shipped'),
                (2, 3, 'paid'),
                (3, 3, 'pending'),
                (4, 3, 'cancelled'),
                (5, 4, 'shipped'),
                (6, 4, 'shipped')
        """)
        self.repo = PostgresStorefrontRepository(ConnectionPool(self.conn))

    async def test_windows_boundary_and_order_statuses(self):
        # 1 has no orders, 3 only other statuses, 7 no orders. 2 and 4 are
        # excluded even with duplicate matches; 5 is exactly at the cutoff.
        self.assertEqual(await self.repo.awaiting_shipment_count(10), 3)
        self.assertEqual(await self.repo.awaiting_shipment_count(3), 2)
        self.assertEqual(await self.repo.awaiting_shipment_count(0), 0)
        self.assertEqual(await self.repo.awaiting_shipment_count(12), 5)

    async def test_null_is_disallowed_but_would_not_poison_not_exists(self):
        with self.assertRaises(psycopg.errors.NotNullViolation):
            async with self.conn.transaction():  # savepoint keeps fixture usable
                await self.conn.execute("INSERT INTO storefront.orders VALUES (7, NULL, 'shipped')")
        # Hypothetical nullable schema only, rolled back with the fixture.
        await self.conn.execute("ALTER TABLE storefront.orders ALTER COLUMN customer_id DROP NOT NULL")
        await self.conn.execute("INSERT INTO storefront.orders VALUES (7, NULL, 'shipped')")
        self.assertEqual(await self.repo.awaiting_shipment_count(10), 3)
        cursor = await self.conn.execute("""
            SELECT count(*) FROM storefront.customers c
            WHERE c.created_at > now() - make_interval(days => %s)
              AND c.customer_id NOT IN (
                  SELECT customer_id FROM storefront.orders WHERE status = 'shipped')
        """, (10,))
        self.assertEqual((await cursor.fetchone())[0], 0)
