"""Profile recovery after losing Rhino's in-memory state."""
import base64
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_settings import SOURCE, functions


class ProfileRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="enscape-profile-test-")
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.profile = folder / "settings.json"
        self.backup = folder / "profile-backup.json"
        self.before = b'\xef\xbb\xbf{ "Version":3, "Settings":{} }\r\n'
        self.profile.write_bytes(self.before)
        self.scope = functions("configure_general", "restore_general", "restore_settings",
                               "write_profile", "profile_bytes", "_write_profile_backup",
                               "_read_profile_backup", "check_version")
        self.sticky = {}
        self.scope.update(os=os, json=json, base64=base64, re=re,
            System=SimpleNamespace(Guid=SimpleNamespace(NewGuid=lambda:"test")),
            sc=SimpleNamespace(sticky=self.sticky), GENERAL_KEY="general", PRESET_KEY="preset",
            validate_settings=lambda values, **kwargs:values,
            profile_path=lambda:str(self.profile), _profile_backup_path=lambda:str(self.backup),
            _skill_file=lambda name:str(SOURCE.parents[1] / name), _installed_version=lambda:"1.2.3.4")

    def test_restore_after_sticky_loss_recovers_exact_original_bytes(self):
        self.scope["configure_general"]({"UseDlss":False})
        self.assertNotEqual(self.profile.read_bytes(), self.before)
        self.sticky.clear()
        self.scope["restore_settings"]()
        self.assertEqual(self.profile.read_bytes(), self.before)
        self.assertFalse(self.backup.exists())

    def test_existing_backup_blocks_new_profile_writes(self):
        self.backup.write_bytes(b"pending original backup")
        with self.assertRaisesRegex(ValueError, "[Rr]estore|backup"):
            self.scope["configure_general"]({"UseDlss":False})
        self.assertEqual(self.profile.read_bytes(), self.before)
        self.assertEqual(self.backup.read_bytes(), b"pending original backup")

    def test_backup_failure_prevents_profile_write(self):
        self.scope["_write_profile_backup"] = Mock(side_effect=OSError("backup disk failure"))
        with self.assertRaisesRegex(OSError, "backup disk failure"):
            self.scope["configure_general"]({"UseDlss":False})
        self.assertEqual(self.profile.read_bytes(), self.before)

    def test_originally_absent_profile_is_removed_on_recovery(self):
        self.profile.unlink()
        self.scope["configure_general"]({"UseDlss":False})
        self.sticky.clear()
        self.scope["restore_settings"]()
        self.assertFalse(self.profile.exists())
        self.assertFalse(self.backup.exists())

    def test_external_profile_change_retains_backup(self):
        self.scope["configure_general"]({"UseDlss":False})
        self.sticky.clear()
        self.profile.write_bytes(b"external change")
        with self.assertRaisesRegex(ValueError, "externally"):
            self.scope["restore_settings"]()
        self.assertEqual(self.profile.read_bytes(), b"external change")
        self.assertTrue(self.backup.exists())

    def test_version_reports_pending_recovery_until_verified_restore(self):
        self.assertFalse(self.scope["check_version"]().get("pending_profile_restore", False))
        self.scope["configure_general"]({"UseDlss":False})
        self.sticky.clear()
        self.assertTrue(self.scope["check_version"]().get("pending_profile_restore", False))
        self.scope["restore_settings"]()
        self.assertFalse(self.scope["check_version"]()["pending_profile_restore"])


if __name__ == "__main__":
    unittest.main()
