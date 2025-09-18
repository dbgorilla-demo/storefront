"""TrafficService — drives constant, realistic storefront load.

Depends only on the StorefrontRepository port. It owns the *decision* of what a
plausible customer session looks like (browse-heavy, occasional checkout) and
the pacing; it knows nothing about SQL or connections.
"""

from __future__ import annotations

import asyncio
import random

from app.ports.storefront import StorefrontRepository
from app.repositories.models import OrderLine, WorkloadSample


class TrafficService:
    # Weighted mix of a realistic day at the storefront. Most of it is healthy
    # traffic across the features a real storefront accumulates: search,
    # analytics, the fulfilment queue, and the marketing and ops reports.
    # under deadline.
    _MIX = (
        ("browse", 28),
        ("view_product", 18),
        ("search", 10),
        ("proc_search", 4),
        ("checkout", 8),           # also awards loyalty points via a stored proc
        ("order_history", 8),
        ("fulfilment_queue", 7),
        ("customer_ltv", 5),
        ("sales_analytics", 5),
        ("awaiting_shipment", 3),
        ("inventory_audit", 3),
        ("revenue_report", 1),
    )

    def __init__(self, storefront: StorefrontRepository, qps: float) -> None:
        self._store = storefront
        self._qps = qps
        self._sample: WorkloadSample | None = None
        self._running = False
        self._paused = False
        self.ok = 0
        self.errors = 0

    def pause(self) -> None:
        """Temporarily stop issuing traffic (used while an alert campaign needs
        the slow queries to dominate pg_stat_statements averages)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def run_forever(self) -> None:
        self._running = True
        # The app can start before the DB primary accepts connections (e.g. a
        # single-shot `helm install`). Keep retrying the initial sample load
        # instead of letting the traffic task die — a failure here must not
        # silently stop all traffic for the life of the pod.
        while self._running and self._sample is None:
            try:
                self._sample = await self._store.sample_workload_ids(limit=200)
            except Exception:  # noqa: BLE001 — DB not ready yet; wait and retry
                await asyncio.sleep(3)
        if not self._running:
            return
        delay = 1.0 / self._qps if self._qps > 0 else 0.5
        # Refresh the id sample periodically so newly-created orders enter rotation.
        refresh_after = max(50, int(self._qps * 60))
        n = 0
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue
            try:
                await self._one_action()
                self.ok += 1
            except Exception:  # noqa: BLE001 — a broken db is expected mid-incident
                self.errors += 1
            n += 1
            if n % refresh_after == 0:
                try:
                    self._sample = await self._store.sample_workload_ids(limit=200)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(delay * random.uniform(0.5, 1.5))

    def stop(self) -> None:
        self._running = False

    async def _one_action(self) -> None:
        s = self._sample
        assert s is not None
        action = self._pick()
        if action == "browse":
            await self._store.browse_category(random.choice(s.category_ids), 20)
        elif action == "search":
            await self._store.search_products(random.choice(s.search_terms), 20)
        elif action == "view_product":
            pid = random.choice(s.product_ids)
            await self._store.get_product(pid)
            await self._store.record_page_view(random.choice(s.customer_ids), pid)
        elif action == "order_history":
            await self._store.customer_order_history(random.choice(s.customer_ids), 10)
        elif action == "revenue_report":
            await self._store.revenue_report(30)
        elif action == "checkout":
            await self._checkout(s)
        elif action == "fulfilment_queue":
            await self._fulfilment_queue()
        elif action == "sales_analytics":
            await self._store.sales_by_month(2026)
        elif action == "awaiting_shipment":
            await self._store.awaiting_shipment_count(90)
        elif action == "inventory_audit":
            await self._store.inventory_reorder_audit(50)
        elif action == "proc_search":
            await self._store.search_products_proc(random.choice(s.search_terms))
        elif action == "customer_ltv":
            await self._store.customer_lifetime_value(random.choice(s.customer_ids))

    async def _fulfilment_queue(self) -> None:
        """Fulfilment queue: fetch the recent-order list, then load each
        order."""
        order_ids = await self._store.recent_order_ids(days=3, limit=25)
        for oid in order_ids:
            await self._store.get_order(oid)

    async def _checkout(self, s: WorkloadSample) -> None:
        n_items = random.randint(1, 3)
        lines = []
        for _ in range(n_items):
            pid = random.choice(s.product_ids)
            prod = await self._store.get_product(pid)
            if not prod:
                continue
            lines.append(
                OrderLine(
                    product_id=prod.product_id,
                    sku=prod.sku,
                    quantity=random.randint(1, 3),
                    unit_price_cents=prod.price_cents,
                )
            )
        if lines:
            customer_id = random.choice(s.customer_ids)
            await self._store.place_order(customer_id, lines)
            # Award loyalty points after checkout — via the stored proc.
            await self._store.apply_loyalty_points(customer_id)

    def _pick(self) -> str:
        total = sum(w for _, w in self._MIX)
        r = random.uniform(0, total)
        upto = 0.0
        for name, w in self._MIX:
            upto += w
            if r <= upto:
                return name
        return "browse"
