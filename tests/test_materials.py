"""Validate material requests without importing Rhino or writing a document."""
import importlib.util
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "enscape_materials.py"


class MaterialRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("material_requests", SOURCE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        def entry(kind, lower=None, upper=None, enum=None):
            return dict(type=kind, range=dict(min=lower, max=upper), enum=enum or [],
                        default=None, unit="factor")
        self.schema = dict(types={"Default": ["DiffuseColor", "Roughness", "DiffuseTexturePath",
                                             "BumpMapType", "IsSolidGlass", "DiffuseTextureSize"],
                                  "Water": ["WaterWaveHeight"]}, parameters={
            "DiffuseColor": entry("Color", 0, 255),
            "Roughness": entry("Double", 0, 1),
            "DiffuseTexturePath": entry("String"),
            "BumpMapType": entry("BumpMapKind", enum=["Undefined", "Bump", "Normal", "Displacement"]),
            "IsSolidGlass": entry("Boolean"),
            "DiffuseTextureSize": entry("Vector2d", 0),
            "WaterWaveHeight": entry("Double", 0, 1),
        })
        self.schema_patch = patch.object(self.module, "materials_schema", return_value=self.schema)
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)

    def test_valid_values_keep_enum_names_and_normalize_sequences(self):
        values = {"DiffuseColor": (255, 0, 32), "Roughness": 0.25,
                  "BumpMapType": "Normal", "IsSolidGlass": False,
                  "DiffuseTextureSize": (1, 2)}
        result = self.module.validate_material("Default", values)
        self.assertEqual(result["DiffuseColor"], [255, 0, 32])
        self.assertEqual(result["BumpMapType"], "Normal")
        self.assertEqual(values["DiffuseColor"], (255, 0, 32))

    def test_invalid_values_fail_before_writing(self):
        cases = [{"missing": 1}, {"Roughness": True}, {"Roughness": -0.1},
                 {"Roughness": 1.1}, {"Roughness": math.nan}, {"Roughness": math.inf},
                 {"DiffuseColor": [256, 0, 0]}, {"DiffuseColor": [1.5, 0, 0]},
                 {"DiffuseColor": [True, 0, 0]}, {"DiffuseColor": [1, 2]},
                 {"BumpMapType": 2}, {"BumpMapType": "normal"},
                 {"IsSolidGlass": 1}, {"DiffuseTexturePath": None},
                 {"DiffuseTextureSize": [0, 1]}, {"DiffuseTextureSize": [math.inf, 1]},
                 {"WaterWaveHeight": 0.5}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.module.validate_material("Default", values)

    def test_unknown_type_and_nondict_fail(self):
        for kind, values in [(0, {}), ("generic", {}), ("Default", []), ("Default", None)]:
            with self.subTest(kind=kind, values=values), self.assertRaises(ValueError):
                self.module.validate_material(kind, values)

    def test_texture_must_be_an_existing_absolute_file_or_empty_to_clear(self):
        with tempfile.TemporaryDirectory(prefix="enscape-material-test-") as folder:
            texture = Path(folder) / "texture.png"
            texture.write_bytes(b"test")
            self.assertEqual(self.module.validate_material("Default", {"DiffuseTexturePath": str(texture)}),
                             {"DiffuseTexturePath": str(texture)})
            for path in (str(texture) + ".missing", folder, "texture.png"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.module.validate_material("Default", {"DiffuseTexturePath": path})
        self.assertEqual(self.module.validate_material("Default", {"DiffuseTexturePath": ""}),
                         {"DiffuseTexturePath": ""})

    def test_create_builds_validated_request_before_mutation(self):
        with patch.object(self.module, "_find_material", return_value=None), \
             patch.object(self.module, "_write_material", return_value={"name": "test"}) as write:
            result = self.module.create_material("Default", "test", {"Roughness": 0.2})
            self.assertEqual(result, {"name": "test"})
            write.assert_called_once_with(None, "Default", "test", {"Roughness": 0.2})
            write.reset_mock()
            with self.assertRaises(ValueError):
                self.module.create_material("Default", "test", {"Roughness": 2})
            write.assert_not_called()

    def test_create_rejects_duplicate_names_and_bad_names(self):
        with patch.object(self.module, "_find_material", return_value=object()), \
             patch.object(self.module, "_write_material") as write:
            with self.assertRaises(ValueError):
                self.module.create_material("Default", "existing", {})
            for name in (None, "", " ", "bad\nname"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.module.create_material("Default", name, {})
            write.assert_not_called()

    def test_edit_reads_type_and_validates_whole_request(self):
        target = object()
        with patch.object(self.module, "_find_material", return_value=target), \
             patch.object(self.module, "_read_material", return_value={"type": "Default"}), \
             patch.object(self.module, "_write_material", return_value={"readback": True}) as write:
            self.assertEqual(self.module.edit_material("test", {"Roughness": 0.1}), {"readback": True})
            write.assert_called_once_with(target, "Default", "test", {"Roughness": 0.1})
            write.reset_mock()
            with self.assertRaises(ValueError):
                self.module.edit_material("test", {"Roughness": 0.1, "typo": 1})
            write.assert_not_called()

    def test_edit_can_change_type_with_validation_for_destination(self):
        target = object()
        with patch.object(self.module, "_find_material", return_value=target), \
             patch.object(self.module, "_read_material", return_value={"type": "Default"}), \
             patch.object(self.module, "_write_material") as write:
            self.module.edit_material("test", {"MaterialType": "Water", "WaterWaveHeight": 0.5})
            write.assert_called_once_with(target, "Water", "test", {"WaterWaveHeight": 0.5})
            write.reset_mock()
            with self.assertRaises(ValueError):
                self.module.edit_material("test", {"MaterialType": "Water", "Roughness": 0.5})
            write.assert_not_called()

    def test_legacy_type_uses_authoritative_v4_slot_without_migration(self):
        material = SimpleNamespace(Version=4, MaterialType="Default")
        system = SimpleNamespace(Enum=SimpleNamespace(ToObject=lambda enum, number: "SelfIlluminated" if number == 6 else "wrong"))
        definitions = {"MaterialType": SimpleNamespace(GetValue=lambda target: 6)}
        with patch.object(self.module, "_installed_type", return_value=object()):
            self.assertEqual(self.module._effective_type(material, system, definitions), "SelfIlluminated")
        self.assertEqual(material.MaterialType, "Default")
        self.assertEqual(material.Version, 4)

    def test_unknown_numeric_bounds_are_not_invented(self):
        self.assertEqual(self.module._range("FutureField", "Double"), (None, None, "unknown"))

    def test_empty_edit_is_rejected_without_lookup(self):
        with patch.object(self.module, "_find_material") as lookup:
            with self.assertRaises(ValueError):
                self.module.edit_material("test", {})
            lookup.assert_not_called()

    def test_refresh_explicitly_rebuilds_then_flushes_the_bound_scene(self):
        export = Mock()
        with patch.object(self.module, "_active_export", return_value=(export, 123)):
            self.assertEqual(self.module.refresh_materials(),
                             {"scene_rebuilt": True, "document_serial_number": 123})
        self.assertEqual([call[0] for call in export.method_calls], ["CreateWorld", "Flush"])


if __name__ == "__main__":
    unittest.main()
