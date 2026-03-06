import tempfile
import unittest
from pathlib import Path

from config import settings
from services.resolve_service import ResolveService


class FakeProject:
    def __init__(self):
        self.render_settings = {}
        self.current_mode = 0
        self.current_format = ("wav", "Linear PCM")
        self.deleted_job_id = None

    def GetCurrentRenderMode(self):
        return self.current_mode

    def GetCurrentRenderFormatAndCodec(self):
        return {"format": self.current_format[0], "codec": self.current_format[1]}

    def GetRenderPresetList(self):
        return []

    def LoadRenderPreset(self, _preset_name):
        return False

    def GetRenderFormats(self):
        return {"wav": "wav"}

    def GetRenderCodecs(self, format_key):
        if format_key == "wav":
            return {"0": "Linear PCM"}
        return {}

    def SetCurrentRenderFormatAndCodec(self, format_key, codec):
        self.current_format = (format_key, codec)
        return True

    def SetCurrentRenderMode(self, mode):
        self.current_mode = mode
        return True

    def SetRenderSettings(self, render_settings):
        self.render_settings = dict(render_settings)
        return True

    def AddRenderJob(self):
        return "job-1"

    def StartRendering(self, _job_id):
        output_path = Path(self.render_settings["TargetDir"]) / f"{self.render_settings['CustomName']}.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"test-audio")
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, _job_id):
        return {"JobStatus": "Complete"}

    def DeleteRenderJob(self, job_id):
        self.deleted_job_id = job_id
        return True


class FakeResolve:
    def __init__(self, initial_page: str, report_initial_page: bool = True, fail_restore_attempts: int = 0):
        self.current_page = initial_page
        self.report_initial_page = report_initial_page
        self.fail_restore_attempts = fail_restore_attempts
        self.get_page_calls = 0
        self.open_calls: list[str] = []

    def GetCurrentPage(self):
        self.get_page_calls += 1
        if self.get_page_calls == 1 and not self.report_initial_page:
            return None
        return self.current_page

    def OpenPage(self, page_name: str):
        self.open_calls.append(page_name)
        if page_name == "edit" and self.fail_restore_attempts > 0:
            self.fail_restore_attempts -= 1
            return False
        self.current_page = page_name
        return True


class ResolveServiceRenderRestoreTests(unittest.TestCase):
    def setUp(self):
        self.original_temp_dir = settings.temp_dir

    def tearDown(self):
        settings.temp_dir = self.original_temp_dir

    def _make_service(self, fake_resolve: FakeResolve, fake_project: FakeProject) -> ResolveService:
        service = ResolveService()
        service._connected = True
        service._resolve = fake_resolve
        service.get_current_project = lambda: fake_project
        service.get_current_timeline = lambda: object()
        return service

    def test_retries_restore_to_original_page_after_render(self):
        fake_project = FakeProject()
        fake_resolve = FakeResolve(initial_page="edit", fail_restore_attempts=1)
        service = self._make_service(fake_resolve, fake_project)

        with tempfile.TemporaryDirectory() as tmpdir:
            settings.temp_dir = Path(tmpdir)
            output = service.render_current_timeline_audio("restore_retry")

        self.assertIsNotNone(output)
        self.assertEqual(fake_resolve.current_page, "edit")
        self.assertIn("deliver", fake_resolve.open_calls)
        self.assertEqual(fake_resolve.open_calls.count("edit"), 2)

    def test_uses_fallback_restore_page_when_resolve_does_not_report_current_page(self):
        fake_project = FakeProject()
        fake_resolve = FakeResolve(initial_page="cut", report_initial_page=False)
        service = self._make_service(fake_resolve, fake_project)

        with tempfile.TemporaryDirectory() as tmpdir:
            settings.temp_dir = Path(tmpdir)
            output = service.render_current_timeline_audio(
                "fallback_restore",
                fallback_restore_page="edit",
            )

        self.assertIsNotNone(output)
        self.assertEqual(fake_resolve.current_page, "edit")
        self.assertEqual(fake_resolve.open_calls[0], "deliver")
        self.assertEqual(fake_resolve.open_calls[-1], "edit")


if __name__ == "__main__":
    unittest.main()
