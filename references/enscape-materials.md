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
enscape.refresh_materials()         # then capture_views in a later Rook call
```
Every write is validated against the schema first and read back after; an unknown key, a bad value
or a missing texture file fails before anything is written.

- **Types:** `Default`, `Grass`, `Water`, `Foliage`, `Clearcoat`, `Carpet`, `SelfIlluminated`,
  `Glass`. Use `materials_schema()` for the keys of each.
- **Units:** colours are RGB 0–255; roughness, metallic, specular, opacity and most controls are
  0–1; emission is cd/m²; texture rotation is in radians.
- **Assign** with Rhino, not the helper: an object's `RenderMaterial` with its material source set
  to the object, or a layer's `RenderMaterial`, then `CommitChanges()`.

## Why refresh
Enscape's live sync keeps showing the old material after a create, assign or edit, and waiting
does not help. `refresh_materials()` rebuilds the Enscape scene so the next capture shows the
change. Call it after the last material change and before `capture_views`.

## Keep or undo
Unlike render settings, material changes the user asked for stay in the document: they are model
content, not render-time state. Changes made only to try something out are undone at the end:
read the material first with `read_material`, delete what was created, write back what was
edited. Save the model only when the user asks.

## Limits
- Tested visibly: creating a `Default` material, assigning it, changing colour and roughness.
  Other types and texture rendering are validated but not yet seen in a render.
- An older (version-4) material keeps its type slot: switching it to `Glass` needs a new material.
