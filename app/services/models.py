"""Service-layer result types. Distinct from repository models: these carry
business-shaped fields (dollars, human labels, severity) the UI renders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HealthView:
    connections_used: int
    connections_max: int
    connection_pct: float
    active_queries: int
    idle_in_transaction: int
    blocked_count: int
    longest_query_seconds: float
    dead_tuple_pct: float
    database_mb: float
    hot_index_present: bool
    autovacuum_enabled: bool
    status: str  # "healthy" | "degraded" | "critical" — business judgement


@dataclass
class FaultResult:
    fault: str
    action: str  # "injected" | "healed"
    detail: str


@dataclass
class ActiveQueryView:
    pid: int
    state: str
    duration_seconds: float
    wait: str | None
    query: str
    issue: str | None      # the anti-pattern this query exhibits, if any
    severity: str          # "normal" | "warn" | "bad"


@dataclass
class StatementView:
    query: str
    calls: int
    total_ms: float
    mean_ms: float
    rows: int
    issue: str | None
