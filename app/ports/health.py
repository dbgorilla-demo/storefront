"""Port: read what the database is currently doing, for the status panel."""

from __future__ import annotations

from typing import Protocol

from app.repositories.models import DatabaseSnapshot


class DatabaseHealthPort(Protocol):
    async def snapshot(self) -> DatabaseSnapshot: ...
