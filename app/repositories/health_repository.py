"""Postgres adapter implementing DatabaseHealthPort.

Reads the live catalog/stats views into a storage-agnostic snapshot. Pure reads;
no state changes here.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.repositories.models import BlockedSession, DatabaseSnapshot


class PostgresHealthRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def snapshot(self) -> DatabaseSnapshot:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM pg_stat_activity
                       WHERE datname = current_database()),
                    (SELECT setting::int FROM pg_settings WHERE name='max_connections'),
                    (SELECT count(*) FROM pg_stat_activity
                       WHERE datname = current_database() AND state='active'),
                    (SELECT count(*) FROM pg_stat_activity
                       WHERE datname = current_database()
                         AND state='idle in transaction'),
                    (SELECT COALESCE(EXTRACT(EPOCH FROM max(now()-query_start)),0)
                       FROM pg_stat_activity
                       WHERE datname = current_database() AND state='active'),
                    (SELECT COALESCE(sum(n_dead_tup),0)
                       FROM pg_stat_user_tables WHERE schemaname='storefront'),
                    (SELECT COALESCE(sum(n_live_tup),0)
                       FROM pg_stat_user_tables WHERE schemaname='storefront'),
                    pg_database_size(current_database()),
                    EXISTS (SELECT 1 FROM pg_indexes
                       WHERE schemaname='storefront'
                         AND indexname='ix_orders_customer'),
                    COALESCE((SELECT NOT (reloptions::text LIKE '%autovacuum_enabled=false%')
                       FROM pg_class WHERE oid='storefront.page_views'::regclass), true)
                """
            )
            row = await cur.fetchone()

            cur = await conn.execute(
                """
                SELECT
                    blocked.pid,
                    blocked.query,
                    blocking.pid,
                    blocking.query,
                    EXTRACT(EPOCH FROM now() - blocked.query_start)
                FROM pg_stat_activity blocked
                JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bpid ON true
                JOIN pg_stat_activity blocking ON blocking.pid = bpid
                WHERE blocked.datname = current_database()
                LIMIT 20
                """
            )
            blocked = tuple(
                BlockedSession(
                    blocked_pid=r[0],
                    blocked_query=(r[1] or "")[:200],
                    blocking_pid=r[2],
                    blocking_query=(r[3] or "")[:200],
                    wait_seconds=float(r[4] or 0),
                )
                for r in await cur.fetchall()
            )

        return DatabaseSnapshot(
            total_connections=row[0],
            max_connections=row[1],
            active_queries=row[2],
            idle_in_transaction=row[3],
            longest_query_seconds=float(row[4]),
            dead_tuples=int(row[5]),
            live_tuples=int(row[6]),
            database_bytes=int(row[7]),
            hot_index_present=bool(row[8]),
            autovacuum_enabled=bool(row[9]),
            blocked_sessions=blocked,
        )
