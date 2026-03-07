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


if __name__ == "__main__":
    unittest.main()
