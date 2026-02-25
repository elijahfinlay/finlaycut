"""Stock footage service: Pexels API integration for B-roll suggestions."""

from typing import Optional
import httpx
from config import settings


class StockService:
    PEXELS_BASE = "https://api.pexels.com"

    async def search_videos(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape"
    ) -> list[dict]:
        """
        Search Pexels for stock video footage.
        Returns list of video results with preview URLs.
        """
        if not settings.pexels_api_key:
            return []

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.PEXELS_BASE}/videos/search",
                headers={"Authorization": settings.pexels_api_key},
                params={
                    "query": query,
                    "per_page": per_page,
                    "orientation": orientation,
                    "size": "medium"
                }
            )
            if response.status_code != 200:
                return []

            data = response.json()
            results = []
            for video in data.get("videos", []):
                # Get the best quality file
                files = video.get("video_files", [])
                best_file = None
                for f in files:
                    if f.get("quality") == "hd":
                        best_file = f
                        break
                if not best_file and files:
                    best_file = files[0]

                results.append({
                    "id": video["id"],
                    "url": video["url"],
                    "preview_url": video.get("image", ""),
                    "download_url": best_file["link"] if best_file else None,
                    "width": best_file.get("width", 0) if best_file else 0,
                    "height": best_file.get("height", 0) if best_file else 0,
                    "duration": video.get("duration", 0),
                    "user": video.get("user", {}).get("name", "Unknown")
                })

            return results

    async def download_video(self, url: str, output_path: str) -> str:
        """Download a stock video to the specified path."""
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)
        return output_path


stock_service = StockService()
