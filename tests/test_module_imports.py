"""Check the facade imports and migration reload without a live Rhino."""
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PUBLIC_NAMES = """
ACTIVE_PRESET_ID BOUNDS CATEGORIES FLAGS GENERAL_KEY KEY PRESET_KEY Rhino
SETTINGS_READER SHEET_OMITTED_VISUAL_KEYS System VERSION_TEST_KEY apply_defaults
base64 capture_views check_version collect_capture configure_general configure_preset ctypes
embedded_presets find_type forms json lighting_overrides math os preparation_status
prepare_capture profile_bytes profile_path re request_capture resolved_settings
restore_general restore_preset restore_settings sc screenshot_files service_provider
services set_preset_data settings_schema shutil start_hidden status stop_monitor
subprocess time typed_array validate_settings version_test write_profile
""".split()


class ModuleImports(unittest.TestCase):
    def test_reload_of_legacy_facade_imports_modules_and_preserves_public_names(self):
        # An isolated interpreter models Rhino's cached single-file module.
        code = textwrap.dedent("""
            import importlib
            import sys
            import types

            sys.path.insert(0, sys.argv[1])
            for name in ('Rhino', 'System', 'System.Windows',
                         'System.Windows.Forms', 'scriptcontext'):
                sys.modules[name] = types.ModuleType(name)
            system = sys.modules['System']
            system.Windows = sys.modules['System.Windows']
            system.Windows.Forms = sys.modules['System.Windows.Forms']
            system.Reflection = types.SimpleNamespace(
                BindingFlags=types.SimpleNamespace(NonPublic=1, Instance=2))
            legacy = types.ModuleType('enscape_background')
            legacy.apply_defaults = lambda values: None
            sys.modules[legacy.__name__] = legacy

            enscape = importlib.reload(legacy)
            actual = sorted(name for name in vars(enscape) if not name.startswith('_'))
            assert actual == sorted(sys.argv[2:]), actual
            assert enscape is legacy
            assert enscape.apply_defaults.__module__ == 'enscape_defaults'
            assert enscape.version_test.__module__ == 'enscape_version_test'
            for name, value in vars(enscape).items():
                if isinstance(value, types.FunctionType):
                    assert value is getattr(sys.modules[value.__module__], name)
                    for symbol in value.__code__.co_names:
                        if symbol in vars(enscape):
                            assert symbol in value.__globals__, (name, symbol)
        """)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(SCRIPTS), *PUBLIC_NAMES],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
