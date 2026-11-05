"""Synthetic compositor checks; no Rhino, renders or retained image fixtures."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import BlendMode


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image_overlay.py"


class ImageOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="image-overlay-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("image_overlay", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def image(self, name, pixels):
        path = self.root / name
        Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(path)
        return name

    def layer(self, path, mode="normal", opacity=1, **options):
        return dict(path=path, mode=mode, opacity=opacity, **options)

    def run_recipe(self, layers, **options):
        recipe = self.root / "recipe.json"
        recipe.write_text(json.dumps(dict(layers=layers, **options)), encoding="utf-8")
        return self.module.main(self.root / "output", recipe)

    def test_channels_cli_keeps_grid_and_psd_layer_properties(self):
        base = self.image("render.png", np.full((4, 6, 3), 160))
        depth = self.image("depth.png", np.full((4, 6, 3), 128))
        ids = self.image("object-id.png", np.full((4, 6, 3), 192))
        recipe = self.root / "channels.json"
        recipe.write_text(json.dumps({"layers": [self.layer(base),
            self.layer(depth, "multiply"), self.layer(ids, "overlay", 0.6)]}))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT),
            str(self.root / "output"), str(recipe)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(all(item["alignment"] is None for item in report["layers"]))
        self.assertEqual(report["layers"][1]["input_size_px"], [6, 4])
        with Image.open(self.root / "output/composite.png") as png:
            np.testing.assert_allclose(np.asarray(png)[..., :3], 104, atol=1)
        psd = PSDImage.open(self.root / "output/layers.psd")
        self.assertEqual([x.blend_mode for x in psd],
                         [BlendMode.NORMAL, BlendMode.MULTIPLY, BlendMode.OVERLAY])
        self.assertEqual([x.opacity for x in psd], [255, 255, 153])
        np.testing.assert_array_equal(np.asarray(psd[0].topil())[..., :3], 160)
        saved = json.loads((self.root / "output/overlay-validation.json").read_text())
        self.assertEqual(saved, report)

    def test_legacy_white_render_matches_original_math_with_hole_and_alignment(self):
        base_pixels = np.full((80, 100, 3), [150, 180, 210], np.uint8)
        render = np.zeros((80, 100, 4), np.uint8)
        render[8:48, 10:50] = [220, 210, 200, 255]
        render[20:40, 10:30, :3] = [70, 90, 110]
        render[25:30, 35:40, 3] = 0  # Enclosed opening is excluded from overlap.
        target = np.zeros((80, 100, 4), np.uint8)
        target[14:54, 30:70, 3] = 255
        base = self.image("base.png", base_pixels)
        source = self.image("render.png", render)
        mask = self.image("mask.png", target)
        report = self.run_recipe([self.layer(base), self.layer(source, "multiply", 0.6,
            grayscale=True, normalize_white=True, align=True)], base_mask=mask)

        # Frozen original lighting_composite.py pipeline, with this known translation.
        alpha = render[..., 3].astype(np.float32) / 255
        matrix = np.array([[1, 0, 20], [0, 1, 6]], np.float32)
        warped_alpha = cv2.warpAffine(alpha, matrix, (100, 80))
        rgb = cv2.warpAffine(render[..., :3].astype(np.float32) * alpha[..., None],
                             matrix, (100, 80))
        rgb = np.divide(rgb, warped_alpha[..., None], out=np.full_like(rgb, 255),
                        where=warped_alpha[..., None] > 1e-6)
        gray = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        white = np.percentile(gray[warped_alpha > 0.99], 95)
        multiplier = np.rint(np.clip(gray / white, 0, 1) * 255) / 255
        strength = np.rint(warped_alpha * 255)[..., None] / 255 * 0.6
        expected = np.rint(base_pixels.astype(np.float32) / 255 *
            (1 - strength + strength * multiplier[..., None]) * 255)
        with Image.open(self.root / "output/composite.png") as png:
            difference = np.max(np.abs(np.asarray(png)[..., :3].astype(float) - expected))
        self.assertLessEqual(difference, 1)
        self.assertEqual(report["layers"][1]["alignment"]["offset_px"], [20, 6])
        self.assertEqual(report["layers"][1]["alignment"]["overlap"], 1)
        psd = PSDImage.open(self.root / "output/layers.psd")
        self.assertEqual(psd[1].topil().getpixel((56, 32))[3], 0)

    def test_uniform_resize_with_named_source_and_base_masks(self):
        source = self.image("source.png", np.full((20, 20, 3), 100))
        base = self.image("base.png", np.full((50, 50, 3), 200))
        sm = np.zeros((20, 20), np.uint8)
        sm[2:12, 2:12] = 255
        bm = np.zeros((50, 50), np.uint8)
        bm[10:30, 10:30] = 255
        source_mask = self.image("source-mask.png", sm)
        base_mask = self.image("base-mask.png", bm)
        report = self.run_recipe([self.layer(base), self.layer(source, "multiply",
            align=True, mask=source_mask)], base_mask=base_mask)
        self.assertEqual(report["layers"][1]["alignment"]["uniform_scale"], 2)
        self.assertGreaterEqual(report["layers"][1]["alignment"]["overlap"], 0.99)

    def test_blend_modes_and_transparent_base(self):
        # Hand-calculated single-channel values for backdrop .25, source .75.
        expected = {"normal": 191, "multiply": 48, "screen": 207,
                    "overlay": 96, "soft-light": 96}
        for mode, value in expected.items():
            with self.subTest(mode=mode):
                a = np.array([[[0.25, 0.25, 0.25, 1]]], np.float32)
                b = np.array([[[0.75, 0.75, 0.75, 1]]], np.float32)
                actual = self.module.blend(a, b, mode, 1)
                self.assertAlmostEqual(float(actual[0, 0, 0]) * 255, value, delta=1)
        a = np.array([[[1, 0, 0, 0]]], np.float32)
        b = np.array([[[0, 0, 1, 0.5]]], np.float32)
        np.testing.assert_allclose(self.module.blend(a, b, "multiply", 1), b)

    def test_transparent_base_opacity_survives_png_and_psd(self):
        base = self.image("base.png", np.full((3, 3, 4), [40, 80, 120, 128]))
        self.run_recipe([self.layer(base, opacity=0.6)])
        with Image.open(self.root / "output/composite.png") as png:
            self.assertEqual(png.getpixel((1, 1)), (40, 80, 120, 77))
        psd = PSDImage.open(self.root / "output/layers.psd")
        self.assertEqual(psd[0].topil().getpixel((1, 1)), (40, 80, 120, 128))
        self.assertEqual(psd[0].opacity, 153)

    def test_grayscale_preserves_brightness_without_white_normalization(self):
        base = self.image("base.png", np.full((3, 3, 3), [50, 100, 150]))
        self.run_recipe([self.layer(base, grayscale=True)])
        with Image.open(self.root / "output/composite.png") as png:
            self.assertEqual(png.getpixel((1, 1)), (93, 93, 93, 255))

    def test_empty_alignment_mask_refuses(self):
        base = self.image("base.png", np.full((4, 4, 3), 128))
        source = self.image("source.png", np.zeros((4, 4, 4)))
        with self.assertRaisesRegex(ValueError, "visible geometry"):
            self.run_recipe([self.layer(base), self.layer(source, align=True)])
        self.assertFalse((self.root / "output").exists())

    def test_missing_opacity_and_invalid_options_refuse_without_outputs(self):
        base = self.image("base.png", np.full((4, 4, 3), 128))
        invalid = [dict(path=base, mode="normal"), self.layer(base, opacity=-0.1),
                   self.layer(base, opacity=float("nan")), self.layer(base, "unknown")]
        for layer in invalid:
            with self.subTest(layer=layer), self.assertRaises(ValueError):
                self.run_recipe([layer])
            self.assertFalse((self.root / "output").exists())

    def test_size_mismatch_without_alignment_refuses(self):
        base = self.image("base.png", np.zeros((10, 10, 3)))
        source = self.image("source.png", np.zeros((11, 10, 3)))
        with self.assertRaisesRegex(ValueError, "size|dimensions"):
            self.run_recipe([self.layer(base), self.layer(source)])
        self.assertFalse((self.root / "output").exists())

    def test_nonuniform_scale_and_low_overlap_refuse(self):
        base = self.image("base.png", np.zeros((100, 100, 3)))
        render = np.zeros((100, 100, 4), np.uint8)
        render[10:70, 10:70] = 255
        source = self.image("source.png", render)
        for nonuniform in (True, False):
            target = np.zeros_like(render)
            target[10:(50 if nonuniform else 70), 10:70] = 255
            if not nonuniform:
                target[10:40, 10:40] = 0  # Missing corner changes the outer contour.
            mask = self.image("target.png", target)
            with self.subTest(nonuniform=nonuniform), self.assertRaisesRegex(
                    ValueError, "non-uniform|overlap"):
                self.run_recipe([self.layer(base), self.layer(source, align=True)], base_mask=mask)
            self.assertFalse((self.root / "output").exists())

    def test_any_existing_output_is_preserved_and_no_other_outputs_written(self):
        base = self.image("base.png", np.zeros((4, 4, 3)))
        output = self.root / "output"
        output.mkdir()
        for name in ("composite.png", "layers.psd", "overlay-validation.json"):
            path = output / name
            path.write_bytes(b"keep")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "overwrite"):
                self.run_recipe([self.layer(base)])
            self.assertEqual(path.read_bytes(), b"keep")
            self.assertEqual(list(output.iterdir()), [path])
            path.unlink()


if __name__ == "__main__":
    unittest.main()
