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


manager = ConnectionManager()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, handle any incoming messages
            data = await websocket.receive_text()
            # Could handle client-side messages here if needed
    except WebSocketDisconnect:
        manager.disconnect(websocket)
