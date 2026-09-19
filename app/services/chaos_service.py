"""ChaosService — game-day fault drills, and the recovery path.

Owns the *policy* of each fault (how many locks, how many connections, how much
churn) and the guarantee that HEAL reverses every fault. Depends only on the
FaultInjectionPort; it never opens a connection itself.
"""

from __future__ import annotations

from app.ports.faults import FaultInjectionPort
from app.services.models import FaultResult

# One entry per button. Keeping the catalog here (not in the web layer) keeps
# the fault policy in the service.
FAULTS = (
    "missing_index", "lock_storm", "connection_flood", "runaway_query", "bloat",
    "reservation_storm", "reservation_index",
)


class ChaosService:
    def __init__(
        self,
        faults: FaultInjectionPort,
        *,
        lock_products: int = 25,
        flood_connections: int = 80,
        runaway_count: int = 4,
        churn_batches: int = 40,
        churn_rows: int = 5000,
        storm_workers: int = 8,
        storm_hot_products: int = 50,
        storm_seconds: int = 600,
    ) -> None:
        self._f = faults
        self._lock_products = lock_products
        self._flood_connections = flood_connections
        self._runaway_count = runaway_count
        self._churn_batches = churn_batches
        self._churn_rows = churn_rows
        self._storm_workers = storm_workers
        self._storm_hot_products = storm_hot_products
        self._storm_seconds = storm_seconds

    async def inject(self, fault: str) -> FaultResult:
        if fault == "missing_index":
            await self._f.drop_hot_index()
            return FaultResult(fault, "injected", "Dropped ix_orders_customer — "
                               "customer order-history lookups now sequential-scan.")
        if fault == "lock_storm":
            n = await self._f.hold_row_locks(self._lock_products)
            return FaultResult(fault, "injected",
                               f"Holding row locks on {n} inventory rows — "
                               "checkouts touching them will block.")
        if fault == "connection_flood":
            n = await self._f.open_idle_connections(self._flood_connections)
            return FaultResult(fault, "injected",
                               f"Opened {n} idle-in-transaction connections — "
                               "eating the connection pool.")
        if fault == "runaway_query":
            n = await self._f.start_runaway_queries(self._runaway_count)
            return FaultResult(fault, "injected",
                               f"Launched {n} unbounded cross-join queries — pinning CPU.")
        if fault == "bloat":
            await self._f.set_autovacuum(False)
            churned = await self._f.churn_rows(self._churn_batches, self._churn_rows)
            return FaultResult(fault, "injected",
                               f"Autovacuum off + churned {churned:,} page_views rows — "
                               "dead tuples accumulating.")
        if fault == "reservation_storm":
            n = await self._f.start_reservation_storm(
                self._storm_workers, self._storm_hot_products, self._storm_seconds
            )
            return FaultResult(fault, "injected",
                               f"Started {n} concurrent reservation workers (SERIALIZABLE); "
                               "refused transactions are counted and retried.")
        if fault == "reservation_index":
            created = await self._f.create_reservation_index()
            return FaultResult(fault, "fixed",
                               "Created ix_inventory_quantity CONCURRENTLY on "
                               "inventory(quantity_available) and analyzed the table."
                               if created else "ix_inventory_quantity already exists.")
        raise ValueError(f"unknown fault: {fault}")

    async def reservation_stats(self):
        return await self._f.reservation_stats()

    async def heal(self) -> list[FaultResult]:
        """Reverse every fault. Safe to call when nothing is broken — each
        restore is a no-op if its fault was never injected.
        """
        results: list[FaultResult] = []

        restored = await self._f.restore_hot_index()
        if restored:
            results.append(FaultResult("missing_index", "healed",
                                       "Recreated ix_orders_customer."))

        await self._f.release_row_locks()
        results.append(FaultResult("lock_storm", "healed", "Released held row locks."))

        closed = await self._f.close_idle_connections()
        results.append(FaultResult("connection_flood", "healed",
                                   f"Closed {closed} leaked connections."))

        killed = await self._f.cancel_runaway_queries()
        results.append(FaultResult("runaway_query", "healed",
                                   f"Cancelled {killed} runaway queries."))

        await self._f.set_autovacuum(True)
        await self._f.reclaim_bloat()
        results.append(FaultResult("bloat", "healed",
                                   "Autovacuum on + VACUUM ANALYZE reclaimed bloat."))

        stats = await self._f.stop_reservation_storm()
        await self._f.drop_reservation_index()
        results.append(FaultResult("reservation_storm", "healed",
                                   f"Stopped reservation workers ({stats.commits:,} committed, "
                                   f"{stats.serialization_failures:,} serialization failures); "
                                   "dropped ix_inventory_quantity."))

        return results
