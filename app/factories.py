"""Composition root. Builds concrete adapters with credentials and injects them
into services, returning services typed as their ports/classes. Callers (the web
layer) use only these factory outputs."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.repositories.alert_pressure_repository import PostgresAlertPressureRepository
from app.repositories.diagnostics_repository import PostgresDiagnosticsRepository
from app.repositories.fault_repository import PostgresFaultRepository
from app.repositories.health_repository import PostgresHealthRepository
from app.repositories.storefront_repository import PostgresStorefrontRepository
from app.services.alert_service import AlertService
from app.services.chaos_service import ChaosService
from app.services.diagnostics_service import DiagnosticsService
from app.services.feature_service import FeatureService
from app.services.health_service import HealthService
from app.services.traffic_service import TrafficService


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.dsn,
        min_size=settings.pool_min,
        max_size=settings.pool_max,
        open=False,
        kwargs={"application_name": "dbg-demo-storefront"},
    )


def create_traffic_service(
    pool: AsyncConnectionPool, settings: Settings
) -> TrafficService:
    repo = PostgresStorefrontRepository(pool=pool)
    return TrafficService(storefront=repo, qps=settings.traffic_qps)


def create_health_service(pool: AsyncConnectionPool) -> HealthService:
    repo = PostgresHealthRepository(pool=pool)
    return HealthService(health=repo)


def create_diagnostics_service(pool: AsyncConnectionPool) -> DiagnosticsService:
    repo = PostgresDiagnosticsRepository(pool=pool)
    return DiagnosticsService(diagnostics=repo)


def create_alert_pressure_repo(
    pool: AsyncConnectionPool, settings: Settings
) -> PostgresAlertPressureRepository:
    return PostgresAlertPressureRepository(pool=pool, dsn=settings.dsn)


def create_alert_service(
    pressure: PostgresAlertPressureRepository,
    settings: Settings,
    traffic: TrafficService,
) -> AlertService:
    return AlertService(
        pressure=pressure,
        traffic=traffic,
        active_conn_count=settings.alert_active_connections,
    )


def create_feature_service(
    chaos: ChaosService,
    alerts: AlertService,
    pressure: PostgresAlertPressureRepository,
) -> FeatureService:
    return FeatureService(chaos=chaos, alerts=alerts, pressure=pressure)


def create_chaos_service(
    pool: AsyncConnectionPool, settings: Settings
) -> ChaosService:
    repo = PostgresFaultRepository(pool=pool, dsn=settings.dsn)
    return ChaosService(
        faults=repo,
        lock_products=settings.lock_products,
        flood_connections=settings.flood_connections,
        runaway_count=settings.runaway_count,
        churn_batches=settings.churn_batches,
        churn_rows=settings.churn_rows,
    )
