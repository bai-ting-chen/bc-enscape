# Enscape materials

[enscape_materials.py](../scripts/enscape_materials.py) lists, reads, creates and edits Enscape
materials. Load the helper as in SKILL.md step 2; materials need no running renderer until the
render.

## Where they live
An Enscape material is a Rhino render material of type `EnscapeRenderMaterial` in the document's
render-content table. A plain Rhino material is not an Enscape material, and the helpers never
convert one silently: create an Enscape material instead.

## Calls
```python
enscape.materials_schema()          # 8 types, every key: type, units, bounds, default, which types
enscape.list_materials()
enscape.read_material("name")
enscape.validate_material("Default", {"Roughness": 0.8})    # checks only, writes nothing
enscape.create_material("Default", "name", {"DiffuseColor": [220, 30, 20], "Roughness": 0.95})
enscape.edit_material("name", {"Roughness": 0.02})          # MaterialType takes an enum name
enscape.assign_material("name", object_ids=[object_id])      # or layer_full_path="A::B"
enscape.refresh_materials()         # then capture_views in a later Rook call
enscape.undo_material_changes()     # only for try-outs; see Keep or undo
```
Every write is validated against the schema first and read back after; an unknown key, a bad value
or a missing texture file fails before anything is written.

- **Types:** `Default`, `Grass`, `Water`, `Foliage`, `Clearcoat`, `Carpet`, `SelfIlluminated`,
  `Glass`. Use `materials_schema()` for the keys of each.
- **Units:** colours are RGB 0–255; roughness, metallic, specular, opacity and most controls are
  0–1; emission is cd/m²; texture rotation is in radians.
- **Assign** with `assign_material` to objects (by id) or a layer (by full path); it records the
  original render material and material source.

## Why refresh
Enscape's live sync keeps showing the old material after a create, assign or edit, and waiting
does not help. `refresh_materials()` rebuilds the Enscape scene so the next capture shows the
change. Call it after the last material change and before `capture_views`.

## Keep or undo
Unlike render settings, material changes the user asked for stay in the document: they are model
content, not render-time state. Changes made only to try something out are undone at the end with
`undo_material_changes()`: it puts this session's assignments back, writes back what was edited,
then deletes what was created. Save the model only when the user asks.

## Limits
- Seen working in renders: all eight types, and albedo, bump, roughness and transparency-mask
  textures with Box mapping, size and rotation. Normal and displacement modes are not yet tested.
- Making a type visible: Grass and Carpet need `height` (with some `variation`); Water needs wave
  height and wind; Foliage needs a transparency mask texture; Glass needs low opacity.
- `SelfIlluminated` in daylight on a white sky looks washed out rather than glowing; judge emission
  at dusk or against a dark background. `Clearcoat` shows little against white surroundings.
- An older (version-4) material keeps its type slot: switching it to `Glass` needs a new material.
