"""Port: the OLTP operations a storefront performs against its database."""

from __future__ import annotations

from typing import Protocol

from app.repositories.models import (
    OrderLine,
    OrderReceipt,
    ProductSummary,
    RevenueBucket,
    WorkloadSample,
)


class StorefrontRepository(Protocol):
    """Everything the storefront needs from its database.

    These are the operations a real e-commerce app runs all day. The traffic
    generator drives them; that is what makes the load realistic rather than
    synthetic `SELECT 1` noise.
    """

    async def sample_workload_ids(self, limit: int) -> WorkloadSample: ...

    async def browse_category(
        self, category_id: int, limit: int
    ) -> list[ProductSummary]: ...

    async def search_products(self, term: str, limit: int) -> list[ProductSummary]: ...

    async def get_product(self, product_id: int) -> ProductSummary | None: ...

    async def record_page_view(self, customer_id: int, product_id: int) -> None: ...

    async def place_order(
        self, customer_id: int, lines: list[OrderLine]
    ) -> OrderReceipt: ...

    async def get_order(self, order_id: int) -> OrderReceipt | None: ...

    async def customer_order_history(
        self, customer_id: int, limit: int
    ) -> list[OrderReceipt]: ...

    async def revenue_report(self, days: int) -> list[RevenueBucket]: ...

    # --- storefront features added over time, each owned by a different team.

    async def recent_order_ids(self, days: int, limit: int) -> list[int]:
        """Recent order ids for the fulfilment queue."""
        ...

    async def sales_by_month(self, year: int) -> list[RevenueBucket]:
        """Analytics tab: revenue bucketed by month for a given year."""
        ...

    async def awaiting_shipment_count(self, days: int) -> int:
        """Fulfilment report: customers with no shipped order."""
        ...

    async def inventory_reorder_audit(self, limit: int) -> list[ProductSummary]:
        """Ops audit: products low on stock or flagged for review."""
        ...

    # --- stored procedures the app calls in normal operation.

    async def customer_lifetime_value(self, customer_id: int) -> int:
        """Calls fn_customer_lifetime_value."""
        ...

    async def apply_loyalty_points(self, customer_id: int) -> int:
        """Calls fn_apply_loyalty_points."""
        ...

    async def search_products_proc(self, term: str) -> list[ProductSummary]:
        """Calls fn_find_products."""
        ...
