"""Composite ordered image layers: image_overlay.py OUTPUT_FOLDER RECIPE.json.

Recipe paths are relative to the recipe file. Layers are bottom to top and require
path, mode and opacity (0-1). Optional layer keys: grayscale, normalize_white,
align and mask. Optional recipe key base_mask supplies the alignment target.
normalize_white requires grayscale and maps the 95th percentile of opaque pixels
to white, reproducing the former white-render workflow when explicitly enabled.
Masks use RGBA alpha, or luminance for images without an alpha channel.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from psd_tools.constants import BlendMode


MODES = {"normal": BlendMode.NORMAL, "multiply": BlendMode.MULTIPLY,
         "screen": BlendMode.SCREEN, "overlay": BlendMode.OVERLAY,
         "soft-light": BlendMode.SOFT_LIGHT}
OUTPUTS = ("composite.png", "layers.psd", "overlay-validation.json")


def load_rgba(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.float32) / 255


def load_mask(path, size_px):
    with Image.open(path) as image:
        if image.size != size_px:
            raise ValueError(f"Mask dimensions differ from its image: {path}")
        mask = image.getchannel("A") if "A" in image.getbands() else image.convert("L")
        return np.asarray(mask, dtype=np.float32) / 255


def outer_silhouette(mask):
    """Fill enclosed openings so glazing does not affect the framing check."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(filled, contours, -1, 1, cv2.FILLED)
    return filled.astype(bool)


def align_layer(source, source_mask, target_mask):
    """Fit silhouettes using one uniform scale and translation; never distort."""
    sx, sy, sw, sh = cv2.boundingRect((source_mask > 0.5).astype(np.uint8))
    tx, ty, tw, th = cv2.boundingRect((target_mask > 0.5).astype(np.uint8))
    if min(sw, sh, tw, th) == 0:
        raise ValueError("Alignment masks must contain visible geometry")
    scale_x, scale_y = tw / sw, th / sh
    # A half-percent allowance covers raster bounds, not anisotropic transforms.
    if abs(scale_x / scale_y - 1) > 0.005:
        raise ValueError("Camera projections require non-uniform scale; refusing distortion")
    scale = (scale_x + scale_y) / 2
    # OpenCV transforms pixel centers; align the outer pixel edges when resizing.
    edge_offset = (scale - 1) / 2
    offset = [tx - scale * sx + edge_offset, ty - scale * sy + edge_offset]
    matrix = np.array([[scale, 0, offset[0]], [0, scale, offset[1]]], np.float32)
    size_px = (target_mask.shape[1], target_mask.shape[0])
    warped_mask = cv2.warpAffine(source_mask, matrix, size_px)
    source_outer = outer_silhouette(warped_mask > 0.5)
    target_outer = outer_silhouette(target_mask > 0.5)
    overlap = float(np.sum(source_outer & target_outer) / np.sum(source_outer | target_outer))
    if overlap < 0.99:
        raise ValueError(f"Insufficient silhouette overlap: {overlap:.5f}; minimum is 0.99")
    alpha = np.minimum(source[..., 3], source_mask)
    warped_alpha = cv2.warpAffine(alpha, matrix, size_px)
    rgb = cv2.warpAffine(source[..., :3] * alpha[..., None], matrix, size_px)
    rgb = np.divide(rgb, warped_alpha[..., None], out=np.ones_like(rgb),
                    where=warped_alpha[..., None] > 1e-6)
    rgba = np.dstack((rgb, np.minimum(warped_alpha, target_mask)))
    return rgba, {"uniform_scale": scale, "offset_px": offset,
                  "source_to_base_matrix": matrix.tolist(), "overlap": overlap,
                  "silhouette_check": "Filled outer silhouettes; enclosed openings excluded"}


def blend(backdrop, source, mode, opacity):
    """Separable RGB blending followed by straight-alpha source-over composition."""
    b, s = backdrop[..., :3], source[..., :3]
    if mode == "normal":
        mixed = s
    elif mode == "multiply":
        mixed = b * s
    elif mode == "screen":
        mixed = b + s - b * s
    elif mode == "overlay":
        mixed = np.where(b <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    elif mode == "soft-light":
        d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
        mixed = np.where(s <= 0.5, b - (1 - 2 * s) * b * (1 - b),
                         b + (2 * s - 1) * (d - b))
    else:
        raise ValueError(f"Unsupported blend mode: {mode}")
    ba, sa = backdrop[..., 3:4], source[..., 3:4] * opacity
    alpha = sa + ba * (1 - sa)
    rgb = (1 - sa) * ba * b + (1 - ba) * sa * s + ba * sa * mixed
    rgb = np.divide(rgb, alpha, out=np.zeros_like(rgb), where=alpha > 0)
    return np.dstack((np.clip(rgb, 0, 1), alpha))


def to_image(rgba):
    return Image.fromarray(np.rint(np.clip(rgba, 0, 1) * 255).astype(np.uint8))


def read_recipe(recipe_path):
    recipe = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
    if not isinstance(recipe, dict) or set(recipe) - {"layers", "base_mask"}:
        raise ValueError("Recipe must contain layers and optionally base_mask")
    layers = recipe.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("Recipe needs a nonempty ordered layers list")
    allowed = {"path", "mode", "opacity", "grayscale", "normalize_white", "align", "mask"}
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict) or not {"path", "mode", "opacity"} <= layer.keys():
            raise ValueError(f"Layer {index} requires path, mode and opacity")
        if set(layer) - allowed:
            raise ValueError(f"Unknown options in layer {index}: {set(layer) - allowed}")
        if layer["mode"] not in MODES:
            raise ValueError(f"Unsupported blend mode in layer {index}: {layer['mode']}")
        opacity = layer["opacity"]
        if (isinstance(opacity, bool) or not isinstance(opacity, (int, float))
                or not np.isfinite(opacity) or not 0 <= opacity <= 1):
            raise ValueError(f"Layer {index} opacity must be between 0 and 1")
        for option in ("grayscale", "normalize_white", "align"):
            if option in layer and not isinstance(layer[option], bool):
                raise ValueError(f"Layer {index} {option} must be a boolean")
        if layer.get("normalize_white") and not layer.get("grayscale"):
            raise ValueError("normalize_white requires grayscale")
        if index == 0 and layer.get("align"):
            raise ValueError("The base layer cannot be aligned to itself")
        if layer.get("mask") and not layer.get("align"):
            raise ValueError("A layer mask requires align: true")
    return recipe


def prepare_layers(recipe, root):
    """Validate and prepare every image before creating any outputs."""
    prepared, records = [], []
    base_size = None
    target_mask = None
    for index, layer in enumerate(recipe["layers"]):
        path = (root / layer["path"]).resolve()
        rgba = load_rgba(path)
        size_px = (rgba.shape[1], rgba.shape[0])
        opacity_8bit = round(layer["opacity"] * 255)
        record = {"path": str(path), "input_size_px": list(size_px),
                  "mode": layer["mode"], "opacity": layer["opacity"],
                  "opacity_8bit": opacity_8bit, "effective_opacity": opacity_8bit / 255,
                  "grayscale": layer.get("grayscale", False),
                  "normalize_white": layer.get("normalize_white", False),
                  "alignment": None}
        if index == 0:
            base_size = size_px
            target_mask = rgba[..., 3]
            if recipe.get("base_mask"):
                mask_path = (root / recipe["base_mask"]).resolve()
                target_mask = load_mask(mask_path, base_size)
                record["base_mask"] = str(mask_path)
        if layer.get("align"):
            source_mask = rgba[..., 3]
            if layer.get("mask"):
                mask_path = (root / layer["mask"]).resolve()
                source_mask = load_mask(mask_path, size_px)
                record["mask"] = str(mask_path)
            rgba, record["alignment"] = align_layer(rgba, source_mask, target_mask)
        elif size_px != base_size:
            raise ValueError(f"Layer {index} size differs from base; request alignment explicitly")
        if layer.get("grayscale"):
            gray = rgba[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            if layer.get("normalize_white"):
                opaque = gray[rgba[..., 3] > 0.99]
                white = float(np.percentile(opaque, 95)) if opaque.size else 0
                if white <= 0:
                    raise ValueError("Grayscale normalization has no usable opaque white level")
                gray = np.clip(gray / white, 0, 1)
                record["grayscale_white_level"] = white * 255
            rgba[..., :3] = gray[..., None]
        # PNG and PSD use identical 8-bit layer pixels and opacity.
        image = to_image(rgba)
        prepared.append(image)
        records.append(record)
    return prepared, records


def write_psd(stream, images, records):
    # RGBA stores alpha as layer transparency rather than a separate pixel mask.
    psd = PSDImage.new("RGBA", images[0].size)
    for image, record in zip(images, records):
        layer = PixelLayer.frompil(image, psd, name=Path(record["path"]).stem)
        layer.blend_mode = MODES[record["mode"]]
        layer.opacity = record["opacity_8bit"]
    psd.save(stream)


def validate_psd(path, images, records):
    psd = PSDImage.open(path)
    if len(psd) != len(images):
        raise ValueError("PSD readback layer count differs")
    for layer, image, record in zip(psd, images, records):
        if (layer.blend_mode != MODES[record["mode"]]
                or layer.opacity != record["opacity_8bit"]
                or layer.topil().convert("RGBA").tobytes() != image.tobytes()):
            raise ValueError("PSD readback layer pixels, mode or opacity differ")


def main(output_folder, recipe_path):
    output_folder, recipe_path = Path(output_folder), Path(recipe_path).resolve()
    if any((output_folder / name).exists() or (output_folder / name).is_symlink()
           for name in OUTPUTS):
        raise ValueError("Refusing to overwrite existing outputs")
    recipe = read_recipe(recipe_path)
    images, records = prepare_layers(recipe, recipe_path.parent)
    composite = np.zeros((*np.asarray(images[0]).shape[:2], 4), np.float32)
    for image, record in zip(images, records):
        composite = blend(composite, np.asarray(image, dtype=np.float32) / 255,
                          record["mode"], record["effective_opacity"])
    report = {"recipe": str(recipe_path), "size_px": list(images[0].size),
              "layers": records, "psd_readback_verified": True,
              "outputs": {name: str((output_folder / name).resolve()) for name in OUTPUTS}}
    output_folder.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name in OUTPUTS:
            path = output_folder / name
            # Exclusive creation also prevents overwriting files appearing mid-run.
            with path.open("xb") as stream:
                created.append(path)
                if name == "composite.png":
                    to_image(composite).save(stream, format="PNG")
                elif name == "layers.psd":
                    write_psd(stream, images, records)
                else:
                    stream.write(json.dumps(report, indent=2).encode("utf-8"))
            if name == "layers.psd":
                validate_psd(path, images, records)
    except Exception:
        for path in reversed(created):
            path.unlink()
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_folder", type=Path)
    parser.add_argument("recipe", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(main(args.output_folder, args.recipe)))
    except (ValueError, OSError) as error:
        parser.exit(2, f"image_overlay: {error}\n")
