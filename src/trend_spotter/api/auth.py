"""API key authentication for Trend Spotter API."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Optional[str] = Security(_header),
) -> Optional[str]:
    """Validate API key from request header.

    If TREND_SPOTTER_API_KEY is not set, auth is disabled (open access).
    """
    expected = os.getenv("TREND_SPOTTER_API_KEY")
    if not expected:
        return None  # No key configured = open access
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key
