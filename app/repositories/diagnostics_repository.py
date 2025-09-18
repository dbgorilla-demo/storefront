"""Postgres adapter implementing DiagnosticsPort. Read-only introspection."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.repositories.models import ActiveSession, StatementStat


class PostgresDiagnosticsRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def active_sessions(self, limit: int) -> list[ActiveSession]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT pid, state,
                       EXTRACT(EPOCH FROM now() - query_start) AS dur,
                       wait_event_type, wait_event, query, application_name
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND pid <> pg_backend_pid()
                   AND state IN ('active', 'idle in transaction')
                   AND query <> ''
                 ORDER BY query_start ASC
                 LIMIT %s
                """,
                (limit,),
            )
            return [
                ActiveSession(
                    pid=r[0],
                    state=r[1],
                    duration_seconds=float(r[2] or 0),
                    wait_event_type=r[3],
                    wait_event=r[4],
                    query=" ".join((r[5] or "").split())[:400],
                    application_name=r[6] or "",
                )
                for r in await cur.fetchall()
            ]

    async def top_statements(self, limit: int) -> list[StatementStat]:
        # Only storefront workload; skip our own introspection and catalog reads.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT s.query, s.calls, s.total_exec_time, s.mean_exec_time, s.rows
                  FROM pg_stat_statements s
                  JOIN pg_database d ON d.oid = s.dbid
                 WHERE d.datname = current_database()
                   AND s.query ILIKE '%%storefront.%%'
                   AND s.query NOT ILIKE '%%pg_stat_%%'
                 ORDER BY s.total_exec_time DESC
                 LIMIT %s
                """,
                (limit,),
            )
            return [
                StatementStat(
                    query=" ".join((r[0] or "").split())[:400],
                    calls=r[1],
                    total_exec_ms=float(r[2] or 0),
                    mean_exec_ms=float(r[3] or 0),
                    rows=int(r[4] or 0),
                )
                for r in await cur.fetchall()
            ]

    async def connections_by_state(self) -> list[tuple[str, int]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT COALESCE(state, 'unknown') AS state, count(*)
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND pid <> pg_backend_pid()
                 GROUP BY 1
                 ORDER BY 2 DESC
                """
            )
            return [(r[0], int(r[1])) for r in await cur.fetchall()]
