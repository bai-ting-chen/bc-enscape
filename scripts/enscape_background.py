"""Experimental Enscape 4.22 bridge hooks, called inside Rhino through Rook.

These are installed plug-in internals, not the public Chaos SDK. Start/capture
are asynchronous: return to Rhino's event loop, then call collect_capture().
"""
import ctypes
import base64
import json
import math
import os
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
ACTIVE_PRESET_ID = "d8e4b713-533b-421b-8b11-a961385e4b37"
FLAGS = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance

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
    throw "Enscape 4.22 setting type missing: $name"
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
    "LightBrightness": (0, 200, "%"), "WindDirectionAngle": (None, 360, "degrees"),
    "HorizonRotation": (None, 360, "degrees"),
    "SkyboxNormalizeBrightness": (0.1, 250000, "lux"), "MoonSize": (100, 1000, "%"),
    "CloudContrails": (None, 30, "count"), "CloudX": (0, 10000, "m"),
    "CloudY": (None, 10000, "m"), "ScreenshotDepthRange": (1, 10000, "m"),
    "ScreenshotResolutionX": (500, 8192, "px"), "ScreenshotResolutionY": (500, 8192, "px"),
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
    version = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin").Assembly.GetName().Version
    if str(version) != "4.22.0.1361":
        raise ValueError("Unverified Enscape version: " + str(version) + "; verified: 4.22.0.1361")
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
    return {"categories": categories, "external": {
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
            if not isinstance(value, str):
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


def configure_lighting(width, height):
    """Select white orthographic lighting; caller supplies both pixel dimensions."""
    return configure_preset({"RenderingStyle": "WHITE", "ProjectionMode": "ORTHOGRAPHIC",
        "ScreenshotResolution": "CUSTOM", "ScreenshotResolutionX": width,
        "ScreenshotResolutionY": height, "ScreenshotViewportAspectRatio": False,
        "ApplyAlphaChannelDuringImageExport": True, "ExportBackgroundMask": False,
        "WhiteBackground": True})


def configure_general(settings):
    """Temporarily edit profile settings, preserving the file's original bytes.

    Enscape loads these values at host initialization. This edits the profile,
    not live hosts; restarting another document's host is never implicit.
    """
    if GENERAL_KEY in sc.sticky:
        raise ValueError("Temporary general settings already exist; restore them first")
    if PRESET_KEY in sc.sticky:
        raise ValueError("Restore the temporary visual preset before editing the profile")
    changes = validate_settings(settings, general=True)
    before = profile_bytes()
    profile = json.loads(before.decode("utf-8-sig")) if before is not None else {"Version": 3, "Settings": {}}
    for key, value in changes.items():
        profile["Settings"][key] = {"Value": value}
    after = json.dumps(profile, indent=2).encode("utf-8")
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
    state = sc.sticky[GENERAL_KEY]
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
    del sc.sticky[GENERAL_KEY]
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
    if len(current) != len(before) or any(not any(key.Equals(old) and value == data for old, data in before) for key, value in current):
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
