"""Port: induce the metric conditions that fire DBGorilla's default alerts.

Each method starts a self-terminating background "pressure campaign" against the
database. They run for a bounded duration (long enough to clear an alert's
`for:` window) and clean themselves up. All campaigns are cancellable.
"""

from __future__ import annotations

from typing import Protocol


class AlertPressurePort(Protocol):
    async def active_connection_pressure(self, count: int, hold_seconds: int) -> int:
        """Open `count` connections each running a long query (state=active),
        held for `hold_seconds`. Drives pg_stat_active_connections_total."""
        ...

    async def slow_query_pressure(
        self, concurrency: int, sleep_seconds: float, duration_seconds: int
    ) -> None:
        """Run `concurrency` slow queries back-to-back for `duration_seconds`.
        Drives pg_stat_statements mean/total exec time."""
        ...

    async def qps_pressure(self, target_qps: int, duration_seconds: int) -> None:
        """Fire cheap queries at a high rate. Drives pg_stat_statements calls."""
        ...

    async def transaction_pressure(self, target_tps: int, duration_seconds: int) -> None:
        """Commit many tiny transactions. Drives xact_commit / n_total_transactions."""
        ...

    async def n_plus_one_pressure(self, duration_seconds: int) -> None:
        """Repeatedly run the classic N+1: one query to list recent orders, then
        one query per order. Surfaces as a single statement called N times."""
        ...

    def active_campaigns(self) -> dict[str, float]:
        """Map of campaign name -> seconds remaining, for the UI."""
        ...

    async def stop_all(self) -> None:
        ...
