"""Inbound HTTP adapter. Parses requests, calls services, maps to JSON.

Holds no business rules and touches no database directly. It owns the app
lifespan: open the pool, launch the constant-traffic loop, expose the buttons.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import load_settings
from app.factories import (
    create_alert_pressure_repo,
    create_alert_service,
    create_chaos_service,
    create_diagnostics_service,
    create_feature_service,
    create_health_service,
    create_pool,
    create_traffic_service,
)
from app.services.chaos_service import FAULTS

_INDEX = (Path(__file__).parent / "index.html").read_text()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    pool = create_pool(settings)
    await pool.open()

    # Reap any chaos/alert connections left behind by a previous pod. They don't
    # close instantly when the old process dies, and against the tight
    # max_connections they'd starve this pod's pool. Start from a clean slate.
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND (application_name LIKE 'dbg-demo-chaos%%' "
                "     OR application_name LIKE 'dbg-demo-alert%%') "
                "AND pid <> pg_backend_pid()"
            )
    except Exception:  # noqa: BLE001
        pass

    traffic = create_traffic_service(pool, settings)
    health = create_health_service(pool)
    chaos = create_chaos_service(pool, settings)
    diagnostics = create_diagnostics_service(pool)
    pressure = create_alert_pressure_repo(pool, settings)
    alerts = create_alert_service(pressure, settings, traffic)
    features = create_feature_service(chaos, alerts, pressure)

    app.state.pool = pool
    app.state.traffic = traffic
    app.state.health = health
    app.state.chaos = chaos
    app.state.diagnostics = diagnostics
    app.state.alerts = alerts
    app.state.features = features
    # Constant traffic starts the moment the app is up and never stops.
    app.state.traffic_task = asyncio.create_task(traffic.run_forever())

    try:
        yield
    finally:
        traffic.stop()
        app.state.traffic_task.cancel()
        # Best-effort: put the database back before we go.
        try:
            await alerts.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            await chaos.heal()
        except Exception:  # noqa: BLE001
            pass
        await pool.close()


app = FastAPI(title="storefront", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/health")
async def api_health() -> JSONResponse:
    view = await app.state.health.current()
    traffic = app.state.traffic
    payload = view.__dict__ | {
        "traffic_ok": traffic.ok,
        "traffic_errors": traffic.errors,
    }
    return JSONResponse(payload)


@app.post("/api/fault/{fault}")
async def api_fault(fault: str) -> JSONResponse:
    if fault not in FAULTS:
        raise HTTPException(status_code=404, detail=f"unknown fault: {fault}")
    result = await app.state.chaos.inject(fault)
    return JSONResponse(result.__dict__)


@app.post("/api/heal")
async def api_heal() -> JSONResponse:
    results = await app.state.chaos.heal()
    return JSONResponse({"results": [r.__dict__ for r in results]})


@app.get("/api/reservations")
async def api_reservations() -> JSONResponse:
    stats = await app.state.chaos.reservation_stats()
    return JSONResponse(stats.__dict__)


@app.get("/api/customers-with-orders")
async def api_customers_with_orders(limit: int = 500) -> JSONResponse:
    repo = app.state.traffic._store
    return JSONResponse({"customers": await repo.customers_with_orders(limit)})


@app.get("/api/activity")
async def api_activity() -> JSONResponse:
    sessions = await app.state.diagnostics.live_sessions()
    return JSONResponse({"sessions": [s.__dict__ for s in sessions]})


@app.get("/api/workload")
async def api_workload() -> JSONResponse:
    statements = await app.state.diagnostics.top_workload()
    return JSONResponse({"statements": [s.__dict__ for s in statements]})


@app.get("/api/connections")
async def api_connections() -> JSONResponse:
    states = await app.state.diagnostics.connection_states()
    return JSONResponse({"states": states})


@app.get("/api/alerts")
async def api_alerts() -> JSONResponse:
    return JSONResponse({
        "alerts": app.state.alerts.catalog(),
        "log_alerts": app.state.alerts.log_alerts(),
    })


@app.post("/api/alerts/{alert_id}")
async def api_alert_trigger(alert_id: str) -> JSONResponse:
    try:
        result = await app.state.alerts.trigger(alert_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(result)


@app.post("/api/alerts-stop")
async def api_alerts_stop() -> JSONResponse:
    return JSONResponse(await app.state.alerts.stop())


@app.get("/api/features")
async def api_features() -> JSONResponse:
    return JSONResponse({"features": app.state.features.catalog()})


@app.post("/api/features/{feature_id}")
async def api_feature_enable(feature_id: str) -> JSONResponse:
    try:
        return JSONResponse(await app.state.features.enable(feature_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/features-restore")
async def api_features_restore() -> JSONResponse:
    return JSONResponse(await app.state.features.restore())
