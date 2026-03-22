"""FastAPI application factory for Trend Spotter API."""

from __future__ import annotations

from fastapi import FastAPI

from .routes import accuracy, cross_domain, health, metrics, predictions, scans


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Trend Spotter API",
        description=(
            "Identify which emerging signals will persist before "
            "they become obvious. Scan fields, view predictions, "
            "and track classification accuracy."
        ),
        version="0.1.0",
    )

    app.include_router(health.router, tags=["health"])
    app.include_router(scans.router, tags=["scans"])
    app.include_router(predictions.router, tags=["predictions"])
    app.include_router(accuracy.router, tags=["accuracy"])
    app.include_router(cross_domain.router, tags=["cross-domain"])
    app.include_router(metrics.router, tags=["metrics"])

    return app


app = create_app()
