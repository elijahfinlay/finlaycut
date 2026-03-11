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

    async def test_timeline_watch_notifications_are_tracked_per_connection(self):
        manager = ConnectionManager()
        watcher_a = _FastWebSocket()
        watcher_b = _FastWebSocket()
        manager.active_connections = {watcher_a, watcher_b}

        manager.set_signature_watch(watcher_a, "sig-a", "Timeline A")
        manager.set_signature_watch(watcher_b, "sig-b", "Timeline B")

        await manager.notify_timeline_watchers("sig-b", "Timeline B")

        self.assertEqual(
            watcher_a.messages,
            [{
                "type": "timeline_changed",
                "timeline_signature": "sig-b",
                "timeline_name": "Timeline B",
            }],
        )
        self.assertEqual(watcher_b.messages, [])


if __name__ == "__main__":
    unittest.main()
