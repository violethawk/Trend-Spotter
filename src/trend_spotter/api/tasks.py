"""Background scan task management for the API.

Uses an in-process thread pool for running scans asynchronously.
Scan results are stored in memory and lost on restart.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from ..config import Config
from ..pipeline import run_pipeline

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_tasks: Dict[str, Dict[str, Any]] = {}


def submit_scan(
    field: str, window: str, config: Config, *,
    generate_descriptions: bool = False,
) -> str:
    """Submit a scan to run in the background. Returns a scan_id."""
    scan_id = str(uuid.uuid4())
    _tasks[scan_id] = {"status": "pending", "result": None, "error": None}

    def _run():
        _tasks[scan_id]["status"] = "running"
        try:
            result = run_pipeline(field, window, config,
                                  generate_descriptions=generate_descriptions)
            _tasks[scan_id]["result"] = result
            _tasks[scan_id]["status"] = "complete"
        except Exception as exc:
            logger.error("Scan %s failed: %s", scan_id, exc)
            _tasks[scan_id]["error"] = str(exc)
            _tasks[scan_id]["status"] = "failed"

    _executor.submit(_run)
    return scan_id


def get_scan(scan_id: str) -> Optional[Dict[str, Any]]:
    """Get the status and result of a scan."""
    return _tasks.get(scan_id)


def run_scan_sync(
    field: str, window: str, config: Config, *,
    generate_descriptions: bool = False,
) -> Dict[str, Any]:
    """Run a scan synchronously and return the result."""
    return run_pipeline(field, window, config,
                        generate_descriptions=generate_descriptions)
