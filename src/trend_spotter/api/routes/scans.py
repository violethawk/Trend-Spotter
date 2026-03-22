"""Scan endpoints for trend discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...config import load_config
from ..auth import verify_api_key
from ..models import ScanRequest, ScanStatusResponse
from ..tasks import get_scan, run_scan_sync, submit_scan

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/scans", status_code=202, response_model=ScanStatusResponse)
async def create_scan(
    req: ScanRequest,
    sync: bool = Query(False, description="Run synchronously and wait for result"),
):
    """Start a trend scan. Returns immediately with a scan_id (async)
    or blocks until complete (sync=true)."""
    config = load_config()

    if sync:
        result = run_scan_sync(req.field, req.time_window, config)
        return ScanStatusResponse(
            scan_id="sync",
            status="complete",
            result=result,
        )

    scan_id = submit_scan(req.field, req.time_window, config)
    return ScanStatusResponse(scan_id=scan_id, status="pending")


@router.get("/scans/{scan_id}", response_model=ScanStatusResponse)
async def get_scan_status(scan_id: str):
    """Poll for scan status and results."""
    task = get_scan(scan_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanStatusResponse(
        scan_id=scan_id,
        status=task["status"],
        result=task.get("result"),
        error=task.get("error"),
    )
