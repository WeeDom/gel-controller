"""
FastAPI control API server for RoomController runtime commands.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, Optional, Protocol

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
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

    def enqueue_capture_baseline(self, room_id: Optional[str] = None) -> Dict[str, object]:
        ...

    def get_control_job(self, job_id: str) -> Dict[str, object]:
        ...

    def analyze_latest(self, room_id: Optional[str] = None) -> Dict[str, object]:
        ...

    def list_events(self, room_id: Optional[str] = None) -> Dict[str, object]:
        ...

    def get_image_bytes(self, filename: str) -> Optional[bytes]:
        ...

    def get_log_entries(self, cursor: Optional[int] = None, limit_bytes: int = 65536) -> Dict[str, object]:
        ...

    def on_breakbeam_trigger(self, sensor_id: str, room_id: str, beam_broken: bool) -> Dict[str, object]:
        ...


class RoomRequest(BaseModel):
    """Shared request payload for room-scoped control actions."""

    room_id: Optional[str] = None


class BreakbeamPayload(BaseModel):
    sensor_id: str
    room_id: str
    beam_broken: bool


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
            result = self._controller.get_status(include_logs=False)
            http_status = 200 if result.get("ok") else 500
            return JSONResponse(content=result, status_code=http_status)

        @app.get("/logs")
        @app.get("/api/v1/logs")
        def logs(cursor: Optional[int] = None, limit_bytes: int = 65536) -> JSONResponse:
            result = self._controller.get_log_entries(
                cursor=cursor,
                limit_bytes=limit_bytes,
            )
            return JSONResponse(content=result, status_code=200 if result.get("ok") else 500)

        @app.post("/capture-baseline")
        @app.post("/api/v1/capture-baseline")
        def capture_baseline(payload: Optional[RoomRequest] = None, wait: bool = False) -> JSONResponse:
            room_id = payload.room_id if payload is not None else None
            if wait:
                result = self._controller.capture_baseline(room_id=room_id)
                http_status = 200 if result.get("ok") else 404
            else:
                result = self._controller.enqueue_capture_baseline(room_id=room_id)
                http_status = 202 if result.get("ok") else 500
            return JSONResponse(content=result, status_code=http_status)

        @app.get("/api/v1/jobs/{job_id}")
        def get_job(job_id: str) -> JSONResponse:
            result = self._controller.get_control_job(job_id=job_id)
            if not result.get("ok"):
                return JSONResponse(content=result, status_code=404)
            status = result.get("status")
            if status in {"queued", "running"}:
                return JSONResponse(content=result, status_code=202)
            return JSONResponse(content=result, status_code=200)

        @app.post("/analyze-latest")
        @app.post("/api/v1/analyze-latest")
        def analyze_latest(payload: Optional[RoomRequest] = None) -> JSONResponse:
            room_id = payload.room_id if payload is not None else None
            result = self._controller.analyze_latest(room_id=room_id)
            http_status = 200 if result.get("ok") else 404
            return JSONResponse(content=result, status_code=http_status)

        _SAFE_IMAGE_RE = re.compile(
            r'^(baseline|capture)-[A-Za-z0-9]+-[A-Za-z0-9]+-\d{8}_\d{6}(?:_\d+)?\.jpe?g$',
            re.IGNORECASE,
        )

        @app.get("/api/v1/events")
        def list_events_all() -> JSONResponse:
            result = self._controller.list_events()
            return JSONResponse(content=result, status_code=200 if result.get("ok") else 500)

        @app.get("/api/v1/events/{room_id}")
        def list_events_room(room_id: str) -> JSONResponse:
            result = self._controller.list_events(room_id=room_id)
            return JSONResponse(content=result, status_code=200 if result.get("ok") else 500)

        @app.get("/api/v1/image/{filename}")
        def get_image(filename: str) -> Response:
            if not _SAFE_IMAGE_RE.match(filename):
                raise HTTPException(status_code=400, detail="Invalid filename")
            data = self._controller.get_image_bytes(filename)
            if data is None:
                raise HTTPException(status_code=404, detail="Image not found")
            return Response(content=data, media_type="image/jpeg")

        @app.post("/api/v1/sensor/breakbeam")
        async def breakbeam(payload: BreakbeamPayload, request: Request) -> JSONResponse:
            from gel_controller.camera_auth import verify_auth_headers
            if not verify_auth_headers(
                method="POST",
                path="/api/v1/sensor/breakbeam",
                query="",
                headers=dict(request.headers),
            ):
                raise HTTPException(status_code=401, detail="Invalid or missing HMAC signature")
            result = self._controller.on_breakbeam_trigger(
                sensor_id=payload.sensor_id,
                room_id=payload.room_id,
                beam_broken=payload.beam_broken,
            )
            return JSONResponse(content=result, status_code=200 if result.get("ok") else 404)

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
