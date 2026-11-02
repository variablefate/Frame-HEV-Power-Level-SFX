"""Updater checks: version ordering, checksum parsing, safe extraction, and the
machine-readable report the control panel relies on."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frame_hev_update", ROOT / "tools/update.py")
assert SPEC and SPEC.loader
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)

HEV_SPEC = importlib.util.spec_from_file_location("frame_hev_for_update", ROOT / "frame_hev.py")
assert HEV_SPEC and HEV_SPEC.loader
frame_hev = importlib.util.module_from_spec(HEV_SPEC)
HEV_SPEC.loader.exec_module(frame_hev)


class VersionTests(unittest.TestCase):
    def test_newer_and_prerelease_ordering(self):
        self.assertGreater(update.version_key("0.3.0"), update.version_key("0.2.9"))
        self.assertGreater(update.version_key("0.2.10"), update.version_key("0.2.9"))
        self.assertGreater(update.version_key("1.0.0"), update.version_key("0.99.99"))
        self.assertGreater(update.version_key("0.2.0"), update.version_key("0.2.0-rc.1"))
        self.assertEqual(update.version_key("v0.2.0"), update.version_key("0.2.0"))
        with self.assertRaises(update.UpdateError):
            update.version_key("main")

    def test_checksum_entry_selection(self):
        text = "a" * 64 + "  frame-hev-0.2.0.zip\n" + "b" * 64 + "  other.zip\n"
        self.assertEqual(update.checksum_from(text, "frame-hev-0.2.0.zip"), "a" * 64)
        self.assertIsNone(update.checksum_from(text, "frame-hev-9.9.9.zip"))
        self.assertIsNone(update.checksum_from("not-a-checksum  frame-hev-0.2.0.zip\n",
                                               "frame-hev-0.2.0.zip"))

    def test_installed_version_reads_the_repository_root_from_tools(self):
        self.assertEqual(update.installed_version(ROOT / "tools"),
                         (ROOT / "VERSION").read_text().strip())

    def test_default_repository_matches_the_runtime_constant(self):
        self.assertEqual(update.DEFAULT_REPO, frame_hev.DEFAULT_UPDATE_REPO)


class ExtractionTests(unittest.TestCase):
    def test_safe_entries_keep_content_and_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            archive = temporary / "app.zip"
            with zipfile.ZipFile(archive, "w") as source:
                info = zipfile.ZipInfo("frame-hev-0.2.0/tools/install.py")
                info.external_attr = 0o755 << 16
                source.writestr(info, "print('ok')\n")
            root = update.extract(archive, temporary / "out")
            script = root / "frame-hev-0.2.0/tools/install.py"
            self.assertEqual(script.read_text(), "print('ok')\n")
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)

    def test_entry_outside_the_target_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            archive = temporary / "evil.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("../escape.txt", "no")
            with self.assertRaises(update.UpdateError):
                update.extract(archive, temporary / "out")
            self.assertFalse((temporary / "escape.txt").exists())


class ReportTests(unittest.TestCase):
    def report(self, installed, latest):
        with (mock.patch.object(update, "latest_release", return_value=latest),
              mock.patch.object(update, "installed_version", return_value=installed)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = update.main(["--json"])
        return code, json.loads(out.getvalue())

    def test_newer_release_is_reported(self):
        code, report = self.report("0.2.0", "0.3.0")
        self.assertEqual(code, 0)
        self.assertTrue(report["newer"])
        self.assertEqual(report["latest"], "0.3.0")
        self.assertEqual(report["installed"], "0.2.0")

    def test_current_release_is_not_an_update(self):
        code, report = self.report("0.3.0", "0.3.0")
        self.assertEqual(code, 0)
        self.assertFalse(report["newer"])

    def test_repository_errors_become_a_json_error(self):
        with (mock.patch.object(update, "latest_release",
                                side_effect=update.UpdateError("no releases")),
              mock.patch.object(update, "installed_version", return_value="0.2.0")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = update.main(["--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["error"], "no releases")

    def test_json_apply_stays_a_single_document(self):
        with (mock.patch.object(update, "latest_release", return_value="9.9.9"),
              mock.patch.object(update, "installed_version", return_value="0.2.0"),
              mock.patch.object(update, "apply_update", return_value="9.9.9")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = update.main(["--json", "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["applied"], "9.9.9")


if __name__ == "__main__":
    unittest.main()
