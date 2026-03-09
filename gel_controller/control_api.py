"""
FastAPI control API server for RoomController runtime commands.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Protocol

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ControlAPIController(Protocol):
    """Controller contract used by the control API transport layer."""

    def is_running(self) -> bool:
        ...

    def get_status(self, include_logs: bool = True, log_lines: int = 80) -> Dict[str, object]:
        ...

    def capture_baseline(self, room_id: Optional[str] = None) -> Dict[str, object]:
        ...

    def analyze_latest(self, room_id: Optional[str] = None) -> Dict[str, object]:
        ...


class RoomRequest(BaseModel):
    """Shared request payload for room-scoped control actions."""

    room_id: Optional[str] = None


class ControlAPIServer:
    """Runs a FastAPI app in a background thread for controller commands."""

    def __init__(self, controller: ControlAPIController, host: str, port: int) -> None:
        self._controller = controller
        self._host = host
        self._port = port
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title="GEL Controller Control API",
            version="1.0.0",
        )

        @app.get("/health")
        @app.get("/api/v1/health")
        def health() -> Dict[str, object]:
            return {"ok": True, "running": self._controller.is_running()}

        @app.get("/status")
        @app.get("/api/v1/status")
        def status() -> JSONResponse:
            result = self._controller.get_status(include_logs=True)
            http_status = 200 if result.get("ok") else 500
            return JSONResponse(content=result, status_code=http_status)

        @app.post("/capture-baseline")
        @app.post("/api/v1/capture-baseline")
        def capture_baseline(payload: Optional[RoomRequest] = None) -> JSONResponse:
            room_id = payload.room_id if payload is not None else None
            result = self._controller.capture_baseline(room_id=room_id)
            http_status = 200 if result.get("ok") else 404
            return JSONResponse(content=result, status_code=http_status)

        @app.post("/analyze-latest")
        @app.post("/api/v1/analyze-latest")
        def analyze_latest(payload: Optional[RoomRequest] = None) -> JSONResponse:
            room_id = payload.room_id if payload is not None else None
            result = self._controller.analyze_latest(room_id=room_id)
            http_status = 200 if result.get("ok") else 404
            return JSONResponse(content=result, status_code=http_status)

        return app

    def start(self) -> None:
        """Start uvicorn in a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        config = uvicorn.Config(
            app=self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config=config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="Controller-Control-API",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        """Request uvicorn shutdown and join the server thread."""
        if self._server is not None:
            self._server.should_exit = True

        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
            if self._thread.is_alive():
                logger.warning("Control API thread did not stop within %.1fs", timeout_seconds)

        self._server = None
        self._thread = None

    def app(self) -> FastAPI:
        """Expose FastAPI app for testing."""
        return self._app
