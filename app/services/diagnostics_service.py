"""DiagnosticsService — presents live sessions and workload for the query views.

The business value here is the *classification*: it tags each query with the
anti-pattern it exhibits, so the console doesn't just show queries — it shows which
ones are problems and why. That judgement is a service-layer concern; the
repository just returns rows.
"""

from __future__ import annotations

import re

from app.ports.diagnostics import DiagnosticsPort
from app.services.models import ActiveQueryView, StatementView

# (label, regex) — first match wins. Ordered most-specific first.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cartesian join", re.compile(r"orders\s+\w+\s*,\s*storefront\.orders", re.I)),
    ("leading-wildcard scan", re.compile(r"ILIKE\s+'?%", re.I)),
    ("leading-wildcard scan", re.compile(r"ILIKE\s+\$\d", re.I)),
    ("function on indexed column", re.compile(r"EXTRACT\s*\(", re.I)),
    ("NOT IN anti-pattern", re.compile(r"NOT\s+IN\s*\(", re.I)),
    ("correlated subqueries", re.compile(r"SELECT\s+count\(\*\).*WHERE.*=\s*\w+\.\w+", re.I)),
    ("full-table UPDATE", re.compile(r"UPDATE\s+storefront\.\w+\s+SET(?!.*WHERE)", re.I)),
    ("unbounded SELECT *", re.compile(r"SELECT\s+\*\s+FROM\s+storefront\.\w+\s*$", re.I)),
]


def _classify(query: str) -> str | None:
    for label, rx in _PATTERNS:
        if rx.search(query):
            return label
    return None


class DiagnosticsService:
    def __init__(self, diagnostics: DiagnosticsPort) -> None:
        self._diag = diagnostics

    async def live_sessions(self, limit: int = 25) -> list[ActiveQueryView]:
        sessions = await self._diag.active_sessions(limit)
        out: list[ActiveQueryView] = []
        for s in sessions:
            wait = (
                f"{s.wait_event_type}:{s.wait_event}"
                if s.wait_event_type
                else None
            )
            # A long-running active query or one stuck on a lock is the headline.
            severity = "normal"
            if s.state == "idle in transaction":
                severity = "warn"  # holding a transaction/locks open
            if s.duration_seconds >= 2:
                severity = "warn"
            if s.duration_seconds >= 10 or (wait and "Lock" in (s.wait_event_type or "")):
                severity = "bad"
            out.append(
                ActiveQueryView(
                    pid=s.pid,
                    state=s.state,
                    duration_seconds=round(s.duration_seconds, 1),
                    wait=wait,
                    query=s.query,
                    issue=_classify(s.query),
                    severity=severity,
                )
            )
        return out

    async def connection_states(self) -> list[dict]:
        """Connection counts by state, for the dashboard's by-state panel.

        This is the view the flood button actually moves: idle-in-transaction
        connections don't count as 'active', so an active-connections chart
        stays flat while this one climbs.
        """
        rows = await self._diag.connections_by_state()
        # Stable, meaningful ordering with the dangerous states first.
        order = {
            "active": 0,
            "idle in transaction": 1,
            "idle in transaction (aborted)": 2,
            "idle": 3,
        }
        rows = sorted(rows, key=lambda r: (order.get(r[0], 9), -r[1]))
        return [{"state": s, "count": c} for s, c in rows]

    async def top_workload(self, limit: int = 20) -> list[StatementView]:
        stats = await self._diag.top_statements(limit)
        return [
            StatementView(
                query=s.query,
                calls=s.calls,
                total_ms=round(s.total_exec_ms, 1),
                mean_ms=round(s.mean_exec_ms, 2),
                rows=s.rows,
                issue=_classify(s.query),
            )
            for s in stats
        ]
