"""Pydantic request/response models for the Trend Spotter API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# --- Requests ---

class ScanRequest(BaseModel):
    field: str
    time_window: Literal["1d", "7d", "30d"] = "7d"
    descriptions: bool = False


class CrossDomainRequest(BaseModel):
    fields: List[str]
    lookback_days: int = 30
    scan_missing: bool = False


# --- Responses ---

class SourceOutput(BaseModel):
    url: str
    source: str
    signal: str


class ScoresOutput(BaseModel):
    mentions: int = 0
    acceleration: int = 0
    durability: int = 0


class TrendOutput(BaseModel):
    name: str
    description: str
    scores: ScoresOutput
    durability_signals: Dict[str, int] = {}
    sentiment_penalty: float = 1.0
    classification: str
    trajectory: str
    prediction_id: Optional[str] = None
    data_gaps: List[str] = []
    sources: List[SourceOutput] = []


class ScanResult(BaseModel):
    field: str
    time_window: str
    generated_at: str
    trends: List[TrendOutput]
    run_data_gaps: List[str] = []


class ScanStatusResponse(BaseModel):
    scan_id: str
    status: Literal["pending", "running", "complete", "failed"]
    result: Optional[ScanResult] = None
    error: Optional[str] = None


class PredictionOutput(BaseModel):
    prediction_id: str
    field: str
    trend: str
    classification: str
    trajectory: str
    scores: Dict[str, int]
    created_at: str
    evaluation_30d: Optional[str] = None
    evaluation_90d: Optional[str] = None


class PredictionListResponse(BaseModel):
    count: int
    predictions: List[PredictionOutput]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    prediction_count: int = 0
