# Image overlay

[image_overlay.py](../scripts/image_overlay.py) stacks images into one composite: an Enscape render
on a Rhino screenshot, a render's channels, or any set. It runs on the host, not in Rhino.

## Recipe
A JSON file beside the inputs; paths are relative to it. Layers run bottom to top.
- Each layer: `path`, `mode` (`normal`, `multiply`, `screen`, `overlay`, `soft-light`), `opacity`
  0–1; optional `grayscale`, `normalize_white`, `align`, `mask`.
- Recipe level: optional `base_mask`.

```json
{"base_mask": "rhino-mask.png", "layers": [
  {"path": "rhino-base.png", "mode": "normal", "opacity": 1},
  {"path": "enscape.png", "mode": "multiply", "opacity": 0.6,
   "grayscale": true, "normalize_white": true, "align": true}]}
```

## Run
Host Python with `numpy`, `opencv-python`, `Pillow` and `psd-tools`, into a new output folder:

```powershell
py -3.14 "$HOME\.claude\skills\bc-enscape\scripts\image_overlay.py" "<new-output-folder>" "<recipe.json>"
```

It writes `composite.png`, `layers.psd` (each layer with its mode and opacity) and
`overlay-validation.json`, and never overwrites. Look at the composite before reporting.

## Rules
- **Same grid:** a render and its `_alphaMask`, `_depth`, `_materialId` and `_objectId` PNGs share
  one grid: no `align`.
- **Different sources:** `align` fits a layer to the base by uniform scale and translation only,
  from the silhouettes (`base_mask`, a layer's `mask`, else alpha). It needs 99% overlap of the
  outer silhouettes; enclosed openings such as glazing are left out of the check.
- **Rhino mask:** RGBA, geometry only, annotations hidden, restored afterwards.
- **White-mode shading on Rhino:** render with `enscape.lighting_overrides(width_px, height_px)`
  (orthographic, white, transparent, the screenshot's size), then use `grayscale` and
  `normalize_white` on a Multiply layer. It only darkens; it is not a true shadow pass.
