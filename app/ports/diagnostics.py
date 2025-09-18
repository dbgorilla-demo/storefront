"""Port: read what the database is executing, for the live-query views."""

from __future__ import annotations

from typing import Protocol

from app.repositories.models import ActiveSession, StatementStat


class DiagnosticsPort(Protocol):
    async def active_sessions(self, limit: int) -> list[ActiveSession]:
        """Backends currently running a statement or holding a transaction open."""
        ...

    async def top_statements(self, limit: int) -> list[StatementStat]:
        """Storefront queries ranked by total execution time (pg_stat_statements)."""
        ...

    async def connections_by_state(self) -> list[tuple[str, int]]:
        """Backend counts grouped by pg_stat_activity.state, highest first."""
        ...
