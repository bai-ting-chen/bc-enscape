"""Apply and restore the render sheet with request overrides."""
import os
import scriptcontext as sc
from enscape_bridge import PRESET_KEY
from enscape_general import GENERAL_KEY, configure_general, restore_general
from enscape_presets import configure_preset, restore_preset
from enscape_profile import _profile_backup_path
from enscape_settings import settings_schema, validate_settings
from enscape_sheet import SHEET_OMITTED_VISUAL_KEYS, _read_defaults


def _check_sheet_complete(values):
    schema = settings_schema()["categories"]
    expected = {entry["key"] for category, entries in schema.items() if category != "general"
                for entry in entries} - SHEET_OMITTED_VISUAL_KEYS
    missing = expected - values.keys()
    if missing:
        raise ValueError("Render sheet is missing visual keys: " + ", ".join(sorted(missing)))


def _split_settings(settings):
    schema = settings_schema()["categories"]
    general_keys = {entry["key"] for entry in schema["general"]}
    known_keys = {entry["key"] for entries in schema.values() for entry in entries}
    for key in settings:
        if key not in known_keys:
            raise ValueError("Unknown sheet or override key: " + str(key))
    visual = {key: value for key, value in settings.items() if key not in general_keys}
    general = {key: value for key, value in settings.items() if key in general_keys}
    # Validate both groups before either write path can run.
    if visual:
        validate_settings(visual)
    if general:
        validate_settings(general, general=True)
    return visual, general


def apply_defaults(overrides):
    """Apply the user's sheet plus explicit request values, retaining backups."""
    if not isinstance(overrides, dict):
        raise ValueError("Overrides must be a dict")
    if PRESET_KEY in sc.sticky or GENERAL_KEY in sc.sticky or os.path.exists(_profile_backup_path()):
        raise ValueError("Restore existing temporary settings before applying defaults")
    values = _read_defaults()
    _check_sheet_complete(values)
    values.update(overrides)
    visual, general = _split_settings(values)
    try:
        # The preset records the modified profile as its own restoration baseline.
        if general:
            configure_general(general)
        if visual:
            configure_preset(visual)
    except Exception:
        restore_settings()
        raise
    return values


def lighting_overrides(width_px, height_px):
    """Return overlay settings to merge with the sheet via apply_defaults."""
    return {"RenderingStyle": "WHITE", "ProjectionMode": "ORTHOGRAPHIC",
            "ScreenshotResolution": "CUSTOM", "ScreenshotResolutionX": width_px,
            "ScreenshotResolutionY": height_px, "ScreenshotViewportAspectRatio": False,
            "ApplyAlphaChannelDuringImageExport": True, "ExportBackgroundMask": False,
            "WhiteBackground": True}


def restore_settings():
    """Attempt both restoration paths, then report every failure together."""
    result = {}
    failures = []
    for name, key, restore in (("preset", PRESET_KEY, restore_preset),
                               ("general", GENERAL_KEY, restore_general)):
        try:
            if key in sc.sticky or (name == "general" and os.path.exists(_profile_backup_path())):
                result[name] = restore()
        except Exception as error:
            failures.append(name + ": " + str(error))
    if failures:
        raise RuntimeError("Settings restoration failed: " + "; ".join(failures))
    return result
