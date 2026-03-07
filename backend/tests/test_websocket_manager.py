import asyncio
import unittest

from api.websocket import ConnectionManager


class _FastWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class _SlowWebSocket:
    async def send_json(self, _message):
        await asyncio.sleep(5)


class WebSocketManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_drops_slow_connections_without_blocking(self):
        manager = ConnectionManager()
        fast = _FastWebSocket()
        slow = _SlowWebSocket()
        manager.active_connections = {fast, slow}

        started = asyncio.get_running_loop().time()
        await manager.broadcast({"type": "progress", "operation": "transcript_editor"})
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 2.0)
        self.assertEqual(len(fast.messages), 1)
        self.assertIn(fast, manager.active_connections)
        self.assertNotIn(slow, manager.active_connections)


if __name__ == "__main__":
    unittest.main()
