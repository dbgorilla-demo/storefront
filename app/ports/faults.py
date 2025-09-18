"""Port: deliberately break the database, and put it back.

Each fault is a matched pair — something that causes the damage and something
that undoes it. Nothing here is simulated; every method does the real thing to
a real database, so recovery drills exercise the genuine failure modes.
"""

from __future__ import annotations

from typing import Protocol


class FaultInjectionPort(Protocol):
    # --- missing index: the hot lookup path loses its index and falls to a seq scan
    async def drop_hot_index(self) -> bool: ...

    async def restore_hot_index(self) -> bool: ...

    # --- lock contention: hold row locks so checkouts queue up behind us
    async def hold_row_locks(self, product_count: int) -> int: ...

    async def release_row_locks(self) -> None: ...

    # --- connection exhaustion: eat the connection slots
    async def open_idle_connections(self, count: int) -> int: ...

    async def close_idle_connections(self) -> int: ...

    # --- runaway query: unbounded joins that pin CPU and never finish on their own
    async def start_runaway_queries(self, count: int) -> int: ...

    async def cancel_runaway_queries(self) -> int: ...

    # --- bloat: churn rows with autovacuum off so dead tuples pile up
    async def set_autovacuum(self, enabled: bool) -> None: ...

    async def churn_rows(self, batches: int, rows_per_batch: int) -> int: ...

    async def reclaim_bloat(self) -> None: ...
