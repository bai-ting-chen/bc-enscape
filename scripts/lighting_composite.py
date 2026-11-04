"""Align the saved Enscape white render and make a two-layer sunlight study."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from psd_tools.constants import BlendMode


def outer_silhouette(mask):
    """Ignore enclosed transparent glass/rattan holes when checking the camera."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(filled, contours, -1, 1, cv2.FILLED)
    return filled.astype(bool)


def align_lighting(render, rhino_alpha):
    """Match orthographic framing using uniform scale and translation only."""
    source = np.asarray(render, dtype=np.float32)
    source_alpha = source[:, :, 3] / 255
    target_alpha = np.asarray(rhino_alpha, dtype=np.float32) / 255
    sx, sy, sw, sh = cv2.boundingRect((source_alpha > 0.5).astype(np.uint8))
    tx, ty, tw, th = cv2.boundingRect((target_alpha > 0.5).astype(np.uint8))
    if min(sw, sh, tw, th) == 0:
        raise ValueError("Render and Rhino mask must contain visible geometry")
    scale_x, scale_y = tw / sw, th / sh
    if abs(scale_x / scale_y - 1) > 0.005:
        raise ValueError("Camera projections do not match; do not distort the render")
    scale = (scale_x + scale_y) / 2
    matrix = np.array([[scale, 0, tx - scale * sx],
                       [0, scale, ty - scale * sy]], dtype=np.float32)
    size = rhino_alpha.size
    alpha = cv2.warpAffine(source_alpha, matrix, size)
    premultiplied = source[:, :, :3] * source_alpha[:, :, None]
    rgb = cv2.warpAffine(premultiplied, matrix, size)
    rgb = np.divide(rgb, alpha[:, :, None], out=np.full_like(rgb, 255),
                    where=alpha[:, :, None] > 1e-6)
    source_mask = outer_silhouette(alpha > 0.5)
    target_mask = outer_silhouette(target_alpha > 0.5)
    overlap = float(np.sum(source_mask & target_mask) /
                    np.sum(source_mask | target_mask))
    if overlap < 0.99:
        raise ValueError(f"Insufficient silhouette overlap: {overlap:.5f}")
    return rgb, np.minimum(alpha, target_alpha), {
        "uniform_scale": scale,
        "source_to_base_matrix": matrix.tolist(),
        "silhouette_intersection_over_union": overlap,
        "alignment_mask": "Filled external silhouettes; original render alpha retained",
    }


def make_overlay(rgb, alpha):
    """Make sunlit white neutral in Multiply, retaining shaded surface values."""
    gray = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    white_level = float(np.percentile(gray[alpha > 0.99], 95))
    if white_level <= 0:
        raise ValueError("Lighting render has no usable white level")
    multiplier = np.clip(gray / white_level, 0, 1)
    rgba = np.empty((*alpha.shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.rint(multiplier[:, :, None] * 255).astype(np.uint8)
    rgba[:, :, 3] = np.rint(alpha * 255).astype(np.uint8)
    return Image.fromarray(rgba), white_level


def write_comparison(base, composite, path, opacity):
    canvas = Image.new("RGB", (1800, 970), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=28)
    for x, image, title in [
        (0, base, "Original Rhino"),
        (900, composite, f"Rhino + Enscape sunlight | {opacity:.0%} Multiply"),
    ]:
        preview = image.copy()
        preview.thumbnail((900, 900), Image.Resampling.LANCZOS)
        canvas.paste(preview, (x + (900 - preview.width) // 2, 70))
        draw.text((x + 30, 22), title, fill="#222222", font=font)
    canvas.save(path)


def blend_lighting(base, overlay, opacity):
    base_rgb = np.asarray(base, dtype=np.float32) / 255
    overlay_rgba = np.asarray(overlay, dtype=np.float32) / 255
    strength = overlay_rgba[:, :, 3:4] * opacity
    blended = base_rgb * (1 - strength + strength * overlay_rgba[:, :, :3])
    return Image.fromarray(np.rint(blended * 255).astype(np.uint8))


def main(folder, opacity):
    if not 0 <= opacity <= 1:
        raise ValueError("Opacity must be between 0 and 1")
    # PSD layer opacity uses 8 bits; use the same strength in the PNG.
    opacity = round(opacity * 255) / 255
    outputs = ["sunlight-overlay.png", "sunlight-composite.png", "sunlight-study.psd",
               "sunlight-comparison.png", "composite-validation.json"]
    if any((folder / name).exists() for name in outputs):
        raise ValueError("Use a fresh output folder; refusing to overwrite outputs")
    base = Image.open(folder / "rhino-base.png").convert("RGB")
    render = Image.open(folder / "enscape-orthographic.png").convert("RGBA")
    rhino_alpha = Image.open(folder / "rhino-mask.png").getchannel("A")
    if not base.size == render.size == rhino_alpha.size:
        raise ValueError("All inputs must have matching pixel dimensions")
    rgb, alpha, alignment = align_lighting(render, rhino_alpha)
    overlay, white_level = make_overlay(rgb, alpha)
    overlay.save(folder / "sunlight-overlay.png")
    composite = blend_lighting(base, overlay, opacity)
    composite.save(folder / "sunlight-composite.png")

    psd = PSDImage.new("RGB", base.size, color=(255, 255, 255))
    PixelLayer.frompil(base, psd, name="Rhino screenshot")
    lighting = PixelLayer.frompil(overlay, psd, name="Enscape sunlight and shadow")
    lighting.blend_mode = BlendMode.MULTIPLY
    lighting.opacity = round(opacity * 255)
    psd_path = folder / "sunlight-study.psd"
    psd.save(psd_path)
    readback = PSDImage.open(psd_path)
    if len(readback) != 2 or readback[1].blend_mode != BlendMode.MULTIPLY:
        raise ValueError("Layered Photoshop file failed readback")
    if readback[0].topil().convert("RGB").tobytes() != base.tobytes():
        raise ValueError("Photoshop base layer differs from the original")
    write_comparison(base, composite, folder / "sunlight-comparison.png", opacity)
    report = {
        "pixels": list(base.size), "blend_mode": "Multiply", "opacity": opacity,
        "layers": [layer.name for layer in readback],
        "base_pixels_unchanged_in_psd": True,
        "grayscale_white_level": white_level, "alignment": alignment,
        "method": "Normalized Enscape white-mode render; approximate lighting layer",
    }
    (folder / "composite-validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--opacity", type=float, default=0.60)
    args = parser.parse_args()
    main(args.folder, args.opacity)
