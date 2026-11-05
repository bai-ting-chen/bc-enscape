"""Host checks for validation and capture collection without a live renderer."""
import ast
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

SOURCE = Path(__file__).resolve().parents[1]/"scripts"/"enscape_background.py"
ROOT = Path(tempfile.gettempdir())

def functions(*names):
    tree = ast.parse(SOURCE.read_text())
    selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    scope = {}
    exec(compile(selected, str(SOURCE), "exec"), scope)
    return scope

class Validation(unittest.TestCase):
    def setUp(self):
        self.scope = functions("validate_settings")
        self.scope["math"] = math
        entries = [
            dict(key="Size", type="UInt32", enum={}, writable=True, range=dict(min=500,max=8192)),
            dict(key="UnknownFloat", type="Single", enum={}, writable=True, range=dict(min="unknown",max="unknown")),
            dict(key="Mode", type="RenderingStyle", enum={"WHITE":1}, writable=True, range={}),
            dict(key="Flags", type="Categories", enum={"Vegetation":1,"People":2}, flags=True, writable=True, range={}),
            dict(key="Format", type="String", enum={}, writable=False, range={}),
            dict(key="Bool", type="Boolean", enum={}, writable=True, range={}),
            dict(key="NullablePath",type="String",enum={},writable=True,range={},nullable=True),
        ]
        self.scope["settings_schema"] = lambda: {"categories":{"output":entries,"general":[]}}
    def test_invalid_values(self):
        for values in ({"Size":499}, {"Size":8193}, {"Size":True}, {"Mode":1}, {"Mode":"BAD"}, {"Bool":1}, {"Format":"png"}, {"missing":1}, {"UnknownFloat":float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.scope["validate_settings"](values)
    def test_nullable_factory_default(self):
        self.assertEqual(self.scope["validate_settings"]({"NullablePath":None}), {"NullablePath":None})
    def test_flag_names_combine_without_accepting_integers(self):
        self.assertEqual(self.scope["validate_settings"]({"Flags":"Vegetation, People"}), {"Flags":3})
        with self.assertRaises(ValueError):
            self.scope["validate_settings"]({"Flags":3})
    def test_unknown_bound_keeps_numeric_values(self):
        self.assertEqual(self.scope["validate_settings"]({"UnknownFloat":-123}), {"UnknownFloat":-123})
    def test_single_storage_overflow(self):
        with self.assertRaises(ValueError):
            self.scope["validate_settings"]({"UnknownFloat":1e100})

class Collection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix="capture-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/"capture"
        self.target = self.root/"outputs"
        self.source.mkdir()
        self.target.mkdir()
        self.scope = functions("collect_capture")
        task = SimpleNamespace(IsCompleted=True,IsFaulted=False,IsCanceled=False,Status="RanToCompletion")
        self.state = dict(capture_task=task,files_before=set(),output_path=str(self.target/"result.png"),capture_started=0)
        class Bitmap:
            def __init__(self, path):
                self.image = Image.open(path)
                self.Width,self.Height=self.image.size
            def Dispose(self):self.image.close()
        import os,shutil,time
        self.scope.update(os=os,shutil=shutil,time=time,System=SimpleNamespace(Drawing=SimpleNamespace(Bitmap=Bitmap)),sc=SimpleNamespace(sticky={"key":self.state}),KEY="key",status=lambda:{"running":True},screenshot_files=lambda:{str(p) for p in self.source.iterdir()})
    def images(self):
        for suffix in ("", "_depth", "_normal"):
            Image.new("RGB",(500,500)).save(self.source/("capture"+suffix+".png"))
    def test_collects_every_file_and_removes_temp(self):
        self.images()
        result=self.scope["collect_capture"]()
        self.assertEqual(len(result["files"]),3)
        self.assertEqual({tuple(f["pixels"]) for f in result["files"]},{(500,500)})
        self.assertFalse(self.source.exists())
        self.assertNotIn("capture_task",self.state)
    def test_collision_preserves_all_sources(self):
        self.images()
        collision=self.target/"result_depth.png"
        collision.write_bytes(b"keep")
        with self.assertRaises(ValueError):self.scope["collect_capture"]()
        self.assertEqual(collision.read_bytes(),b"keep")
        self.assertEqual(len(list(self.source.iterdir())),3)
        self.assertEqual(len(list(self.target.iterdir())),1)
    def test_mixed_dimensions_preserve_sources(self):
        self.images()
        Image.new("RGB",(501,500)).save(self.source/"capture_depth.png")
        with self.assertRaises(ValueError):self.scope["collect_capture"]()
        self.assertEqual(len(list(self.source.iterdir())),3)
        self.assertEqual(len(list(self.target.iterdir())),0)

class GeneralPersistence(unittest.TestCase):
    def test_failed_write_keeps_original_bytes(self):
        import builtins, os
        with tempfile.TemporaryDirectory(dir=ROOT,prefix="profile-test-") as folder:
            path=Path(folder)/"settings.json"
            before=b'{"Version":3,"Settings":{}}'
            path.write_bytes(before)
            scope=functions("configure_general","restore_general","write_profile")
            sticky={}
            scope.update(os=os,json=json,System=SimpleNamespace(Guid=SimpleNamespace(NewGuid=lambda:"test")),sc=SimpleNamespace(sticky=sticky),GENERAL_KEY="general",PRESET_KEY="preset",validate_settings=lambda x,**kwargs:x,profile_path=lambda:str(path),profile_bytes=lambda:path.read_bytes())
            class BrokenStream:
                def __init__(self,file):self.file=file
                def __enter__(self):return self
                def __exit__(self,*args):self.file.close()
                def write(self,data):
                    self.file.write(data[:5])
                    raise OSError("simulated disk write failure")
            def broken_open(name,mode):
                file=builtins.open(name,mode)
                return BrokenStream(file) if mode in ("wb","xb") else file
            scope["open"]=broken_open
            with self.assertRaises(OSError):scope["configure_general"]({"UseDlss":False})
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual([p.name for p in Path(folder).iterdir()],["settings.json"])

if __name__=="__main__":unittest.main(verbosity=2)
