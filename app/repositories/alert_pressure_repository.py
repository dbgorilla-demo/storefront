"""Postgres adapter implementing AlertPressurePort.

Each campaign is a self-terminating background task that applies a specific kind
of load until its deadline, then cleans up. Connections opened here deliberately
escape the app pool (they ARE the pressure) and are tagged by application_name so
stop_all can terminate any strays.
"""

from __future__ import annotations

import asyncio
import time

from psycopg_pool import AsyncConnectionPool

_APP_ACTIVE = "dbg-demo-alert-active-conns"
_APP_SLOW = "dbg-demo-alert-slow"
_APP_QPS = "dbg-demo-alert-qps"
_APP_TXN = "dbg-demo-alert-txn"
_APP_NPLUS1 = "dbg-demo-alert-nplus1"

# Campaigns run their load on their OWN bounded connections, never the app pool,
# so the pool stays free for health/reads/traffic. Kept small so all campaigns
# plus the app pool and collector fit comfortably under max_connections.
_WORKERS = {"slow": 4, "qps": 5, "txn": 5, "nplus1": 2}


class PostgresAlertPressureRepository:
    def __init__(self, pool: AsyncConnectionPool, dsn: str) -> None:
        self._pool = pool
        self._dsn = dsn
        # name -> (task, deadline_epoch)
        self._campaigns: dict[str, tuple[asyncio.Task, float]] = {}

    async def _load_worker(self, appname: str, deadline: float, body) -> None:
        """Run `body(conn)` in a loop on a dedicated direct connection until the
        deadline. Deliberately does NOT use the app pool — campaign load must not
        starve the pool the app serves health/reads/traffic from. Tagged by
        application_name so stop_all / the startup reaper can terminate it."""
        import psycopg

        conn = None
        try:
            conn = await psycopg.AsyncConnection.connect(
                self._dsn, application_name=appname, autocommit=True
            )
            while time.monotonic() < deadline:
                try:
                    await body(conn)
                except Exception:  # noqa: BLE001 — a broken db mid-incident is expected
                    await asyncio.sleep(0.3)
        except Exception:  # noqa: BLE001 — couldn't open (e.g. at max_connections)
            pass
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---- campaign bookkeeping -------------------------------------------

    def _register(self, name: str, coro, duration_seconds: int) -> None:
        self._cancel(name)
        deadline = time.monotonic() + duration_seconds
        task = asyncio.create_task(coro)
        self._campaigns[name] = (task, deadline)

    def _cancel(self, name: str) -> None:
        existing = self._campaigns.pop(name, None)
        if existing:
            existing[0].cancel()

    def active_campaigns(self) -> dict[str, float]:
        now = time.monotonic()
        out: dict[str, float] = {}
        for name, (task, deadline) in list(self._campaigns.items()):
            if task.done():
                self._campaigns.pop(name, None)
                continue
            out[name] = max(0.0, round(deadline - now, 0))
        return out

    # ---- active-connection pressure -------------------------------------

    async def active_connection_pressure(self, count: int, hold_seconds: int) -> int:
        self._register(
            "active_connections",
            self._hold_active_connections(count, hold_seconds),
            hold_seconds,
        )
        return count

    async def _hold_active_connections(self, count: int, hold_seconds: int) -> None:
        import psycopg

        conns: list = []
        try:
            for _ in range(count):
                try:
                    c = await psycopg.AsyncConnection.connect(
                        self._dsn, application_name=_APP_ACTIVE, autocommit=True
                    )
                except Exception:  # noqa: BLE001 — expected once we hit the ceiling
                    break
                conns.append(c)
            # Keep every connection in state=active by looping a sleep query.
            async def keep_active(c):
                while True:
                    await c.execute("SELECT pg_sleep(2)")

            keepers = [asyncio.create_task(keep_active(c)) for c in conns]
            try:
                await asyncio.sleep(hold_seconds)
            finally:
                for k in keepers:
                    k.cancel()
        finally:
            for c in conns:
                try:
                    await c.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---- slow-query pressure --------------------------------------------

    async def slow_query_pressure(
        self, concurrency: int, sleep_seconds: float, duration_seconds: int
    ) -> None:
        self._register(
            "slow_query",
            self._run_slow_queries(concurrency, sleep_seconds, duration_seconds),
            duration_seconds,
        )

    async def _run_slow_queries(
        self, concurrency: int, sleep_seconds: float, duration_seconds: int
    ) -> None:
        deadline = time.monotonic() + duration_seconds

        async def body(conn):
            # A genuinely expensive query: sleep + a real scan so it shows up in
            # pg_stat_statements as high mean exec time.
            await conn.execute(
                "SELECT pg_sleep(%s), count(*) FROM storefront.orders o "
                "JOIN storefront.order_items i ON i.order_id = o.order_id",
                (sleep_seconds,),
            )

        n = min(concurrency, _WORKERS["slow"])
        await asyncio.gather(
            *[self._load_worker(_APP_SLOW, deadline, body) for _ in range(n)]
        )

    # ---- qps pressure ---------------------------------------------------

    async def qps_pressure(self, target_qps: int, duration_seconds: int) -> None:
        self._register(
            "qps",
            self._run_qps(target_qps, duration_seconds),
            duration_seconds,
        )

    async def _run_qps(self, target_qps: int, duration_seconds: int) -> None:
        deadline = time.monotonic() + duration_seconds
        # A few workers each hammering a cheap indexed lookup back-to-back — a
        # handful of connections easily produces a large QPS spike.
        workers = min(target_qps, _WORKERS["qps"])

        async def body(conn):
            await conn.execute(
                "SELECT product_id FROM storefront.products WHERE product_id = %s",
                (1 + int(time.monotonic() * 1000) % 5000,),
            )

        await asyncio.gather(
            *[self._load_worker(_APP_QPS, deadline, body) for _ in range(workers)]
        )

    # ---- transaction pressure -------------------------------------------

    async def transaction_pressure(self, target_tps: int, duration_seconds: int) -> None:
        self._register(
            "transactions",
            self._run_transactions(target_tps, duration_seconds),
            duration_seconds,
        )

    async def _run_transactions(self, target_tps: int, duration_seconds: int) -> None:
        deadline = time.monotonic() + duration_seconds
        workers = min(target_tps, _WORKERS["txn"])

        async def body(conn):
            # Each explicit commit bumps xact_commit / n_total_transactions.
            async with conn.transaction():
                await conn.execute("SELECT 1")

        await asyncio.gather(
            *[self._load_worker(_APP_TXN, deadline, body) for _ in range(workers)]
        )

    # ---- N+1 pressure ---------------------------------------------------

    async def n_plus_one_pressure(self, duration_seconds: int) -> None:
        self._register(
            "n_plus_one",
            self._run_n_plus_one(duration_seconds),
            duration_seconds,
        )

    async def _run_n_plus_one(self, duration_seconds: int) -> None:
        deadline = time.monotonic() + duration_seconds

        async def body(conn):
            # The "1": list recent orders.
            cur = await conn.execute(
                "SELECT order_id FROM storefront.orders "
                "ORDER BY placed_at DESC LIMIT 50"
            )
            order_ids = [r[0] for r in await cur.fetchall()]
            # The "N": one round-trip per order instead of a join. Each is the
            # SAME normalized statement, so pg_stat_statements shows it with a
            # call count in the thousands.
            for oid in order_ids:
                await conn.execute(
                    "SELECT o.order_id, o.total_cents, o.status, "
                    "       (SELECT count(*) FROM storefront.order_items i "
                    "         WHERE i.order_id = o.order_id) AS item_count "
                    "  FROM storefront.orders o WHERE o.order_id = %s",
                    (oid,),
                )

        await asyncio.gather(
            *[self._load_worker(_APP_NPLUS1, deadline, body)
              for _ in range(_WORKERS["nplus1"])]
        )

    # ---- stop -----------------------------------------------------------

    async def stop_all(self) -> None:
        for name in list(self._campaigns):
            self._cancel(name)
        # Sweep any campaign connections (all tagged dbg-demo-alert%) still open.
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND application_name LIKE 'dbg-demo-alert%%' "
                    "AND pid <> pg_backend_pid()"
                )
        except Exception:  # noqa: BLE001
            pass
