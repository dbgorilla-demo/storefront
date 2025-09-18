"""Runtime configuration. This is composition-root code — the ONE place that
reads the environment for connection details. Nothing in app/services or
app/ports imports this."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    dsn: str
    traffic_qps: float
    pool_min: int
    pool_max: int
    lock_products: int
    flood_connections: int
    runaway_count: int
    churn_batches: int
    churn_rows: int
    alert_active_connections: int


def load_settings() -> Settings:
    host = os.environ.get("PG_HOST", "storefront-pg-rw")
    port = os.environ.get("PG_PORT", "5432")
    user = os.environ.get("PG_USER", "storefront_app")
    password = os.environ["PG_PASSWORD"]
    db = os.environ.get("PG_DB", "storefront")
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return Settings(
        dsn=dsn,
        traffic_qps=float(os.environ.get("TRAFFIC_QPS", "8")),
        pool_min=int(os.environ.get("POOL_MIN", "2")),
        pool_max=int(os.environ.get("POOL_MAX", "10")),
        lock_products=int(os.environ.get("CHAOS_LOCK_PRODUCTS", "25")),
        flood_connections=int(os.environ.get("CHAOS_FLOOD_CONNECTIONS", "80")),
        runaway_count=int(os.environ.get("CHAOS_RUNAWAY_COUNT", "4")),
        churn_batches=int(os.environ.get("CHAOS_CHURN_BATCHES", "40")),
        churn_rows=int(os.environ.get("CHAOS_CHURN_ROWS", "5000")),
        alert_active_connections=int(os.environ.get("ALERT_ACTIVE_CONNECTIONS", "130")),
    )
