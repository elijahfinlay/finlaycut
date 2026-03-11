import tempfile
import unittest
from pathlib import Path

from config import settings
from services.clip_search_service import (
    ClipSearchService,
    ClipSearchStore,
    _build_analysis_key,
)


class _TrackingConnection:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        self.closed = True
        return self._conn.close()


class ClipSearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "clip_search.sqlite3"
        self.store = ClipSearchStore(db_path=self.db_path)

    def test_persist_transcript_words_groups_by_clip_item_id(self):
        count = self.store.persist_transcript_words(
            "sig-1",
            [
                {"clip_item_id": "clip-a", "word": "hello"},
                {"clip_item_id": "clip-a", "word": "world"},
                {"clip_item_id": "clip-b", "word": "sunset"},
                {"clip_item_id": "clip-b", "word": "[silence]"},
            ],
        )

        transcript_map = self.store.load_transcript_map("sig-1")
        self.assertEqual(count, 2)
        self.assertEqual(transcript_map["clip-a"], "hello world")
        self.assertEqual(transcript_map["clip-b"], "sunset")

    def test_persist_transcript_words_ignores_analysis_markers(self):
        count = self.store.persist_transcript_words(
            "sig-2",
            [
                {"clip_item_id": "clip-a", "word": "hello"},
                {"clip_item_id": "clip-a", "word": "[silence 0:01.00-0:01.40 / 10f]", "kind": "silence"},
                {"clip_item_id": "clip-a", "word": "uh", "kind": "audio_hesitation"},
                {"clip_item_id": "clip-a", "word": "world"},
            ],
        )

        transcript_map = self.store.load_transcript_map("sig-2")
        self.assertEqual(count, 1)
        self.assertEqual(transcript_map["clip-a"], "hello world")

    def test_persist_transcript_words_separates_same_file_by_source_span(self):
        count = self.store.persist_transcript_words(
            "sig-3",
            [
                {"file_path": "/tmp/interview.mov", "source_start_frame": 0, "source_end_frame": 24, "word": "hello"},
                {"file_path": "/tmp/interview.mov", "source_start_frame": 0, "source_end_frame": 24, "word": "there"},
                {"file_path": "/tmp/interview.mov", "source_start_frame": 240, "source_end_frame": 264, "word": "general"},
                {"file_path": "/tmp/interview.mov", "source_start_frame": 240, "source_end_frame": 264, "word": "kenobi"},
            ],
        )

        transcript_map = self.store.load_transcript_map("sig-3")
        self.assertEqual(count, 2)
        self.assertEqual(
            transcript_map["path:/tmp/interview.mov:0:24"],
            "hello there",
        )
        self.assertEqual(
            transcript_map["path:/tmp/interview.mov:240:264"],
            "general kenobi",
        )

    def test_partial_transcript_upsert_does_not_delete_other_clip_rows(self):
        self.store.persist_transcript_words(
            "sig-4",
            [
                {"clip_item_id": "clip-a", "word": "hello"},
                {"clip_item_id": "clip-b", "word": "sunset"},
            ],
            replace_existing=True,
        )

        count = self.store.persist_transcript_words(
            "sig-4",
            [
                {"clip_item_id": "clip-a", "word": "updated"},
                {"clip_item_id": "clip-a", "word": "intro"},
            ],
            replace_existing=False,
        )

        transcript_map = self.store.load_transcript_map("sig-4")
        self.assertEqual(count, 1)
        self.assertEqual(transcript_map["clip-a"], "updated intro")
        self.assertEqual(transcript_map["clip-b"], "sunset")

    def test_store_operations_close_sqlite_connections(self):
        original_connect = self.store._connect
        trackers: list[_TrackingConnection] = []

        def tracked_connect():
            tracker = _TrackingConnection(original_connect())
            trackers.append(tracker)
            return tracker

        self.store._connect = tracked_connect  # type: ignore[method-assign]

        self.store.load_transcript_map("sig-4")

        self.assertEqual(len(trackers), 1)
        self.assertTrue(trackers[0].closed)

    def test_refresh_storage_location_migrates_existing_db(self):
        old_value = settings.clip_search_storage_dir
        self.addCleanup(setattr, settings, "clip_search_storage_dir", old_value)

        old_dir = Path(self.temp_dir.name) / "old-store"
        new_dir = Path(self.temp_dir.name) / "new-store"
        settings.clip_search_storage_dir = str(old_dir)

        service = ClipSearchService(frame_dir=self.temp_dir.name)
        service.persist_transcript_payload(
            "sig-migrate",
            [{"clip_item_id": "clip-a", "word": "hello"}],
            replace_existing=True,
        )
        previous_db_path = service.get_storage_info()["effective_db_path"]

        settings.clip_search_storage_dir = str(new_dir)
        migration = service.refresh_storage_location(previous_db_path)

        self.assertTrue(migration["migrated"])
        self.assertEqual(migration["configured_db_path"], str(new_dir / "clip_search.sqlite3"))
        self.assertEqual(service.store.load_transcript_map("sig-migrate")["clip-a"], "hello")

    def test_query_and_overlay_use_persisted_analysis_rows(self):
        analysis_key = _build_analysis_key("sig-1", 0, 240, 1)
        self.store.start_analysis(
            analysis_key,
            project_name="Project",
            timeline_name="Timeline",
            timeline_signature="sig-1",
            scope_in_frame=0,
            scope_out_frame=240,
            track_index=1,
            scope_mode="timeline",
            provider_name="gemini",
            provider_model="gemini-2.5-flash",
        )
        self.store.complete_analysis(
            analysis_key,
            rows=[
                {
                    "clip_key": "clip-a",
                    "clip_item_id": "clip-a",
                    "clip_name": "Intro",
                    "track_index": 1,
                    "file_path": "/tmp/intro.mov",
                    "timeline_start_frame": 0,
                    "timeline_end_frame": 120,
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 5.0,
                    "source_start_frame": 0,
                    "source_end_frame": 120,
                    "summary_text": "Wide shot of a dog at sunset",
                    "shot_type": "Wide shot",
                    "labels": ["dog", "sunset", "beach"],
                    "labels_text": "dog, sunset, beach",
                    "ocr_text": "Finlay Cut",
                    "transcript_text": "this is the intro",
                    "provider_payload": {"summary": "Wide shot of a dog at sunset"},
                    "error_state": None,
                    "error_message": None,
                }
            ],
            transcript_available=True,
            provider_name="gemini",
            provider_model="gemini-2.5-flash",
        )

        results = self.store.query(analysis_key, "dog", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["clip_name"], "Intro")
        self.assertEqual(results[0]["summary_text"], "Wide shot of a dog at sunset")

        service = ClipSearchService(store=self.store, frame_dir=self.temp_dir.name)
        service._get_scope_info = lambda track_index=1: {  # type: ignore[method-assign]
            "project_name": "Project",
            "timeline_name": "Timeline",
            "timeline_signature": "sig-1",
            "scope_in_frame": 0,
            "scope_out_frame": 240,
            "scope_in_seconds": 0.0,
            "scope_out_seconds": 10.0,
            "scope_mode": "timeline",
            "track_index": 1,
            "fps": 24.0,
            "timeline_start_frame": 0,
            "timeline_end_frame": 240,
        }
        srt_content, entry_count = service.build_overlay_srt(track_index=1)
        self.assertEqual(entry_count, 1)
        self.assertIn("Wide shot", srt_content)
        self.assertIn("dog", srt_content)

    def test_clear_current_scope_removes_saved_analysis(self):
        analysis_key = _build_analysis_key("sig-clear", 0, 240, 1)
        self.store.start_analysis(
            analysis_key,
            project_name="Project",
            timeline_name="Timeline",
            timeline_signature="sig-clear",
            scope_in_frame=0,
            scope_out_frame=240,
            track_index=1,
            scope_mode="timeline",
            provider_name="gemini",
            provider_model="gemini-2.5-flash",
        )
        self.store.complete_analysis(
            analysis_key,
            rows=[
                {
                    "clip_key": "clip-a",
                    "clip_item_id": "clip-a",
                    "clip_name": "Intro",
                    "track_index": 1,
                    "file_path": "/tmp/intro.mov",
                    "timeline_start_frame": 0,
                    "timeline_end_frame": 120,
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 5.0,
                    "source_start_frame": 0,
                    "source_end_frame": 120,
                    "summary_text": "Wide shot of a dog at sunset",
                    "shot_type": "Wide shot",
                    "labels": ["dog", "sunset", "beach"],
                    "labels_text": "dog, sunset, beach",
                    "ocr_text": "Finlay Cut",
                    "transcript_text": "",
                    "provider_payload": {"summary": "Wide shot of a dog at sunset"},
                    "error_state": None,
                    "error_message": None,
                }
            ],
            transcript_available=False,
            provider_name="gemini",
            provider_model="gemini-2.5-flash",
        )

        service = ClipSearchService(store=self.store, frame_dir=self.temp_dir.name)
        service._get_scope_info = lambda track_index=1: {  # type: ignore[method-assign]
            "project_name": "Project",
            "timeline_name": "Timeline",
            "timeline_signature": "sig-clear",
            "scope_in_frame": 0,
            "scope_out_frame": 240,
            "scope_in_seconds": 0.0,
            "scope_out_seconds": 10.0,
            "scope_mode": "timeline",
            "track_index": 1,
            "fps": 24.0,
            "timeline_start_frame": 0,
            "timeline_end_frame": 240,
        }

        payload = service.clear_current_scope(track_index=1)

        self.assertTrue(payload["cleared"])
        self.assertEqual(payload["clip_count"], 1)
        self.assertIsNone(self.store.get_status(analysis_key, current_timeline_signature="sig-clear"))
        self.assertEqual(self.store.query(analysis_key, "", limit=10), [])


if __name__ == "__main__":
    unittest.main()
