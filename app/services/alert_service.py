"""AlertService — trigger DBGorilla's default database alerts on demand.

Owns the catalog of alerts DBGorilla ships for a monitored Postgres database and,
for each, the load campaign that drives the underlying metric across its
threshold long enough to clear the alert's `for: 1m` window. Depends on the
AlertPressurePort (the load) and the TrafficService (to damp normal traffic when
an average-based alert would otherwise be diluted).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.ports.alerts import AlertPressurePort
from app.services.traffic_service import TrafficService

# Campaigns run past the 1-minute `for:` window plus a couple of scrape cycles.
_HOLD = 140


@dataclass
class AlertSpec:
    id: str
    title: str
    severity: str          # "critical" | "warning"
    metric: str
    condition: str
    campaign: str          # which pressure campaign fires it
    triggerable: bool
    note: str = ""


# The default per-database alert catalog (backend alert_registry SUPPORTED_ALERTS).
# The six log-pattern alerts are DBGorilla-internal (they match backend log lines,
# not database metrics) and cannot be induced from this app — listed for
# completeness so the console can display the full default set.
ALERTS: list[AlertSpec] = [
    AlertSpec(
        "pg_active_connections_threshold", "Active connections over threshold",
        "critical", "pg_stat_active_connections_total", "> 100 active backends",
        "active_connections", False,
        note="Unreachable by construction: armed at >100 active connections, but "
             "max_connections=50 is a hard ceiling — Postgres refuses new "
             "connections at 50, so the count can never reach 100. Raising "
             "max_connections above 100 would make it triggerable, at the cost of "
             "the deliberate 'max_connections too low' finding.",
    ),
    AlertSpec(
        "pg_slow_query_threshold", "Slow query (p95 duration)",
        "critical", "pg_stat_statements_seconds_total / calls", "avg > 30s per call",
        "slow_query_deep", True,
        note="Marginal: needs the per-call average across the 4m window above 30s. "
             "The trigger pauses normal traffic and runs ~35s queries to beat "
             "dilution, but recent fast calls in the window can keep it under.",
    ),
    AlertSpec(
        "pg_avg_response_time_threshold", "Average response time over threshold",
        "warning", "pg_stat_statements_mean_exec_time_seconds", "avg > 2000",
        "slow_query", False,
        note="Effectively unreachable: the threshold (2000, from a *_MS config "
             "value) is compared against a *_seconds metric, so it needs a 2000-"
             "SECOND average per statement. Live avg is ~0.09s. Unit mismatch in "
             "the default alert — the anomaly variant fires fine; this one won't.",
    ),
    AlertSpec(
        "pg_avg_response_time_anomaly", "Average response time anomaly",
        "warning", "pg_stat_statements_mean_exec_time_seconds", "> 2x 1h baseline",
        "slow_query", True,
    ),
    AlertSpec(
        "pg_stat_statements_anomaly", "Statement performance anomaly",
        "warning", "pg_stat_statements_seconds_total rate", "> 2x 1h baseline",
        "slow_query", True,
    ),
    AlertSpec(
        "pg_qps_anomaly", "Query-rate (QPS) anomaly",
        "warning", "pg_stat_statements_calls_total rate", "> 2x baseline and > 10 qps",
        "qps", True,
    ),
    AlertSpec(
        "pg_total_transactions_threshold", "Total transactions over threshold",
        "warning", "pg_stat_n_total_transactions_total", "> 100,000,000 cumulative",
        "transactions", False,
        note="Cumulative counter; 100M is not reachable on demand. Trigger drives "
             "the transaction rate so the metric visibly climbs, but won't cross "
             "the threshold in a short window.",
    ),
]

_LOG_ALERTS = [
    "database_connection_error", "exporter_inaccessible", "job_failed",
    "llm_failure", "rate_limit_violation", "internal_alert",
]


class AlertService:
    def __init__(
        self,
        pressure: AlertPressurePort,
        traffic: TrafficService,
        *,
        active_conn_count: int = 130,
    ) -> None:
        self._p = pressure
        self._traffic = traffic
        self._active_conn_count = active_conn_count
        self._resume_task: asyncio.Task | None = None

    def catalog(self) -> list[dict]:
        active = self._p.active_campaigns()
        out = []
        for a in ALERTS:
            out.append({
                **a.__dict__,
                "remaining": active.get(_campaign_key(a.campaign), 0),
            })
        return out

    def log_alerts(self) -> list[str]:
        return list(_LOG_ALERTS)

    async def trigger(self, alert_id: str) -> dict:
        spec = next((a for a in ALERTS if a.id == alert_id), None)
        if spec is None:
            raise ValueError(f"unknown alert: {alert_id}")

        if spec.campaign == "active_connections":
            # Do NOT open a connection storm: with max_connections=50 the alert
            # (>100) can't fire, and saturating the ceiling would starve the app
            # pool and DBGorilla's own collection. Explain instead of harming.
            return {
                "alert": alert_id, "started": False, "hold_seconds": 0,
                "detail": "Not triggered — " + spec.note,
            }
        elif spec.campaign == "slow_query":
            # Moderately slow queries; keep normal traffic so the anomaly/threshold
            # sees a mix but the mean climbs well past 2s.
            await self._p.slow_query_pressure(6, 3.0, _HOLD)
            detail = f"Running slow (~3s) queries for {_HOLD}s."
        elif spec.campaign == "slow_query_deep":
            # p95>30s needs the per-call average above 30s: pause normal traffic
            # so the slow queries aren't diluted, and make them genuinely long.
            self._traffic.pause()
            self._schedule_resume(_HOLD)
            await self._p.slow_query_pressure(4, 35.0, _HOLD)
            detail = (f"Paused normal traffic and running ~35s queries for {_HOLD}s "
                      "so per-call average exceeds 30s.")
        elif spec.campaign == "qps":
            await self._p.qps_pressure(30, _HOLD)
            detail = f"Driving ~30 qps of point lookups for {_HOLD}s."
        elif spec.campaign == "transactions":
            await self._p.transaction_pressure(20, _HOLD)
            detail = (f"Committing many tiny transactions for {_HOLD}s. "
                      "Note: won't cross the 100M cumulative threshold.")
        else:
            raise ValueError(f"no campaign for {spec.campaign}")

        return {"alert": alert_id, "started": True, "hold_seconds": _HOLD, "detail": detail}

    def _schedule_resume(self, after: int) -> None:
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()

        async def _resume():
            try:
                await asyncio.sleep(after)
            finally:
                self._traffic.resume()

        self._resume_task = asyncio.create_task(_resume())

    async def stop(self) -> dict:
        await self._p.stop_all()
        self._traffic.resume()
        if self._resume_task:
            self._resume_task.cancel()
        return {"stopped": True}


def _campaign_key(campaign: str) -> str:
    # Map the service-level campaign name to the repository's campaign key.
    return {
        "active_connections": "active_connections",
        "slow_query": "slow_query",
        "slow_query_deep": "slow_query",
        "qps": "qps",
        "transactions": "transactions",
    }[campaign]
