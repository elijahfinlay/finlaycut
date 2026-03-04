import asyncio
import json
from typing import Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for real-time progress updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """Send a message to all connected clients."""
        dead = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.add(connection)
        self.active_connections -= dead

    async def send_progress(self, operation: str, progress: float, message: str = ""):
        """Send a progress update to all clients."""
        await self.broadcast({
            "type": "progress",
            "operation": operation,
            "progress": progress,
            "message": message
        })

    async def send_complete(self, operation: str, result: dict = None):
        """Send a completion notification."""
        await self.broadcast({
            "type": "complete",
            "operation": operation,
            "result": result or {}
        })

    async def send_error(self, operation: str, error: str):
        """Send an error notification."""
        await self.broadcast({
            "type": "error",
            "operation": operation,
            "error": error
        })

    async def send_resolve_status(self, status: dict):
        """Send Resolve connection status update."""
        await self.broadcast({
            "type": "resolve_status",
            **status
        })

    async def send_timeline_changed(self, new_signature: str, timeline_name: str):
        """Notify all clients that the timeline signature has changed."""
        await self.broadcast({
            "type": "timeline_changed",
            "timeline_signature": new_signature,
            "timeline_name": timeline_name,
        })


manager = ConnectionManager()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            if msg.get("type") == "watch_signature":
                # Frontend tells us which signature to watch for changes
                from services.resolve_service import resolve_service
                resolve_service.set_watched_signature(
                    msg.get("signature"),
                    msg.get("timeline_name"),
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
