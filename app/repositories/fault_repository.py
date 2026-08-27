"""Postgres adapter implementing FaultInjectionPort.

Every fault does the real thing to the real database, and every fault has a
paired restore. To make faults that outlive a single request (idle connections,
runaway queries) the adapter tags them with a known application_name and later
finds them via pg_stat_activity, so a HEAL can clean up connections this process
no longer holds a handle to.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from psycopg_pool import AsyncConnectionPool

from app.repositories.models import ReservationStats

_HOT_INDEX = "ix_orders_customer"
_HOT_INDEX_DDL = (
    "CREATE INDEX ix_orders_customer ON storefront.orders(customer_id)"
)

# application_name tags — the handle we use to find and kill our own chaos.
_APP_IDLE = "dbg-demo-chaos-idle"
_APP_RUNAWAY = "dbg-demo-chaos-runaway"
_APP_LOCKER = "dbg-demo-chaos-locker"
_APP_RESERVE = "dbg-demo-chaos-reserve"
_log = logging.getLogger(__name__)

_RESERVATION_INDEX = "ix_inventory_quantity"
_RESERVATION_INDEX_DDL = (
    "CREATE INDEX ix_inventory_quantity "
    "ON storefront.inventory(quantity_available)"
)
# A reservation first counts the lines that still carry deep stock (the
# free-shipping promo is only offered while enough do), then adjusts one
# product's stock. "Deep" is the top percentile of stock levels at the time
# the reservations start.
_DEEP_STOCK_PERCENTILE = 0.99


class PostgresFaultRepository:
    def __init__(self, pool: AsyncConnectionPool, dsn: str) -> None:
        self._pool = pool
        # Raw DSN needed to open connections that deliberately escape the pool
        # (idle-connection flood, background lock holder). These are the fault,
        # so they cannot come from the well-behaved pool.
        self._dsn = dsn
        self._idle_conns: list = []
        self._idle_autoclose: asyncio.Task | None = None
        self._lock_task: asyncio.Task | None = None
        self._lock_stop = asyncio.Event()
        self._reserve_tasks: list[asyncio.Task] = []
        self._reserve_stop = asyncio.Event()
        self._reserve_started: float = 0.0
        self._reserve_counts = {"attempts": 0, "commits": 0, "failures": 0}
        self._deep_threshold = 0

    # ---- missing index ---------------------------------------------------

    async def drop_hot_index(self) -> bool:
        async with self._pool.connection() as conn:
            await conn.execute(f"DROP INDEX IF EXISTS storefront.{_HOT_INDEX}")
        return True

    async def restore_hot_index(self) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='storefront' "
                "AND indexname=%s",
                (_HOT_INDEX,),
            )
            if await cur.fetchone():
                return False
            await conn.execute(_HOT_INDEX_DDL)
        return True

    # ---- lock contention -------------------------------------------------

    async def hold_row_locks(self, product_count: int) -> int:
        """Open a background connection that SELECT ... FOR UPDATE-locks the top
        inventory rows and holds the transaction open until released. Real
        checkouts touching those products then queue behind it.
        """
        if self._lock_task and not self._lock_task.done():
            return 0
        self._lock_stop = asyncio.Event()
        started: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        self._lock_task = asyncio.create_task(
            self._hold_locks(product_count, started)
        )
        return await started

    async def _hold_locks(
        self, product_count: int, started: asyncio.Future
    ) -> None:
        import psycopg

        try:
            conn = await psycopg.AsyncConnection.connect(
                self._dsn, application_name=_APP_LOCKER, autocommit=False
            )
        except Exception as e:  # noqa: BLE001
            if not started.done():
                started.set_exception(e)
            return
        try:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT product_id FROM storefront.inventory "
                    "ORDER BY product_id LIMIT %s FOR UPDATE",
                    (product_count,),
                )
                locked = len(await cur.fetchall())
                if not started.done():
                    started.set_result(locked)
                # Hold the locks open until released or a safety cap elapses.
                try:
                    await asyncio.wait_for(self._lock_stop.wait(), timeout=600)
                except asyncio.TimeoutError:
                    pass
        finally:
            await conn.close()

    async def release_row_locks(self) -> None:
        self._lock_stop.set()
        if self._lock_task:
            try:
                await asyncio.wait_for(self._lock_task, timeout=10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._lock_task.cancel()
            self._lock_task = None

    # ---- connection exhaustion ------------------------------------------

    async def open_idle_connections(self, count: int) -> int:
        import psycopg

        opened = 0
        for _ in range(count):
            try:
                conn = await psycopg.AsyncConnection.connect(
                    self._dsn, application_name=_APP_IDLE, autocommit=True
                )
                # idle-in-transaction is nastier signal than plain idle
                await conn.execute("BEGIN")
                self._idle_conns.append(conn)
                opened += 1
            except Exception:  # noqa: BLE001 — we WANT to hit max_connections
                break
        # Safety net: auto-release after a window even if HEAL is never pressed,
        # so a flood can't permanently pin connections against max_connections.
        self._schedule_idle_autoclose(300)
        return opened

    def _schedule_idle_autoclose(self, after: int) -> None:
        if self._idle_autoclose and not self._idle_autoclose.done():
            self._idle_autoclose.cancel()

        async def _expire():
            try:
                await asyncio.sleep(after)
                await self.close_idle_connections()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass

        self._idle_autoclose = asyncio.create_task(_expire())

    async def close_idle_connections(self) -> int:
        # Close the ones we still hold, then sweep any our tag left behind.
        closed = 0
        for conn in self._idle_conns:
            try:
                await conn.close()
                closed += 1
            except Exception:  # noqa: BLE001
                pass
        self._idle_conns.clear()
        closed += await self._terminate_by_appname(_APP_IDLE)
        return closed

    # ---- runaway queries -------------------------------------------------

    async def start_runaway_queries(self, count: int) -> int:
        import psycopg

        started = 0
        for _ in range(count):
            try:
                conn = await psycopg.AsyncConnection.connect(
                    self._dsn, application_name=_APP_RUNAWAY, autocommit=True
                )
            except Exception:  # noqa: BLE001
                break
            # Fire-and-forget an unbounded cross join. It pins a CPU and will
            # not finish; we reap it by application_name later.
            asyncio.create_task(self._run_runaway(conn))
            started += 1
        return started

    @staticmethod
    async def _run_runaway(conn) -> None:
        try:
            await conn.execute(
                "SELECT count(*) FROM storefront.orders a, storefront.orders b, "
                "storefront.order_items c WHERE a.total_cents > b.total_cents"
            )
        except Exception:  # noqa: BLE001 — cancelled on heal, expected
            pass
        finally:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def cancel_runaway_queries(self) -> int:
        return await self._terminate_by_appname(_APP_RUNAWAY)

    # ---- bloat -----------------------------------------------------------

    async def set_autovacuum(self, enabled: bool) -> None:
        # Storage parameters can't be bound as placeholders; the value is a
        # server-controlled boolean, not user input, so inlining is safe.
        flag = "true" if enabled else "false"
        async with self._pool.connection() as conn:
            await conn.execute(
                f"ALTER TABLE storefront.page_views SET (autovacuum_enabled = {flag})"
            )

    async def churn_rows(self, batches: int, rows_per_batch: int) -> int:
        """Insert then delete high volumes of page_views. With autovacuum off
        the deleted rows become dead tuples that never get reclaimed — visible
        bloat.
        """
        churned = 0
        async with self._pool.connection() as conn:
            for _ in range(batches):
                await conn.execute(
                    "INSERT INTO storefront.page_views (customer_id, product_id) "
                    "SELECT 1 + (g %% 8000), 1 + (g %% 5000) "
                    "FROM generate_series(1, %s) g",
                    (rows_per_batch,),
                )
                await conn.execute(
                    "DELETE FROM storefront.page_views "
                    "WHERE page_view_id IN ("
                    "  SELECT page_view_id FROM storefront.page_views "
                    "  ORDER BY page_view_id DESC LIMIT %s)",
                    (rows_per_batch,),
                )
                churned += rows_per_batch
        return churned

    async def reclaim_bloat(self) -> None:
        # VACUUM cannot run inside a transaction block; use an autocommit conn.
        async with self._pool.connection() as conn:
            await conn.set_autocommit(True)
            await conn.execute("VACUUM (ANALYZE) storefront.page_views")

    # ---- reservation conflicts -------------------------------------------

    async def start_reservation_storm(
        self, workers: int, hot_products: int, hold_seconds: int
    ) -> int:
        """Run `workers` concurrent reservation transactions for up to
        `hold_seconds`: the checkout reservation flow (deep-stock guard, then
        one product's stock adjusted) against `hot_products` low-stock
        products, at the SERIALIZABLE isolation the reservation requires. A
        transaction Postgres refuses to commit is counted and retried, as the
        checkout does.
        """
        if any(not t.done() for t in self._reserve_tasks):
            return 0
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT percentile_disc(%s) WITHIN GROUP (ORDER BY quantity_available) "
                "FROM storefront.inventory",
                (_DEEP_STOCK_PERCENTILE,),
            )
            row = await cur.fetchone()
            self._deep_threshold = int(row[0] or 0)
            cur = await conn.execute(
                "SELECT product_id FROM storefront.inventory "
                "ORDER BY quantity_available, product_id LIMIT %s",
                (hot_products,),
            )
            hot_ids = [r[0] for r in await cur.fetchall()]
        if not hot_ids:
            return 0
        self._reserve_stop = asyncio.Event()
        self._reserve_counts = {"attempts": 0, "commits": 0, "failures": 0}
        self._reserve_started = time.monotonic()
        self._reserve_tasks = [
            asyncio.create_task(self._reserve_loop(i, hot_ids, hold_seconds))
            for i in range(workers)
        ]
        return workers

    async def _reserve_loop(self, worker: int, hot_ids: list[int], hold_seconds: int) -> None:
        import psycopg
        from psycopg import IsolationLevel
        from psycopg.errors import SerializationFailure

        try:
            conn = await psycopg.AsyncConnection.connect(
                self._dsn, application_name=_APP_RESERVE, autocommit=False
            )
            await conn.set_isolation_level(IsolationLevel.SERIALIZABLE)
        except Exception:  # noqa: BLE001
            _log.exception("reservation worker %d could not connect", worker)
            return
        deadline = time.monotonic() + hold_seconds
        counts = self._reserve_counts
        try:
            while not self._reserve_stop.is_set() and time.monotonic() < deadline:
                product_id = random.choice(hot_ids)
                delta = 1 if (counts["attempts"] + worker) % 2 == 0 else -1
                counts["attempts"] += 1
                try:
                    async with conn.transaction():
                        await conn.execute(
                            "SELECT count(*) FROM storefront.inventory "
                            "WHERE quantity_available > %s",
                            (self._deep_threshold,),
                        )
                        await conn.execute(
                            "UPDATE storefront.inventory "
                            "SET quantity_available = greatest(quantity_available + %s, 0), "
                            "    updated_at = now() "
                            "WHERE product_id = %s",
                            (delta, product_id),
                        )
                    counts["commits"] += 1
                except SerializationFailure:
                    counts["failures"] += 1
                except Exception:  # noqa: BLE001 — connection lost on heal, expected
                    _log.exception("reservation worker %d stopped", worker)
                    break
                await asyncio.sleep(0.005)
        finally:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def stop_reservation_storm(self) -> ReservationStats:
        self._reserve_stop.set()
        for t in self._reserve_tasks:
            try:
                await asyncio.wait_for(t, timeout=10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                t.cancel()
        self._reserve_tasks = []
        await self._terminate_by_appname(_APP_RESERVE)
        return await self.reservation_stats()

    async def reservation_stats(self) -> ReservationStats:
        running = any(not t.done() for t in self._reserve_tasks)
        elapsed = (time.monotonic() - self._reserve_started) if self._reserve_started else 0.0
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='storefront' AND indexname=%s",
                (_RESERVATION_INDEX,),
            )
            present = (await cur.fetchone()) is not None
        c = self._reserve_counts
        return ReservationStats(
            running=running,
            workers=sum(1 for t in self._reserve_tasks if not t.done()),
            attempts=c["attempts"],
            commits=c["commits"],
            serialization_failures=c["failures"],
            elapsed_seconds=round(elapsed, 1),
            index_present=present,
        )

    async def create_reservation_index(self) -> bool:
        import psycopg

        # CONCURRENTLY cannot run inside a transaction and does not block
        # reservations while it builds — so a dedicated autocommit connection,
        # not one from the pool.
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='storefront' AND indexname=%s",
                (_RESERVATION_INDEX,),
            )
            if await cur.fetchone():
                return False
            await conn.execute(_RESERVATION_INDEX_DDL.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY"))
            await conn.execute("ANALYZE storefront.inventory")
        return True

    async def drop_reservation_index(self) -> bool:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS storefront.{_RESERVATION_INDEX}")
        return True

    # ---- shared ----------------------------------------------------------

    async def _terminate_by_appname(self, appname: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM ("
                "  SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "  WHERE application_name = %s AND pid <> pg_backend_pid()"
                ") t",
                (appname,),
            )
            row = await cur.fetchone()
            return row[0] if row else 0
