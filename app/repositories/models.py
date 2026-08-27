"""Storage-agnostic types returned by the repository ports.

These deliberately carry no Postgres in them: no rows, no cursors, no psycopg
types. Swapping the storefront onto a different engine would not change a
single field here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ProductSummary:
    product_id: int
    sku: str
    name: str
    price_cents: int
    category_name: str
    quantity_available: int


@dataclass(frozen=True)
class OrderLine:
    product_id: int
    sku: str
    quantity: int
    unit_price_cents: int


@dataclass(frozen=True)
class OrderReceipt:
    order_id: int
    customer_id: int
    status: str
    total_cents: int
    placed_at: datetime
    lines: tuple[OrderLine, ...]


@dataclass(frozen=True)
class RevenueBucket:
    day: date
    order_count: int
    revenue_cents: int


@dataclass(frozen=True)
class WorkloadSample:
    """Real identifiers pulled from the database, used to drive realistic traffic.

    Without this the load generator would query for IDs that don't exist and
    every plan would be an index-only miss.
    """

    customer_ids: tuple[int, ...]
    product_ids: tuple[int, ...]
    category_ids: tuple[int, ...]
    search_terms: tuple[str, ...]


@dataclass(frozen=True)
class ReservationStats:
    """Live counters for the inventory-reservation drill: how many SERIALIZABLE
    reservation transactions were attempted, how many committed, and how many
    Postgres refused with a serialization failure (SQLSTATE 40001) and had to
    be retried."""

    running: bool
    workers: int
    attempts: int
    commits: int
    serialization_failures: int
    elapsed_seconds: float
    index_present: bool


@dataclass(frozen=True)
class BlockedSession:
    blocked_pid: int
    blocked_query: str
    blocking_pid: int
    blocking_query: str
    wait_seconds: float


@dataclass(frozen=True)
class ActiveSession:
    """A backend currently doing something, from pg_stat_activity."""

    pid: int
    state: str
    duration_seconds: float
    wait_event_type: str | None
    wait_event: str | None
    query: str
    application_name: str


@dataclass(frozen=True)
class StatementStat:
    """One normalized query's aggregate cost, from pg_stat_statements."""

    query: str
    calls: int
    total_exec_ms: float
    mean_exec_ms: float
    rows: int


@dataclass(frozen=True)
class DatabaseSnapshot:
    """A point-in-time read of what the database is actually doing."""

    total_connections: int
    max_connections: int
    active_queries: int
    idle_in_transaction: int
    blocked_sessions: tuple[BlockedSession, ...]
    longest_query_seconds: float
    dead_tuples: int
    live_tuples: int
    database_bytes: int
    hot_index_present: bool
    autovacuum_enabled: bool
