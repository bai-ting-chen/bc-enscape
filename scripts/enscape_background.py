"""Experimental Enscape bridge hooks, called inside Rhino through Rook.

These are installed plug-in internals, not the public Chaos SDK. Start/capture
are asynchronous: return to Rhino's event loop, then call collect_capture().
"""
import ctypes
import base64
import json
import math
import os
import re
import shutil
import subprocess
import time

import Rhino
import System
import System.Windows.Forms as forms
import scriptcontext as sc

KEY = "bc_enscape_background"
PRESET_KEY = "bc_enscape_temporary_preset"
GENERAL_KEY = "bc_enscape_temporary_general"
VERSION_TEST_KEY = "bc_enscape_version_test"
ACTIVE_PRESET_ID = "d8e4b713-533b-421b-8b11-a961385e4b37"
FLAGS = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance


def _skill_file(name):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)


def _installed_version():
    return str(find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin").Assembly.GetName().Version)


def check_version():
    """Report the installed and documented versions; a mismatch is not an error."""
    with open(_skill_file("SKILL.md"), encoding="utf-8-sig") as stream:
        matches = re.findall(r"^Tested with Enscape (\d+(?:\.\d+){1,3})\.\s*$", stream.read(), re.MULTILINE)
    tested = matches[0] if len(matches) == 1 else None
    installed = _installed_version()
    return {"installed": installed, "tested": tested, "match": installed == tested,
            "pending_profile_restore": os.path.exists(_profile_backup_path())}


def _read_defaults():
    """Read Value cells only; enum words such as None remain strings."""
    values = {}
    in_table = False
    with open(_skill_file("render-defaults.md"), encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.lstrip().startswith("|"):
                in_table = False
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells[:3] == ["Key", "Value", "Allowed"]:
                in_table = True
                continue
            if not in_table or all(set(cell) <= set("-: ") for cell in cells):
                continue
            key = cells[0]
            if len(cells) < 3 or not key or key in values:
                raise ValueError("Invalid or duplicate sheet row: " + key)
            try:
                values[key] = json.loads(cells[1])
            except ValueError:
                values[key] = cells[1]
    if not values:
        raise ValueError("The render sheet contains no setting rows")
    return values


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
    """Restore whichever paths were changed, in reverse application order."""
    result = {}
    if PRESET_KEY in sc.sticky:
        result["preset"] = restore_preset()
    if GENERAL_KEY in sc.sticky or os.path.exists(_profile_backup_path()):
        result["general"] = restore_general()
    return result


def _probe_value(entry):
    """Choose a different value using discovered enums or known bounds only."""
    current = entry["value"]
    if entry["enum"]:
        return next((name for name in entry["enum"] if name != current), None)
    if entry["type"] == "Boolean":
        return not current
    if entry["type"] in ("Single", "Double", "Int32", "UInt32"):
        return next((value for value in entry["range"].values()
                     if value != "unknown" and value != current), None)
    return None


def _version_category(state, category):
    entries = settings_schema()["categories"][category]
    sheet = _read_defaults()
    candidates = sorted(entries, key=lambda entry: (
        entry["key"] not in sheet, entry["type"] != "Boolean", not bool(entry["enum"])))
    for entry in candidates:
        value = _probe_value(entry) if entry["writable"] else None
        if value is not None:
            break
    else:
        raise ValueError("No writable test value discovered for " + category)
    key = entry["key"]
    try:
        configure = configure_general if category == "general" else configure_preset
        configure({key: value})
        actual = next(item["value"] for item in settings_schema()["categories"][category] if item["key"] == key)
        if actual != value:
            raise ValueError(key + " failed resolved readback")
    except Exception as error:
        state["items"][category] = {"passed": False, "key": key, "error": str(error)}
        raise
    finally:
        restore_settings()
    state["items"][category] = {"passed": True, "key": key, "value": value, "restored": True}


def _version_snapshot_matches(state):
    store, document = embedded_presets()
    current = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
    return profile_bytes() == state["profile"] and _preset_records_match(current, state["presets"])


def _preset_records_match(current, before):
    return len(current) == len(before) and all(
        any(key.Equals(old) and value == data for old, data in before) for key, value in current)


def _version_capture(state):
    if state["phase"] == "prepare":
        apply_defaults({})
        prepare_capture(state["view"])
        state["phase"] = "preparing"
    elif state["phase"] == "preparing":
        if preparation_status()["preparation"] == "RanToCompletion":
            request_capture(os.path.join(state["folder"], "version-test.png"))
            state["phase"] = "capturing"
    elif state["phase"] == "capturing":
        capture = collect_capture()
        if capture["capture_task"] == "RanToCompletion":
            state["items"]["capture"] = dict(capture, passed=True)
            restore_settings()
            if not _version_snapshot_matches(state):
                raise ValueError("Version test changed the original profile or embedded presets")
            state["items"]["restoration"] = {"passed": True}
            state["phase"] = "complete"


def version_test(output_folder):
    """Advance one scratch-scene check; poll in later Rook calls until complete.

    Each call returns to Rhino's event loop. Reuse the same new folder throughout;
    a completed summary is retained until a different output folder is supplied.
    """
    if not os.path.isabs(output_folder):
        raise ValueError("Version test needs an absolute new output folder")
    folder = os.path.abspath(output_folder)
    state = sc.sticky.get(VERSION_TEST_KEY)
    if state and state["folder"] != folder and state["phase"] != "complete":
        raise ValueError("A version test is already in progress in " + state["folder"])
    if not state or state["folder"] != folder:
        if "capture_task" in sc.sticky.get(KEY, {}):
            raise ValueError("Collect the pending capture before testing settings")
        running = status()
        if not running["running"] or running["start_task"] != "RanToCompletion":
            raise ValueError("Start the intended renderer and wait for completion before the version test")
        capture_view = sc.sticky.get(KEY, {}).get("capture_view")
        if capture_view is not None:
            view = str(capture_view.Name)
        elif sc.doc.NamedViews.Count == 1:
            view = sc.doc.NamedViews[0].Name
        else:
            raise ValueError("Prepare the intended named view before testing a scene with multiple views")
        if os.path.exists(folder):
            raise ValueError("Version test output folder already exists: " + folder)
        restore_settings()
        state = {"folder": folder, "view": view, "version": check_version(), "items": {}, "phase": "sheet"}
        store, document = embedded_presets()
        state["profile"] = profile_bytes()
        state["presets"] = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
        sc.sticky[VERSION_TEST_KEY] = state
    try:
        if state["phase"] == "sheet":
            values = _read_defaults()
            _split_settings(values)
            state["items"]["sheet"] = {"passed": True, "keys": len(values)}
            state["remaining"] = list(settings_schema()["categories"])
            os.makedirs(folder)
            state["phase"] = "categories"
        elif state["phase"] == "categories":
            category = state["remaining"].pop(0)
            _version_category(state, category)
            if not state["remaining"]:
                state["phase"] = "prepare"
        elif state["phase"] != "complete":
            _version_capture(state)
    except Exception as error:
        state["items"][state["phase"]] = {"passed": False, "error": str(error)}
        # An in-flight screenshot must finish before its preset can be removed.
        task = sc.sticky.get(KEY, {}).get("capture_task")
        if task is None or task.IsCompleted:
            try:
                restore_settings()
                state["items"]["restoration"] = {"passed": _version_snapshot_matches(state)}
            except Exception as restore_error:
                state["items"]["restoration"] = {"passed": False, "error": str(restore_error)}
        else:
            state["items"]["restoration"] = {"passed": False, "error": "Capture pending; backups retained"}
        state["phase"] = "complete"
    return {"version": state["version"], "folder": folder, "phase": state["phase"],
            "complete": state["phase"] == "complete",
            "passed": state["phase"] == "complete" and all(item["passed"] for item in state["items"].values()),
            "items": state["items"]}

# The host uses .NET 9; Rhino's plug-in uses .NET Framework. Reflect in a separate
# PowerShell process, replaying the bound document snapshot through Enscape's own
# resolver rather than reimplementing its fallback or hardcoding factory values.
SETTINGS_READER = r'''
$ErrorActionPreference = "Stop"
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$root = Join-Path ${env:ProgramFiles} "Enscape\RendererHost"
[System.Runtime.Loader.AssemblyLoadContext]::Default.add_Resolving({
    param($context, $name)
    $path = Join-Path $root ($name.Name + ".dll")
    if (Test-Path -LiteralPath $path) { $context.LoadFromAssemblyPath($path) }
})
$assemblies = @(
    [Reflection.Assembly]::LoadFrom((Join-Path $root "Enscape.ViewModels.dll")),
    [Reflection.Assembly]::LoadFrom((Join-Path $root "Enscape.Settings.dll"))
)
function SettingType($name) {
    foreach ($assembly in $assemblies) {
        $type = $assembly.GetType("Enscape.EnscapeSettings." + $name)
        if ($type) { return $type }
    }
    throw "Installed Enscape setting type missing: $name"
}
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Reflection;
public class EnscapeSnapshotProxy : DispatchProxy {
    public object Profile, Metadata;
    public Guid ActiveId;
    public Dictionary<Guid, object> Presets = new Dictionary<Guid, object>();
    public Dictionary<Guid, string> Names = new Dictionary<Guid, string>();
    protected override object Invoke(MethodInfo method, object[] args) {
        switch (method.Name) {
            case "RetrieveUserProfileSettings": return Profile;
            case "get_ActivePresetId": return ActiveId;
            case "get_AllMetadata": return Metadata;
            case "PresetName": return Names[(Guid)args[0]];
            case "TryGetSettingsData":
                object value;
                bool found = Presets.TryGetValue((Guid)args[0], out value);
                args[1] = value;
                return found;
            default: throw new NotSupportedException(method.Name);
        }
    }
}
"@
$serializer = [Activator]::CreateInstance((SettingType "SettingsSerializer"), [object[]]@($null))
$profile = $serializer.Deserialize([string]$request.profile)
$user = [Reflection.DispatchProxy]::Create((SettingType "ISettingsDataProvider"), [EnscapeSnapshotProxy])
$user.Profile = $profile
$embedded = [Reflection.DispatchProxy]::Create((SettingType "IEmbeddedSettingsProvider"), [EnscapeSnapshotProxy])
$embedded.ActiveId = [Guid]$request.active
$metadataType = [AppDomain]::CurrentDomain.GetAssemblies() | ForEach-Object { $_.GetType("Enscape.EmbeddedMetadata") } | Where-Object { $_ } | Select-Object -First 1
$metadata = [Array]::CreateInstance($metadataType, $request.presets.Count)
$index = 0
foreach ($preset in $request.presets) {
    $id = [Guid]$preset.id
    $embedded.Presets.Add($id, $serializer.Deserialize([string]$preset.settings))
    $embedded.Names.Add($id, [string]$preset.name)
    $metadata.SetValue([Activator]::CreateInstance($metadataType, [object[]]@($id, [string]$preset.name)), $index++)
}
$embedded.Metadata = $metadata
$managerType = SettingType "VisualSettingsManager"
$manager = [Runtime.CompilerServices.RuntimeHelpers]::GetUninitializedObject($managerType)
$flags = [Reflection.BindingFlags]"NonPublic,Instance"
$managerType.GetField("_userSettingsProvider", $flags).SetValue($manager, $user)
$managerType.GetField("_embeddedSettingsProvider", $flags).SetValue($manager, $embedded)
$managerType.GetField("_defaultVisualSettings", $flags).SetValue($manager, [Activator]::CreateInstance((SettingType "SettingsData")))
$resolved = $managerType.GetMethod("GetActivePresetSettings", [Reflection.BindingFlags]"Public,NonPublic,Instance").Invoke($manager, @())
$groupsType = SettingType "SettingsGroups"
$result = @{}
foreach ($group in "Visual", "General") {
    $values = if ($group -eq "Visual") { $resolved.AllData } else { $profile.AllData }
    $items = @()
    foreach ($type in $groupsType.GetField($group).GetValue($null)) {
        $setting = [Activator]::CreateInstance($type)
        $defaultValue = $setting.Value
        if ($values.ContainsKey($type)) { $setting = $values[$type] }
        $valueType = $type.GetProperty("Value").PropertyType
        $value = $setting.Value
        $enums = @{}
        if ($valueType.IsEnum) {
            foreach ($name in [Enum]::GetNames($valueType)) { $enums[$name] = [int][Enum]::Parse($valueType, $name) }
            $value = [int]$value
            $defaultValue = [int]$defaultValue
        }
        $items += @{key=$type.Name; type=$valueType.Name; enum=$enums; value=$value; default=$defaultValue; flags=$valueType.IsDefined([FlagsAttribute], $false)}
    }
    $result[$group.ToLowerInvariant()] = $items
}
$result | ConvertTo-Json -Depth 12 -Compress
'''

CATEGORIES = {
    "style": "RenderingStyle ArtisticMode Palette PaperModelFilterType PaperModelFilterCategories PaperModelFilterInstances Jitter ExtendedLines SurfaceDetail ColorGradient BleedingColor PolystyrolTransmission HatchedShadows TransparentGlass",
    "outline": "OutlineThickness ArtisticOutlines",
    "camera": "ProjectionMode FieldOfView DofStrength DofAutoFocus DofFocalPoint",
    "exposure": "AutoExposure ExposureBrightness",
    "quality": "RenderQuality",
    "corrections": "AutoContrast TonemapHighlights TonemapShadows Saturation ColorTemperature",
    "effects": "MotionBlur LensflareIntensity BloomIntensity VignetteIntensity ChromaticAberration",
    "fog": "GodraysDensity FogHeight",
    "illumination": "SunPower StarsMoonPower LightBrightness AmbientAmount ShadowSharpness LightViewAuto LightViewMinimum LightViewMaximum",
    "wind": "WindIntensity WindDirectionAngle",
    "sky source": "HorizonSource SkyboxFile HorizonRotation WhiteBackground SkyboxUseSunSpot SkyboxNormalize SkyboxNormalizeBrightness MoonSize",
    "clouds": "CloudDensity CloudVariety CloudCirrusAmount CloudContrails CloudX CloudY",
    "output": "ScreenshotResolution ScreenshotResolutionX ScreenshotResolutionY ScreenshotViewportAspectRatio ExportBackgroundMask ScreenshotDepthRange ScreenshotAutoFileNameEnabled ScreenshotAutoFolder ScreenshotDefaultExtension ApplyAlphaChannelDuringImageExport RecordBitrateMode RecordFramesPerSecond PanoramaResolution ScreenshotJsonExport ScreenshotEdgeDetection ScreenshotPositionLayerFormat ScreenshotUseSubFolder",
}

# Explicit bounds from Enscape.Views compiled visual-setting controls and the
# CaptureSettingsViewModel resolution validator. Missing bounds stay unknown.
BOUNDS = {
    "FieldOfView": (10, 140, "degrees"), "DofFocalPoint": (0.1, 100, "m"),
    "TonemapHighlights": (-100, 100, "%"), "TonemapShadows": (-100, 100, "%"),
    "Saturation": (0, 200, "%"), "FogHeight": (1, 10000, "m"),
    "ColorTemperature": (1500, 15000, "K"),
    "LightViewMinimum": (0.1, 9999, "lux"), "LightViewMaximum": (100, 120000, "lux"),
    "SunPower": (0, 500, "%"), "StarsMoonPower": (0, 300, "%"),
    "LightBrightness": (0, 200, "%"), "WindDirectionAngle": (0, 360, "degrees"),
    "HorizonRotation": (0, 360, "degrees"),
    "SkyboxNormalizeBrightness": (0.1, 250000, "lux"), "MoonSize": (100, 1000, "%"),
    "CloudContrails": (0, 30, "count"), "CloudX": (0, 10000, "m"),
    "CloudY": (0, 10000, "m"), "ScreenshotDepthRange": (1, 10000, "m"),
    "ScreenshotResolutionX": (500, 8192, "px"), "ScreenshotResolutionY": (500, 8192, "px"),
    "MouseSpeedMultiplier": (0.3, 3, "factor"), "MouseSmoothing": (1, 10, "unknown"),
    "MoveSpeedMultiplier": (0.1, 3, "factor"), "CameraMovementSmoothing": (0, 7, "unknown"),
    "SpectatorHeight": (1, 2.1, "m"), "SpectatorWidth": (0.2, 1, "m"),
}
for _key in "MotionBlur LensflareIntensity BloomIntensity VignetteIntensity ChromaticAberration GodraysDensity AmbientAmount WindIntensity CloudDensity CloudVariety CloudCirrusAmount OutlineThickness Jitter ExtendedLines SurfaceDetail ColorGradient BleedingColor PolystyrolTransmission DofStrength ExposureBrightness ShadowSharpness".split():
    BOUNDS[_key] = (0, 100, "%")


def profile_path():
    return os.path.join(str(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments)),
                        "Enscape", "Settings", "settings.json")


def profile_bytes():
    path = profile_path()
    if not os.path.exists(path):
        return None
    with open(path, "rb") as stream:
        return stream.read()


def _profile_backup_path():
    return os.path.join(os.environ["LOCALAPPDATA"], "bc-enscape", "profile-backup.json")


def _read_profile_backup():
    with open(_profile_backup_path(), "rb") as stream:
        data = json.loads(stream.read().decode("utf-8"))
    if os.path.normcase(os.path.abspath(data["profile_path"])) != os.path.normcase(os.path.abspath(profile_path())):
        raise ValueError("Profile backup belongs to a different profile path; backup retained")
    return {key: base64.b64decode(data[key], validate=True) if data[key] is not None else None
            for key in ("before", "after")}


def _write_profile_backup(before, after):
    """Persist exact recovery bytes before any profile replacement; never overwrite."""
    path = _profile_backup_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {key: base64.b64encode(value).decode("ascii") if value is not None else None
            for key, value in (("before", before), ("after", after))}
    data["profile_path"] = os.path.abspath(profile_path())
    created = False
    try:
        with open(path, "xb") as stream:
            created = True
            stream.write(json.dumps(data).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if _read_profile_backup() != {"before": before, "after": after}:
            raise ValueError("Profile backup failed byte readback")
    except Exception:
        if created:
            os.remove(path)
        raise


def write_profile(data):
    """Replace the profile only after the complete new contents reach disk."""
    path = profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staging = os.path.join(os.path.dirname(path), "bc-enscape-settings-" + str(System.Guid.NewGuid()) + ".tmp")
    try:
        with open(staging, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.remove(staging)


def resolved_settings():
    store, document = embedded_presets()
    presets, active_ids = {}, set()
    for item in store.GetAllStoredData(document):
        payload = json.loads(item.Data)
        identifier = str(item.Identifier.Id)
        if identifier == ACTIVE_PRESET_ID:
            active_ids.add(payload["Settings"]["Settings"]["ActivePresetSetting"]["Value"])
        else:
            entry = {"id": identifier, "name": payload["Name"],
                     "settings": json.dumps(payload["Settings"])}
            if identifier in presets and presets[identifier] != entry:
                raise ValueError("Scoped presets differ; cannot resolve the renderer account scope")
            presets[identifier] = entry
    if len(active_ids) > 1:
        raise ValueError("Scoped active presets differ; cannot resolve the renderer account scope")
    profile = profile_bytes()
    request = {"active": next(iter(active_ids), str(System.Guid.Empty)),
               "presets": list(presets.values()),
               "profile": profile.decode("utf-8-sig") if profile is not None else json.dumps({"Version": 3, "Settings": {}})}
    encoded = base64.b64encode(SETTINGS_READER.encode("utf-16-le")).decode("ascii")
    process = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                             input=json.dumps(request), text=True, capture_output=True,
                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError("Enscape setting resolution failed: " + process.stderr)
    return json.loads(process.stdout)


def settings_schema():
    """Return installed visual/general keys, resolved stored values and known bounds.

    Resolution replays VisualSettingsManager.GetActivePresetSettings on the bound
    snapshot. It cannot inspect unsaved in-memory edits in the renderer process.
    General settings are profile values, loaded when RendererHost initializes.
    """
    resolved = resolved_settings()
    categories = {key: [] for key in CATEGORIES}
    categories["general"] = []
    for group, entries in resolved.items():
        for entry in entries:
            key = entry["key"]
            category = "general" if group == "general" else next(
                (name for name, keys in CATEGORIES.items() if key in keys.split()), None)
            if category is None:
                raise ValueError("Unclassified Enscape setting discovered: " + key)
            lower, upper, unit = BOUNDS.get(key, (None, None, None))
            entry.update({"unit": unit or "unknown", "range": {
                "min": lower if lower is not None else "unknown",
                "max": upper if upper is not None else "unknown"},
                "writable": key != "ScreenshotDefaultExtension",
                "nullable": entry["type"] == "String" and entry["default"] is None,
                "source": profile_path() if group == "general" else "VisualSettingsManager.GetActivePresetSettings (document snapshot + profile fallback)"})
            if entry["enum"]:
                for field in ("value", "default"):
                    number = entry[field]
                    entry[field] = next((name for name, value in entry["enum"].items()
                                          if value == number), number)
                    if entry["flags"] and not isinstance(entry[field], str):
                        entry[field] = ", ".join(name for name, bit in entry["enum"].items()
                                                 if bit and number & bit == bit)
            if key == "ScreenshotDefaultExtension":
                entry["note"] = "Read-only: the Rhino capture route forces PNG"
            if group == "general":
                entry["note"] = "User-wide profile value; existing renderer hosts require a restart to load changes"
            categories[category].append(entry)
    return {"version": check_version(), "categories": categories, "external": {
        "sun": "Rhino document sun; prepare_capture uses RhinoSunParser.ParseSun",
        "camera": "Rhino named view; prepare_capture sends its camera"}}


def validate_settings(settings, general=False):
    if not isinstance(settings, dict) or not settings:
        raise ValueError("Pass a nonempty settings dict")
    schema = settings_schema()["categories"]
    entries = {entry["key"]: entry for group, values in schema.items()
               if (group == "general") == general for entry in values}
    result = {}
    for key, value in settings.items():
        if key not in entries or not entries[key]["writable"]:
            raise ValueError("Unknown or read-only setting: " + str(key))
        entry = entries[key]
        if entry["enum"]:
            names = [part.strip() for part in value.split(",")] if isinstance(value, str) else []
            if not names or (len(names) > 1 and not entry.get("flags")) or any(name not in entry["enum"] for name in names):
                raise ValueError(key + " requires an enum name: " + ", ".join(entry["enum"]))
            value = 0
            for name in names:
                value |= entry["enum"][name]
        elif entry["type"] == "Boolean":
            if type(value) is not bool:
                raise ValueError(key + " requires bool")
        elif entry["type"] == "String":
            if not isinstance(value, str) and not (value is None and entry.get("nullable")):
                raise ValueError(key + " requires string")
        else:
            integer = entry["type"] in ("Int32", "UInt32")
            if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value):
                raise ValueError(key + " requires a finite " + entry["type"])
            if entry["type"] == "UInt32" and not 0 <= value <= 4294967295:
                raise ValueError(key + " exceeds UInt32 storage range")
            if entry["type"] == "Int32" and not -2147483648 <= value <= 2147483647:
                raise ValueError(key + " exceeds Int32 storage range")
            if entry["type"] == "Single" and abs(value) > 3.4028234663852886e38:
                raise ValueError(key + " exceeds Single storage range")
            for bound, test in (("min", lambda x: value < x), ("max", lambda x: value > x)):
                limit = entry["range"][bound]
                if limit != "unknown" and test(limit):
                    raise ValueError(key + " is outside its known range")
        result[key] = value
    return result


def find_type(name):
    return next(assembly.GetType(name)
                for assembly in System.AppDomain.CurrentDomain.GetAssemblies()
                if assembly.GetType(name) is not None)


def service_provider():
    plugin_type = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin")
    plugin_id = plugin_type.GetProperty("PluginGuidIdentifier").GetValue(None)
    plugin = Rhino.PlugIns.PlugIn.Find(plugin_id)
    return plugin_type.GetField("_serviceProvider", FLAGS).GetValue(plugin)


def services():
    provider = service_provider()
    return {
        "sync": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.RhinoRenderingSync")),
        "state": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.CurrentRenderingStateProvider")),
        "screenshots": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RhinoStoredRenderings")),
    }


def typed_array(type_name, values):
    array = System.Array.CreateInstance(find_type(type_name), len(values))
    for index, value in enumerate(values):
        array.SetValue(value, index)
    return array


def embedded_presets():
    provider = service_provider()
    embedded = provider.GetService(find_type("Enscape.Rhino.Plugin.EmbeddedData.RhinoEmbeddedData"))
    sync = services()["sync"]
    document = sync.GetType().GetField("_documents", FLAGS).GetValue(sync).ActiveDocument
    return embedded.VisualSettingPresets, document


def set_preset_data(store, document, records):
    operations = []
    for identifier, payload in records:
        data = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DataObject"),
                                              System.Array[System.Object]([identifier, payload]))
        operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.SetEmbeddedDataOperation"))
        operation.DataObject = data
        operations.append(operation)
    store.ExecuteOperations(document, typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))


def configure_preset(settings):
    """Overlay validated keys on the resolved preset and retain exact backups."""
    if PRESET_KEY in sc.sticky:
        raise ValueError("A temporary preset is already active; restore it first")
    if KEY in sc.sticky and "capture_task" in sc.sticky[KEY]:
        raise ValueError("Collect the pending capture before changing settings")
    changes = validate_settings(settings)
    resolved = resolved_settings()["visual"]
    options = {entry["key"]: {"Value": entry["value"]} for entry in resolved}
    options.update({key: {"Value": value} for key, value in changes.items()})
    store, document = embedded_presets()
    before = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
    preset_id = System.Guid.NewGuid()
    identifier_type = find_type("Enscape.Cad.EmbeddedData.DataObjectIdentifier")

    def identifier(value):
        return System.Activator.CreateInstance(identifier_type, System.Array[System.Object]([System.Guid(str(value)), ""]))

    payload = json.dumps({"Name": "background-temporary", "Settings": {"Version": 3, "Settings": options}})
    active = json.dumps({"Name": "ActivePresetId", "Settings": {"Version": 3,
                        "Settings": {"ActivePresetSetting": {"Value": str(preset_id)}}}})
    active_keys = [key for key, _ in before if str(key.Id) == ACTIVE_PRESET_ID]
    if not active_keys:
        active_keys = [identifier(ACTIVE_PRESET_ID)]
    records = [(identifier(preset_id), payload)] + [(key, active) for key in active_keys]
    sc.sticky[PRESET_KEY] = {"document": document, "store": store,
                             "before": before, "profile_before": profile_bytes(),
                             "written_keys": [key for key, _ in records]}
    try:
        set_preset_data(store, document, records)
        current = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
        if any(not any(key.Equals(old) and value == data for old, data in current)
               for key, value in records):
            raise ValueError("Preset write failed readback")
        if profile_bytes() != sc.sticky[PRESET_KEY]["profile_before"]:
            raise ValueError("User profile changed during preset configuration")
    except Exception:
        restore_preset()
        raise
    return {"changed": settings, "preset_id": str(preset_id), "stored_readback": True}


def configure_general(settings):
    """Temporarily edit profile settings, preserving the file's original bytes.

    Enscape loads these values at host initialization. This edits the profile,
    not live hosts; restarting another document's host is never implicit.
    """
    if GENERAL_KEY in sc.sticky or os.path.exists(_profile_backup_path()):
        raise ValueError("Temporary general settings already exist; restore them first")
    if PRESET_KEY in sc.sticky:
        raise ValueError("Restore the temporary visual preset before editing the profile")
    changes = validate_settings(settings, general=True)
    before = profile_bytes()
    profile = json.loads(before.decode("utf-8-sig")) if before is not None else {"Version": 3, "Settings": {}}
    for key, value in changes.items():
        profile["Settings"][key] = {"Value": value}
    after = json.dumps(profile, indent=2).encode("utf-8")
    _write_profile_backup(before, after)
    sc.sticky[GENERAL_KEY] = {"before": before, "after": after}
    try:
        write_profile(after)
        if profile_bytes() != after:
            raise ValueError("General settings write failed readback")
    except Exception:
        restore_general()
        raise
    return {"changed": settings, "stored_readback": True,
            "application": "Restart the intended renderer host to load these profile values"}


def restore_general():
    backup = _profile_backup_path()
    state = _read_profile_backup() if os.path.exists(backup) else sc.sticky[GENERAL_KEY]
    if GENERAL_KEY in sc.sticky and sc.sticky[GENERAL_KEY] != state:
        raise ValueError("Profile backups disagree; backups retained")
    current = profile_bytes()
    if current not in (state["before"], state["after"]):
        raise ValueError("Profile changed externally; backup retained, refusing to replace it")
    if state["before"] is None:
        if current is not None:
            os.remove(profile_path())
    else:
        write_profile(state["before"])
    if profile_bytes() != state["before"]:
        raise ValueError("General restoration failed byte readback")
    if os.path.exists(backup):
        os.remove(backup)
    sc.sticky.pop(GENERAL_KEY, None)
    return {"general_restored": True}


def restore_preset():
    state = sc.sticky[PRESET_KEY]
    before = state["before"]
    operations = []
    for key in state["written_keys"]:
        if not any(key.Equals(previous) for previous, _ in before):
            operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DeleteEmbeddedDataOperation"))
            operation.Identifier = key
            operations.append(operation)
    if operations:
        state["store"].ExecuteOperations(state["document"], typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))
    if before:
        set_preset_data(state["store"], state["document"], before)
    current = [(item.Identifier, item.Data) for item in state["store"].GetAllStoredData(state["document"])]
    if not _preset_records_match(current, before):
        raise ValueError("Preset restoration failed readback")
    if profile_bytes() != state["profile_before"]:
        raise ValueError("User profile differs after preset restoration")
    del sc.sticky[PRESET_KEY]
    return {"preset_restored": True}


def prepare_capture(named_view):
    """Send the exact named camera, Rhino sun and named-view context to Enscape."""
    if sc.doc.NamedViews.FindByName(named_view) < 0:
        raise ValueError("Named view does not exist in the bound document")
    if any(obj.ObjectType == Rhino.DocObjects.ObjectType.ClipPlane for obj in sc.doc.Objects):
        raise ValueError("Clipping planes require an explicit capture context; this helper handles unclipped views")
    current = services()["state"].CurrentState
    provider = service_provider()
    constructor = find_type("Enscape.Rhino.Plugin.Views.RhinoView").GetConstructors()[0]
    args = []
    for parameter in constructor.GetParameters():
        if parameter.ParameterType.FullName == "System.String":
            args.append(named_view)
        elif parameter.ParameterType.FullName == "Enscape.Rhino.Plugin.Documents.RhinoDocument":
            args.append(current.RenderedDocument)
        else:
            service = provider.GetService(parameter.ParameterType)
            if service is None:
                raise ValueError("Missing Enscape service: " + parameter.ParameterType.FullName)
            args.append(service)
    state = sc.sticky[KEY]
    state["capture_view"] = constructor.Invoke(System.Array[System.Object](args))
    context = find_type("Enscape.Cad.RenderingSync.ViewportContext").GetMethod("CreateFromView").Invoke(
        None, System.Array[System.Object]([state["capture_view"]]))
    content = System.Activator.CreateInstance(find_type("Enscape.Cad.RenderingSync.ViewContent"))
    content.World = current.Session.CurrentWorld
    content.Camera = state["capture_view"].GetCamera()
    sun_parser = provider.GetService(find_type("Enscape.Rhino.Plugin.Views.RhinoSunParser"))
    content.SunPosition = sun_parser.ParseSun(sc.doc)
    planes = System.Activator.CreateInstance(find_type("Enscape.Cad.Views.GlobalSectionPlanes"))
    planes.SectionPlanes = System.Array.CreateInstance(find_type("GlmSharp.dvec4"), 0)
    content.GlobalSectionPlanes = planes
    state["context_task"] = current.LiveViewport.LiveViewport.InitializeContextAsync(context, content, True)
    return preparation_status()


def preparation_status():
    state = sc.sticky[KEY]
    task = state["context_task"]
    if task.IsFaulted or task.IsCanceled:
        raise RuntimeError(str(task.Exception))
    return {"preparation": str(task.Status)}


def screenshot_files():
    root = os.path.join(os.environ["TEMP"], "Enscape", "Screenshot")
    return {os.path.join(folder, name)
            for folder, _, names in os.walk(root)
            for name in names}


def start_hidden(renderer_process_id):
    """Hide this explicitly selected renderer host's window as it starts."""
    previous = sc.sticky.get(KEY)
    if previous and previous.get("timer") is not None:
        raise ValueError("Monitor already active; inspect status before starting again")
    process = System.Diagnostics.Process.GetProcessById(renderer_process_id)
    if process.ProcessName != "Enscape.RendererHost":
        raise ValueError("Selected process is not an Enscape renderer host")
    state = {"services": services(), "process": process, "handles": set()}
    timer = forms.Timer()
    timer.Interval = 100

    def hide_window(sender, args):
        if process.HasExited:
            sender.Stop()
            return
        process.Refresh()
        handle = process.MainWindowHandle.ToInt64()
        if handle:
            state["handles"].add(handle)
        for handle in state["handles"]:
            if ctypes.windll.user32.IsWindowVisible(handle):
                ctypes.windll.user32.ShowWindowAsync(handle, 0)

    timer.Tick += hide_window
    state["timer"] = timer
    state["hide_callback"] = hide_window
    sc.sticky[KEY] = state
    timer.Start()
    state["start_task"] = state["services"]["sync"].RequestStartRendererAsync()
    return status()


def status():
    state = sc.sticky[KEY]
    current = state["services"]["state"].CurrentState
    task = state["start_task"]
    return {
        "start_task": str(task.Status), "running": current.IsRunning,
        "visible_window_count": sum(bool(ctypes.windll.user32.IsWindowVisible(h))
                                    for h in state["handles"]),
        "error": str(task.Exception) if task.IsFaulted else None,
    }


def request_capture(output_path):
    state = sc.sticky[KEY]
    context_task = state.get("context_task")
    if context_task is None or str(context_task.Status) != "RanToCompletion":
        raise ValueError("Run prepare_capture(named_view) and wait for preparation to finish first")
    if "capture_task" in state:
        raise ValueError("A capture is already pending; collect it first")
    if not output_path.lower().endswith(".png"):
        raise ValueError("The Rhino capture route only supports PNG")
    if not os.path.isabs(output_path):
        raise ValueError("Output path must be absolute")
    ready = status()
    if ready["start_task"] != "RanToCompletion" or not ready["running"]:
        raise ValueError("Renderer startup has not completed")
    if os.path.exists(output_path):
        raise ValueError("Refusing to overwrite an existing output")
    current = state["services"]["state"].CurrentState
    args = System.Activator.CreateInstance(find_type(
        "Enscape.Cad.RenderingSync.TakeScreenshotRequestEventArgs"))
    args.LiveViewport = current.LiveViewport.LiveViewport
    screenshots = state["services"]["screenshots"]
    handler = screenshots.GetType().GetField("TakeScreenshotRequested", FLAGS).GetValue(screenshots)
    state["files_before"] = screenshot_files()
    state["output_path"] = output_path
    state["capture_started"] = time.time()
    handler.DynamicInvoke(System.Array[System.Object]([screenshots, args]))
    state["capture_task"] = args.WaitAsync()
    return {"capture_task": str(state["capture_task"].Status)}


def collect_capture():
    state = sc.sticky[KEY]
    task = state["capture_task"]
    if not task.IsCompleted:
        return {"capture_task": str(task.Status)}
    if task.IsFaulted:
        raise RuntimeError(str(task.Exception))
    if task.IsCanceled:
        raise RuntimeError("Capture was canceled")
    candidates = screenshot_files() - state["files_before"]
    folders = {os.path.dirname(path) for path in candidates}
    if len(folders) != 1:
        raise ValueError("Cannot identify one capture directory; source files left untouched")
    # Each native capture owns one new GUID directory. Find its main image by
    # the common filename prefix, retaining every channel suffix verbatim.
    mains = [path for path in candidates if path.lower().endswith(".png") and all(
        other == path or os.path.basename(other).startswith(os.path.splitext(os.path.basename(path))[0] + "_")
        for other in candidates)]
    if len(mains) != 1:
        raise ValueError("Cannot identify the main image and channels; source files left untouched")
    source_main = mains[0]
    source_stem = os.path.splitext(os.path.basename(source_main))[0]
    target_stem = os.path.splitext(state["output_path"])[0]
    files = []
    for source in sorted(candidates):
        bitmap = System.Drawing.Bitmap(source)
        try:
            pixels = [bitmap.Width, bitmap.Height]
            if min(pixels) < 500:
                raise ValueError("Capture is unexpectedly small")
        finally:
            bitmap.Dispose()
        suffix = os.path.basename(source)[len(source_stem):]
        target = target_stem + suffix
        if os.path.exists(target):
            raise ValueError("Refusing to overwrite capture output: " + target)
        files.append({"source": source, "output": target, "pixels": pixels})
    if any(item["pixels"] != files[0]["pixels"] for item in files):
        raise ValueError("Capture channel sizes differ; source files left untouched")
    os.makedirs(os.path.dirname(state["output_path"]), exist_ok=True)
    created = []
    try:
        for item in files:
            with open(item["output"], "xb") as target:
                created.append(item["output"])
                with open(item["source"], "rb") as source:
                    shutil.copyfileobj(source, target)
    except Exception:
        for path in created:
            os.remove(path)
        raise
    for item in files:
        os.remove(item["source"])
    folder = folders.pop()
    if not os.listdir(folder):
        os.rmdir(folder)
    result = {
        "capture_task": str(task.Status), "pixels": files[0]["pixels"],
        "output": state["output_path"], "files": files,
        "seconds": time.time() - state["capture_started"],
        **status(),
    }
    del state["capture_task"]
    return result


def stop_monitor():
    state = sc.sticky[KEY]
    if state.get("timer") is not None:
        state["timer"].Stop()
        state["timer"].Dispose()
        state["timer"] = None
