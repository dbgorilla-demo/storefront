"""FeatureService — the Storefront veneer.

Presents each database problem the app can cause as an innocent-looking product
"feature". A viewer clicks a feature (ship a flash sale, open the customer
dashboard, turn on inventory sync) and the corresponding real problem appears in
the database — so the audience can draw the line from a shipped feature to what
DBGorilla detects. Composes the existing services; owns no load itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.alerts import AlertPressurePort
from app.services.alert_service import AlertService
from app.services.chaos_service import ChaosService

_HOLD = 140


@dataclass
class Feature:
    id: str
    name: str
    icon: str
    tagline: str          # the product pitch
    problem: str          # the database consequence (so viewers connect the dots)
    kind: str             # "chaos" | "alert" | "nplus1"
    action: str


# Ordered by operational impact: the everyday feature that quietly regresses,
# then the ones that visibly hurt.
FEATURES: list[Feature] = [
    Feature(
        "dashboard", "Customer 360 Dashboard", "\U0001F464",
        "One screen with every customer's full order history.",
        "N+1 queries — one lookup per order instead of a join",
        "nplus1", "n_plus_one",
    ),
    Feature(
        "search", "Smart Product Search", "\U0001F50D",
        "Instant search across the whole catalog, any keyword.",
        "Leading-wildcard scans → response-time anomaly",
        "alert", "pg_avg_response_time_anomaly",
    ),
    Feature(
        "trending", "Trending Now", "\U0001F525",
        "Live 'hot products' carousel on every page load.",
        "Query-rate spike → QPS anomaly",
        "alert", "pg_qps_anomaly",
    ),
    Feature(
        "flashsale", "Flash Sale", "⚡",
        "Limited-time inventory drops with live stock holds.",
        "Row-lock contention → checkouts block",
        "chaos", "lock_storm",
    ),
    Feature(
        "sync", "Real-Time Inventory Sync", "\U0001F504",
        "Keep stock consistent across every sales channel.",
        "Idle-in-transaction connection pileup",
        "chaos", "connection_flood",
    ),
    Feature(
        "report", "Year-in-Review Report", "\U0001F4CA",
        "A personalized annual sales wrap-up per customer.",
        "Unbounded join pins CPU",
        "chaos", "runaway_query",
    ),
    Feature(
        "fastorders", "Fast Orders v2", "\U0001F680",
        "A sleeker, rebuilt order-history experience.",
        "Ships without the old index → sequential scans",
        "chaos", "missing_index",
    ),
    Feature(
        "clickstream", "Clickstream Analytics", "\U0001F4C8",
        "Track every page view for product insights.",
        "Table bloat — dead tuples pile up",
        "chaos", "bloat",
    ),
]


class FeatureService:
    def __init__(
        self,
        chaos: ChaosService,
        alerts: AlertService,
        pressure: AlertPressurePort,
    ) -> None:
        self._chaos = chaos
        self._alerts = alerts
        self._pressure = pressure

    def catalog(self) -> list[dict]:
        active = self._pressure.active_campaigns()
        out = []
        for f in FEATURES:
            remaining = 0.0
            if f.kind == "nplus1":
                remaining = active.get("n_plus_one", 0)
            out.append({**f.__dict__, "remaining": remaining})
        return out

    async def enable(self, feature_id: str) -> dict:
        f = next((x for x in FEATURES if x.id == feature_id), None)
        if f is None:
            raise ValueError(f"unknown feature: {feature_id}")

        if f.kind == "chaos":
            result = await self._chaos.inject(f.action)
            detail = result.detail
        elif f.kind == "alert":
            result = await self._alerts.trigger(f.action)
            detail = result["detail"]
        elif f.kind == "nplus1":
            await self._pressure.n_plus_one_pressure(_HOLD)
            detail = (f"Serving the dashboard with an N+1 for {_HOLD}s — the "
                      "per-order lookup runs ~50x per page load.")
        else:
            raise ValueError(f"unknown feature kind: {f.kind}")

        return {"feature": f.id, "name": f.name, "problem": f.problem, "detail": detail}

    async def restore(self) -> dict:
        """Roll back every feature: heal faults, stop alert campaigns, stop N+1."""
        await self._chaos.heal()
        await self._alerts.stop()
        await self._pressure.stop_all()
        return {"restored": True}
