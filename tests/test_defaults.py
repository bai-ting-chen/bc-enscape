"""Sheet parsing, version reporting and reversible application checks."""
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open
from test_settings import functions

class Version(unittest.TestCase):
    def test_mismatch_returns_values_without_raising(self):
        scope=functions("check_version")
        scope.update(re=re,_installed_version=lambda:"1.2.3.4",_skill_file=lambda name:name,
                     os=SimpleNamespace(path=SimpleNamespace(exists=lambda path:False)),_profile_backup_path=lambda:"backup",
                     open=mock_open(read_data="Tested with Enscape 9.8.7.6.\n"))
        self.assertEqual(scope["check_version"](),dict(installed="1.2.3.4",tested="9.8.7.6",match=False,pending_profile_restore=False))
    def test_match_reads_the_document(self):
        scope=functions("check_version")
        scope.update(re=re,_installed_version=lambda:"1.2.3.4",_skill_file=lambda name:name,
                     os=SimpleNamespace(path=SimpleNamespace(exists=lambda path:False)),_profile_backup_path=lambda:"backup",
                     open=mock_open(read_data="Tested with Enscape 1.2.3.4.\n"))
        self.assertTrue(scope["check_version"]()["match"])

class Sheet(unittest.TestCase):
    def scope(self,text):
        scope=functions("_read_defaults")
        scope.update(json=json,_skill_file=lambda name:name,open=mock_open(read_data=text))
        return scope
    def test_types_and_allowed_column_are_independent(self):
        scope=self.scope('| Key | Value | Allowed |\n|---|---|---|\n| Enum | None | ignored |\n| Empty | "" | ignored |\n| Flag | false | ignored |\n| Number | 12.5 | ignored |\n')
        self.assertEqual(scope["_read_defaults"](),dict(Enum="None",Empty="",Flag=False,Number=12.5))
    def test_duplicate_names_are_rejected(self):
        scope=self.scope('| Key | Value | Allowed |\n|---|---|---|\n| Size | 1000 | ignored |\n| Size | 1500 | ignored |\n')
        with self.assertRaisesRegex(ValueError,"Size"):scope["_read_defaults"]()
    def test_missing_leading_pipe_names_the_line(self):
        scope=self.scope('| Key | Value | Allowed |\n|---|---|---|\nSunPower | 80 | ignored |\n| LightBrightness | 100 | ignored |\n')
        with self.assertRaisesRegex(ValueError,"line 3.*SunPower"):
            scope["_read_defaults"]()
    def test_blank_line_ends_a_table_before_prose(self):
        scope=self.scope('| Key | Value | Allowed |\n|---|---|---|\n| SunPower | 80 | ignored |\n\nNext category.\n')
        self.assertEqual(scope["_read_defaults"](), {"SunPower":80})
    def test_only_documented_omissions_are_optional(self):
        scope=functions("_check_sheet_complete","SHEET_OMITTED_VISUAL_KEYS")
        keys=scope["SHEET_OMITTED_VISUAL_KEYS"] | {"SunPower"}
        scope["settings_schema"]=lambda:{"categories":{"output":[{"key":key} for key in keys],
                                                       "general":[{"key":"UseDlss"}]}}
        scope["_check_sheet_complete"]({"SunPower":80})
        with self.assertRaisesRegex(ValueError,"SunPower"):
            scope["_check_sheet_complete"]({})

class Application(unittest.TestCase):
    def setUp(self):
        self.scope=functions("apply_defaults","_split_settings","validate_settings","_check_sheet_complete","SHEET_OMITTED_VISUAL_KEYS")
        entries=[dict(key="SunPower",type="Single",enum={},writable=True,range=dict(min=0,max=500))]
        general=[dict(key="UseDlss",type="Boolean",enum={},writable=True,range={})]
        import math
        self.scope.update(math=math,settings_schema=lambda:{"categories":{"illumination":entries,"general":general}},
            _read_defaults=lambda:{"SunPower":80,"UseDlss":True},
            sc=SimpleNamespace(sticky={}),PRESET_KEY="preset",GENERAL_KEY="general",
            os=SimpleNamespace(path=SimpleNamespace(exists=lambda path:False)),_profile_backup_path=lambda:"backup",
            configure_preset=Mock(),configure_general=Mock(),restore_settings=Mock())
    def test_invalid_merge_writes_nothing(self):
        with self.assertRaisesRegex(ValueError,"SunPower"):self.scope["apply_defaults"]({"SunPower":501})
        self.scope["configure_general"].assert_not_called()
        self.scope["configure_preset"].assert_not_called()
    def test_corrupted_sun_row_prevents_both_writes(self):
        text='| Key | Value | Allowed |\n|---|---|---|\nSunPower | 80 | ignored |\n| LightBrightness | 100 | ignored |\n\n| Key | Value | Allowed |\n|---|---|---|\n| UseDlss | true | ignored |\n'
        self.scope["_read_defaults"]=Sheet().scope(text)["_read_defaults"]
        with self.assertRaisesRegex(ValueError,"SunPower"):
            self.scope["apply_defaults"]({})
        self.scope["configure_general"].assert_not_called()
        self.scope["configure_preset"].assert_not_called()
    def test_missing_visual_sheet_key_cannot_be_supplied_by_override(self):
        self.scope["_read_defaults"]=lambda:{"UseDlss":True}
        with self.assertRaisesRegex(ValueError,"missing.*SunPower"):
            self.scope["apply_defaults"]({"SunPower":200})
        self.scope["configure_general"].assert_not_called()
        self.scope["configure_preset"].assert_not_called()
    def test_override_and_visual_failure_restore(self):
        self.scope["configure_preset"].side_effect=OSError("write failed")
        with self.assertRaises(OSError):self.scope["apply_defaults"]({"SunPower":200})
        self.scope["configure_general"].assert_called_once_with({"UseDlss":True})
        self.scope["restore_settings"].assert_called_once()
    def test_return_is_the_sheet_plus_overrides(self):
        self.assertEqual(self.scope["apply_defaults"]({"SunPower":200}),{"SunPower":200,"UseDlss":True})
    def test_restore_order(self):
        scope=functions("restore_settings")
        order=[]
        scope.update(sc=SimpleNamespace(sticky={"preset":{},"general":{}}),PRESET_KEY="preset",GENERAL_KEY="general",
            restore_preset=lambda:order.append("preset"),restore_general=lambda:order.append("general"))
        scope["restore_settings"]()
        self.assertEqual(order,["preset","general"])
    def test_preset_restore_failure_still_attempts_general(self):
        scope=functions("restore_settings")
        general=Mock()
        scope.update(sc=SimpleNamespace(sticky={"preset":{},"general":{}}),PRESET_KEY="preset",GENERAL_KEY="general",
                     restore_preset=Mock(side_effect=ValueError("document closed")),restore_general=general)
        with self.assertRaisesRegex(Exception,"preset.*document closed"):
            scope["restore_settings"]()
        general.assert_called_once()
    def test_both_restore_failures_are_reported(self):
        scope=functions("restore_settings")
        scope.update(sc=SimpleNamespace(sticky={"preset":{},"general":{}}),PRESET_KEY="preset",GENERAL_KEY="general",
                     restore_preset=Mock(side_effect=ValueError("document closed")),
                     restore_general=Mock(side_effect=OSError("profile locked")))
        with self.assertRaisesRegex(Exception,"preset.*document closed.*general.*profile locked"):
            scope["restore_settings"]()

class VersionProbe(unittest.TestCase):
    def test_probe_changes_an_enum_or_boolean(self):
        scope = functions("_probe_value")
        self.assertEqual(scope["_probe_value"](dict(enum={"HIGH":2,"DRAFT":0},value="HIGH")), "DRAFT")
        self.assertFalse(scope["_probe_value"](dict(enum={},type="Boolean",value=True)))

    def test_numeric_probe_respects_known_bounds_and_skips_unknown(self):
        scope = functions("_probe_value")
        self.assertEqual(scope["_probe_value"](dict(enum={},type="Single",value=100,range=dict(min=0,max=100))), 0)
        self.assertIsNone(scope["_probe_value"](dict(enum={},type="Single",value=5,range=dict(min="unknown",max="unknown"))))

    def test_capture_wait_does_not_submit_twice(self):
        scope = functions("_version_capture")
        state = dict(phase="capturing",items={})
        scope.update(collect_capture=lambda:{"capture_task":"WaitingForActivation"},restore_settings=Mock())
        scope["_version_capture"](state)
        self.assertEqual(state["phase"], "capturing")
        scope["restore_settings"].assert_not_called()

    def test_category_readback_failure_restores_and_names_the_failed_key(self):
        scope = functions("_version_category", "_probe_value")
        entries = [dict(key="UseDlss",type="Boolean",enum={},value=True,writable=True)]
        scope.update(settings_schema=lambda:{"categories":{"general":entries}},
                     _read_defaults=lambda:{"UseDlss":True},configure_general=Mock(),restore_settings=Mock())
        state = dict(items={})
        with self.assertRaisesRegex(ValueError,"UseDlss"):
            scope["_version_category"](state,"general")
        scope["restore_settings"].assert_called_once()
        self.assertFalse(state["items"]["general"]["passed"])

    def test_general_probe_prefers_render_sheet_keys(self):
        scope = functions("_version_category", "_probe_value")
        entries = [dict(key=key,type="Boolean",enum={},value=True,writable=True)
                   for key in ("CustomWindowCaptionEnabled","UseDlss")]
        def configure(changes):
            for entry in entries:
                if entry["key"] in changes: entry["value"] = changes[entry["key"]]
        scope.update(settings_schema=lambda:{"categories":{"general":entries}},
                     _read_defaults=lambda:{"UseDlss":True},configure_general=configure,restore_settings=Mock())
        state = dict(items={})
        scope["_version_category"](state,"general")
        self.assertEqual(state["items"]["general"]["key"], "UseDlss")

if __name__=="__main__":unittest.main()
