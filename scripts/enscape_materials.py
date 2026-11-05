"""Discover and edit Enscape Rhino render content; assignments belong to Rhino.

Run inside Rhino through Rook. Public numeric values use Enscape storage units:
fractions, RGB bytes, radians for texture rotation, and cd/m2 for emission.
Schema defaults come from an unattached instance of the installed material type.
"""
import math
import os


_TYPE_NAME = "Enscape.Rhino.Plugin.Materials.EnscapeRenderMaterial"
_TYPE_ID = "a040e9d1-853f-435f-bfb8-5cc4fd88c617"


def _installed_type(name):
    from enscape_bridge import find_type
    return find_type(name)


def _material_version():
    from enscape_version import check_version
    return check_version()


def _enum_type_name(key):
    if key == "BumpMapType":
        return "Enscape.Rhino.Plugin.Materials.BumpMapKind"
    if key.endswith("TextureWorldCoordinateSystemType"):
        return "Enscape.Cad.Materials.WorldCoordinateSystemType"
    return None


def _runtime():
    import Rhino
    import System
    import scriptcontext as sc
    from enscape_bridge import find_type
    flags = (System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
             | System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Instance)
    material_type = find_type(_TYPE_NAME)
    definitions = material_type.GetField("FieldDefinitions", flags).GetValue(None)
    return Rhino, System, sc.doc, material_type, definitions


def _plain(value, kind):
    if kind == "Color":
        return [int(value.R), int(value.G), int(value.B)]
    if kind == "Vector2d":
        return [float(value.X), float(value.Y)]
    if kind == "String":
        return "" if value is None else str(value)
    if kind == "Double":
        return float(value)
    if kind == "Boolean":
        return bool(value)
    return str(value)


def _parameter_types(key, types):
    # These are the branches of EnscapeRenderMaterialParser.ParseMaterialTypes.
    if key.startswith("Water"):
        return ["Water"]
    if key.startswith("Grass"):
        return ["Grass", "Carpet"]
    if key.startswith("Emissive"):
        return ["SelfIlluminated"]
    if key == "Metallic":
        return ["Default", "Clearcoat"]
    if key in ("Opacity", "IndexOfRefraction", "IsSolidGlass"):
        return ["Default", "Glass"]
    if key.startswith("TransparencyTexture"):
        return ["Default", "Foliage", "SelfIlluminated", "Glass"]
    return [name for name in types if name != "Water"]


def _range(key, kind):
    # Bounds from Enscape.Views material editors' compiled Avalonia controls.
    # Percentage controls display 100 times the stored fraction. Unknown bounds
    # stay explicit, matching settings_schema rather than inventing limits.
    if kind == "Color":
        return 0, 255, "RGB bytes (sRGB), three integers"
    if key == "BumpAmount":
        return -10, 10, "Enscape bump amount"
    if key == "NormalMapIntensity":
        return 0, 2, "factor (UI percent / 100)"
    if key == "EmissiveStrength":
        return 0, 100000, "cd/m2; 0 disables emission, UI active range starts at 1"
    if key == "IndexOfRefraction":
        return 1, 2.5, "refractive index"
    if key.endswith("TextureRotation"):
        return None, None, "radians"
    if kind == "Vector2d":
        return 0, None, "positive texture width and height factors (Rhino TextureTransformationUnit.Factor)"
    if key in ("WaterWindDirectionX", "WaterWindDirectionY"):
        return None, None, "wind vector component; length is intensity, angle is radians"
    if (key in ("ImageFade", "Opacity", "Roughness", "Metallic", "Specular",
                "WaterWaveHeight", "WaterWaveScale", "WaterCausticsIntensity",
                "GrassHeight", "GrassHeightVariation") or key.endswith("TextureBrightness")):
        return 0, 1, "factor (UI percent / 100)"
    return None, None, "unknown" if kind == "Double" else "none"


def materials_schema():
    """Return installed types and field keys, defaults, bounds and units.

    types maps each enum name to its applicable writable keys. parameters maps
    each key to metadata. Default is Enscape's generic material. All eight types
    share Rhino storage; inactive fields are excluded from write requests.
    """
    version = _material_version()
    if not version["match"]:
        raise RuntimeError("Material schema requires the tested Enscape version")
    _, system, _, material_type, definitions = _runtime()
    names = list(system.Enum.GetNames(_installed_type("Enscape.Cad.Materials.MaterialType")))
    parameters = {}
    template = system.Activator.CreateInstance(material_type)
    try:
        for item in definitions:
            key, definition = str(item.Key), item.Value
            if key in ("MaterialType", "MaterialTypeV5"):
                continue
            kind = definition.GetType().GetGenericArguments()[0].Name
            default = definition.GetValue(template)
            enum = []
            if _enum_type_name(key):
                enum_type = _installed_type(_enum_type_name(key))
                enum = list(system.Enum.GetNames(enum_type))
                default = str(system.Enum.ToObject(enum_type, system.Int32(default)))
                kind = enum_type.Name
            elif kind not in ("Color", "Vector2d", "Double", "String", "Boolean"):
                raise RuntimeError("Undocumented material field type: " + key + " " + kind)
            else:
                default = _plain(default, kind)
            lower, upper, unit = _range(key, kind)
            parameters[key] = dict(type=kind, default=default, enum=enum,
                                   range=dict(min=lower, max=upper), unit=unit,
                                   storage_key=str(definition.InternalName))
    finally:
        template.Dispose()
    return dict(version=version["installed"], render_content_type_id=_TYPE_ID,
                parameters=parameters, types={name: [key for key in parameters
                    if name in _parameter_types(key, names)] for name in names},
                type_parameter="MaterialType (enum name, accepted by edit_material)")


def validate_material(material_type, parameters):
    """Validate the whole request before writing; return normalized values.

    Texture/video paths must be absolute existing files. Empty string clears a
    path. Colours are three RGB integers in 0..255. Numeric enum IDs are refused.
    None bounds mean unknown; numeric values must always be finite.
    """
    schema = materials_schema()
    if not isinstance(material_type, str) or material_type not in schema["types"]:
        raise ValueError("Unknown material type; use an enum name from materials_schema")
    if not isinstance(parameters, dict):
        raise ValueError("Material parameters must be a dict")
    result = {}
    for key, value in parameters.items():
        if key not in schema["types"][material_type]:
            raise ValueError("Unknown or inactive parameter for " + material_type + ": " + str(key))
        entry = schema["parameters"][key]
        kind = entry["type"]
        if entry["enum"]:
            if not isinstance(value, str) or value not in entry["enum"]:
                raise ValueError(key + " requires an enum name: " + ", ".join(entry["enum"]))
        elif kind == "Boolean":
            if type(value) is not bool:
                raise ValueError(key + " requires bool")
        elif kind == "String":
            if not isinstance(value, str):
                raise ValueError(key + " requires string")
            if value and (not os.path.isabs(value) or not os.path.isfile(value)):
                raise ValueError(key + " requires an absolute existing file, or empty string to clear")
        else:
            sequence = kind in ("Color", "Vector2d")
            if sequence and (not isinstance(value, (list, tuple)) or len(value) != (3 if kind == "Color" else 2)):
                raise ValueError(key + " requires " + ("RGB bytes" if kind == "Color" else "two positive sizes"))
            for component in value if sequence else [value]:
                allowed = (int,) if kind == "Color" else (int, float)
                if type(component) not in allowed or not math.isfinite(component):
                    raise ValueError(key + " requires finite " + kind)
                lower, upper = entry["range"]["min"], entry["range"]["max"]
                if ((lower is not None and component < lower) or
                        (upper is not None and component > upper) or
                        (kind == "Vector2d" and component <= 0)):
                    raise ValueError(key + " is outside its known range")
            if sequence:
                value = list(value)
        result[key] = value
    return result


def _find_material(name, required=True):
    _, _, doc, _, _ = _runtime()
    matches = [m for m in doc.RenderMaterials if m.Name == name]
    if len(matches) > 1:
        raise ValueError("Ambiguous material name: " + name)
    if not matches and required:
        raise ValueError("Material not found: " + name)
    return matches[0] if matches else None


def list_materials():
    """List all Rhino render materials with IDs, names and Enscape/native types."""
    _, system, doc, _, definitions = _runtime()
    return [dict(id=str(m.Id), name=m.Name, enscape=str(m.TypeId).lower() == _TYPE_ID,
                 type=_effective_type(m, system, definitions) if str(m.TypeId).lower() == _TYPE_ID else m.TypeName)
            for m in doc.RenderMaterials]


def _effective_type(material, system, definitions):
    # Version 4 stores the authoritative type in the original slot. Its V5
    # getter returns the unused default until Enscape migrates it at render time.
    if material.Version < 4:
        raise ValueError("Material storage predates version 4; migrate explicitly in Enscape first")
    if material.Version == 4:
        number = definitions["MaterialType"].GetValue(material)
        return str(system.Enum.ToObject(_installed_type("Enscape.Cad.Materials.MaterialType"), number))
    return str(material.MaterialType)


def _read_material(material):
    if str(material.TypeId).lower() != _TYPE_ID:
        raise ValueError("Material is not Enscape render content: " + material.Name)
    _, system, _, _, definitions = _runtime()
    from enscape_bridge import find_type
    schema = materials_schema()
    values = {}
    for key, entry in schema["parameters"].items():
        value = definitions[key].GetValue(material)
        if entry["enum"]:
            enum_type = find_type(_enum_type_name(key))
            value = str(system.Enum.ToObject(enum_type, system.Int32(value)))
        else:
            value = _plain(value, entry["type"])
        values[key] = value
    return dict(id=str(material.Id), name=material.Name, type=_effective_type(material, system, definitions),
                storage_version=int(material.Version), parameters=values)


def read_material(name):
    """Read Enscape fields without migrating legacy material versions."""
    return _read_material(_find_material(name))


def _active_export():
    from enscape_bridge import FLAGS, services
    _, _, doc, _, _ = _runtime()
    current = services()["state"].CurrentState
    if not current.IsRunning:
        raise ValueError("Start Enscape before refreshing its material scene")
    session = current.Session
    export = session.GetType().GetProperty("ActiveExportSession", FLAGS).GetValue(session, None)
    serial = export.GetType().GetProperty("DocumentSerialNumber", FLAGS).GetValue(export, None)
    if serial != doc.RuntimeSerialNumber:
        raise ValueError("Enscape is rendering a different Rhino document")
    return export, int(doc.RuntimeSerialNumber)


def refresh_materials():
    """Rebuild the active Enscape scene after script edits, before capture_views.

    Rhino's preview change queue can retain old assignments despite live updates
    being enabled. This explicitly re-exports the scene, without editing geometry.
    Call capture_views in a later Rook call so renderer tasks can progress.
    """
    export, serial = _active_export()
    export.CreateWorld()
    export.Flush()
    return {"scene_rebuilt": True, "document_serial_number": serial}


def _boxed(value, definition, key, system, rhino):
    from enscape_bridge import find_type
    kind = definition.GetType().GetGenericArguments()[0].Name
    if _enum_type_name(key):
        enum_type = find_type(_enum_type_name(key))
        number = system.Convert.ToInt32(system.Enum.Parse(enum_type, value))
        return system.Double(number) if kind == "Double" else system.Int32(number)
    if kind == "Color":
        return system.Drawing.Color.FromArgb(*value)
    if kind == "Vector2d":
        return rhino.Geometry.Vector2d(*value)
    if kind == "Double":
        return system.Double(value)
    if kind == "Boolean":
        return system.Boolean(value)
    return value


def _write_material(material, material_type, name, parameters):
    from enscape_bridge import find_type
    rhino, system, doc, runtime_type, definitions = _runtime()
    create = material is None
    if create:
        material = system.Activator.CreateInstance(runtime_type)
    # Retain raw fields, including both legacy type slots, for exact rollback.
    before = [(item.Value, item.Value.GetValue(material)) for item in definitions]
    undo = doc.BeginUndoRecord("Enscape material " + ("create" if create else "edit"))
    added = False
    try:
        material.BeginChange(rhino.Render.RenderContent.ChangeContexts.Program)
        try:
            if create or _effective_type(material, system, definitions) != material_type:
                material.MaterialType = system.Enum.Parse(find_type("Enscape.Cad.Materials.MaterialType"), material_type)
            if create:
                material.Name = name
            for key, value in parameters.items():
                definition = definitions[key]
                definition.SetValue(material, _boxed(value, definition, key, system, rhino))
        finally:
            material.EndChange()
        if create:
            if not doc.RenderMaterials.Add(material):
                raise RuntimeError("Rhino refused the Enscape material")
            added = True
        actual = _read_material(_find_material(name))
        if actual["type"] != material_type or any(actual["parameters"][k] != v for k, v in parameters.items()):
            raise RuntimeError("Material readback differs from the validated request")
        doc.Views.Redraw()
        return actual
    except Exception:
        if create:
            if added and not doc.RenderMaterials.Remove(material):
                raise RuntimeError("Material write failed and Rhino refused rollback")
        else:
            material.BeginChange(rhino.Render.RenderContent.ChangeContexts.Program)
            try:
                for definition, value in before:
                    definition.SetValue(material, value)
            finally:
                material.EndChange()
            if any(not system.Object.Equals(definition.GetValue(material), value) for definition, value in before):
                raise RuntimeError("Material write failed and rollback readback differs")
        raise
    finally:
        if undo:
            doc.EndUndoRecord(undo)
        if create and not added:
            material.Dispose()


def create_material(material_type, name, parameters):
    """Create unassigned Enscape content and return its verified readback.

    Example: create_material('Default', 'test', {'DiffuseColor': [220, 30, 20],
                                               'Roughness': 0.8})
    """
    if not isinstance(name, str) or not name.strip() or any(c in name for c in "\r\n\x00"):
        raise ValueError("Material name must be a nonempty single line")
    values = validate_material(material_type, parameters)
    if _find_material(name, required=False) is not None:
        raise ValueError("Material already exists: " + name)
    return _write_material(None, material_type, name, values)


def edit_material(name, parameters):
    """Edit a uniquely named Enscape material; MaterialType may change its type.

    Example: edit_material('test', {'DiffuseColor': [20, 70, 220], 'Roughness': 0.05})
    Native Rhino materials require a separate explicit conversion, never a fallback.
    """
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("Pass a nonempty material parameter dict")
    material = _find_material(name)
    current = _read_material(material)
    values = dict(parameters)
    material_type = values.pop("MaterialType", current["type"])
    values = validate_material(material_type, values)
    if current.get("storage_version", 5) == 4 and material_type == "Glass":
        raise ValueError("Glass requires version-5 storage; migrate this legacy material explicitly first")
    return _write_material(material, material_type, name, values)
