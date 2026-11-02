"""``GET /api/health``: liveness plus app and schema version."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zfinance import __version__
from zfinance.db import get_connection
from zfinance.migrator import schema_version

router = APIRouter(prefix="/api", tags=["health"])


class Health(BaseModel):
    """Health response. ``schema_version`` is the highest applied migration number."""

    status: str
    version: str
    schema_version: int


@router.get("/health")
def health(conn: Annotated[sqlite3.Connection, Depends(get_connection)]) -> Health:
    """Report that the API is up and the database is reachable."""
    return Health(status="ok", version=__version__, schema_version=schema_version(conn))
