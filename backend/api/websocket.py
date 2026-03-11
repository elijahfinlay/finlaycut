import asyncio
import json
from typing import Any, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for real-time progress updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.signature_watches: dict[WebSocket, dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        self.signature_watches.pop(websocket, None)

    async def _send_json_with_timeout(self, connection: WebSocket, message: dict) -> bool:
        try:
            await asyncio.wait_for(connection.send_json(message), timeout=0.75)
            return True
        except Exception:
            return False

    async def broadcast(self, message: dict):
        """Send a message to all connected clients."""
        if not self.active_connections:
            return

        connections = list(self.active_connections)
        results = await asyncio.gather(
            *(self._send_json_with_timeout(connection, message) for connection in connections),
            return_exceptions=True,
        )

        dead = {
            connection
            for connection, result in zip(connections, results)
            if result is not True
        }
        for connection in dead:
            self.disconnect(connection)

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

    def set_signature_watch(
        self,
        websocket: WebSocket,
        signature: str | None,
        timeline_name: str | None = None,
    ) -> None:
        """Track the timeline signature a specific client wants to monitor."""
        if not signature:
            self.signature_watches.pop(websocket, None)
            return

        signature_value = str(signature)
        watch = self.signature_watches.get(websocket)
        if watch and watch.get("signature") == signature_value:
            watch["timeline_name"] = timeline_name
            return

        self.signature_watches[websocket] = {
            "signature": signature_value,
            "timeline_name": timeline_name,
            "last_signature": signature_value,
        }

    def has_signature_watches(self) -> bool:
        return bool(self.signature_watches)

    async def notify_timeline_watchers(self, current_signature: str, timeline_name: str) -> None:
        """Notify only clients whose watched timeline signature has changed."""
        if not self.signature_watches:
            return

        dead: set[WebSocket] = set()
        for connection, watch in list(self.signature_watches.items()):
            if current_signature == watch.get("last_signature"):
                continue

            success = await self._send_json_with_timeout(connection, {
                "type": "timeline_changed",
                "timeline_signature": current_signature,
                "timeline_name": timeline_name,
            })
            if success:
                watch["last_signature"] = current_signature
            else:
                dead.add(connection)

        for connection in dead:
            self.disconnect(connection)


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
                manager.set_signature_watch(
                    websocket,
                    msg.get("signature"),
                    msg.get("timeline_name"),
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
