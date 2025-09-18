"""HealthService — turns a raw DatabaseSnapshot into a business-judged view."""

from __future__ import annotations

from app.ports.health import DatabaseHealthPort
from app.services.models import HealthView


class HealthService:
    def __init__(self, health: DatabaseHealthPort) -> None:
        self._health = health

    async def current(self) -> HealthView:
        s = await self._health.snapshot()
        conn_pct = (
            100.0 * s.total_connections / s.max_connections
            if s.max_connections
            else 0.0
        )
        total_tuples = s.dead_tuples + s.live_tuples
        dead_pct = 100.0 * s.dead_tuples / total_tuples if total_tuples else 0.0

        # Business rules for the headline status light.
        status = "healthy"
        if (
            conn_pct >= 70
            or s.blocked_sessions
            or s.longest_query_seconds >= 10
            or dead_pct >= 20
            or not s.hot_index_present
        ):
            status = "degraded"
        if (
            conn_pct >= 90
            or len(s.blocked_sessions) >= 5
            or s.longest_query_seconds >= 30
            or dead_pct >= 40
        ):
            status = "critical"

        return HealthView(
            connections_used=s.total_connections,
            connections_max=s.max_connections,
            connection_pct=round(conn_pct, 1),
            active_queries=s.active_queries,
            idle_in_transaction=s.idle_in_transaction,
            blocked_count=len(s.blocked_sessions),
            longest_query_seconds=round(s.longest_query_seconds, 1),
            dead_tuple_pct=round(dead_pct, 1),
            database_mb=round(s.database_bytes / 1_048_576, 1),
            hot_index_present=s.hot_index_present,
            autovacuum_enabled=s.autovacuum_enabled,
            status=status,
        )
