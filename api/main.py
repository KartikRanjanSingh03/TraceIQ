"""
api/main.py — Entry-point shim for Uvicorn.

Uvicorn is invoked from the project root as:
    uvicorn api.main:app --reload

The canonical FastAPI application lives in src/api/main.py.
This module re-exports `app` from there so the Uvicorn command above
resolves correctly without duplicating any logic.
"""
from src.api.main import app  # noqa: F401  re-export for uvicorn api.main:app

__all__ = ["app"]
