"""Panel helpers that need no display. Skipped where GTK3 bindings are absent."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

try:
    import gi
    gi.require_version("Gtk", "3.0")
    HAVE_GTK = True
except (ImportError, ValueError):
    HAVE_GTK = False


@unittest.skipUnless(HAVE_GTK, "GTK3 Python bindings are not installed")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("frame_hev_panel", ROOT / "frame_hev_panel.py")
        assert spec and spec.loader
        cls.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.panel)

    def test_settings_lookup_and_number_fallback(self):
        text = "FRAME_HEV_VOLUME=0.7\nFRAME_HEV_THRESHOLDS=50,25\n"
        self.assertEqual(self.panel.configured(text, "FRAME_HEV_THRESHOLDS", "off"), "50,25")
        self.assertEqual(self.panel.configured(text, "FRAME_HEV_MISSING", "fallback"), "fallback")
        values = self.panel.frame_hev.parse_settings(text)
        self.assertEqual(self.panel.number(values, "FRAME_HEV_VOLUME", 0.65), 0.7)
        self.assertEqual(self.panel.number(values, "FRAME_HEV_MISSING", 120), 120.0)
        self.assertEqual(self.panel.number({"FRAME_HEV_VOLUME": "bad"}, "FRAME_HEV_VOLUME", 0.65), 0.65)

    def test_service_state_returns_systemd_words_not_raw_errors(self):
        with mock.patch.object(self.panel.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, stdout="active\n", stderr="")):
            self.assertEqual(self.panel.service_state(), ("active", None))
        with mock.patch.object(self.panel.subprocess, "run",
                               return_value=subprocess.CompletedProcess(
                                   [], 1, stdout="", stderr="Failed to connect to user scope bus\n")):
            self.assertEqual(self.panel.service_state(),
                             ("unknown", "Failed to connect to user scope bus"))

    def test_save_settings_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "environment"
            with mock.patch.object(self.panel, "CONFIG", target):
                self.panel.save_settings("FRAME_HEV_VOLUME=0.5")
                self.assertEqual(target.read_text(), "FRAME_HEV_VOLUME=0.5\n")
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_version_reads_the_runtime_file(self):
        with mock.patch.object(self.panel, "APP", ROOT):
            self.assertEqual(self.panel.version(), (ROOT / "VERSION").read_text().strip())


if __name__ == "__main__":
    unittest.main()
