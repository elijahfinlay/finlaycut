import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from api.routes import resolve_status


class ResolveRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_link_clips_returns_success(self):
        with (
            patch.object(resolve_status.resolve_service, "_connected", True),
            patch.object(resolve_status.resolve_service, "_resolve", object()),
            patch.object(
                resolve_status.resolve_service,
                "link_all_clips",
                return_value={"success": True, "linked_pairs": 3},
            ) as link_all,
        ):
            response = self.client.post("/api/resolve/link-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["linked_pairs"], 3)
        self.assertIn("Linked 3 matching video and audio clip pair", payload["message"])
        link_all.assert_called_once()

    def test_link_clips_requires_connection(self):
        with (
            patch.object(resolve_status.resolve_service, "_connected", False),
            patch.object(resolve_status.resolve_service, "_resolve", None),
        ):
            response = self.client.post("/api/resolve/link-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "Not connected to DaVinci Resolve")

    def test_link_clips_retries_stale_connection_before_failing(self):
        with (
            patch.object(resolve_status.resolve_service, "_connected", False),
            patch.object(resolve_status.resolve_service, "_resolve", object()),
            patch.object(resolve_status.resolve_service, "try_connect", return_value=True) as try_connect,
            patch.object(
                resolve_status.resolve_service,
                "link_all_clips",
                return_value={"success": True, "linked_pairs": 1},
            ) as link_all,
        ):
            response = self.client.post("/api/resolve/link-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["linked_pairs"], 1)
        try_connect.assert_called_once()
        link_all.assert_called_once()

    def test_select_timeline_returns_updated_status(self):
        with (
            patch.object(resolve_status.resolve_service, "_connected", True),
            patch.object(resolve_status.resolve_service, "_resolve", object()),
            patch.object(
                resolve_status.resolve_service,
                "set_current_timeline_by_index",
                return_value={"success": True, "timeline_index": 2, "timeline_name": "Timeline B"},
            ) as switch_timeline,
            patch.object(
                resolve_status.resolve_service,
                "get_status",
                return_value={
                    "connected": True,
                    "project": {"name": "Project", "timeline_count": 2},
                    "timeline": {"name": "Timeline B"},
                    "using_cached": False,
                },
            ),
        ):
            response = self.client.post("/api/resolve/timeline/select", json={"timeline_index": 2})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["timeline_name"], "Timeline B")
        self.assertEqual(payload["status"]["timeline"]["name"], "Timeline B")
        switch_timeline.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
