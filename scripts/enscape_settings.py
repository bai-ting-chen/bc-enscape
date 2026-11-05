"""Discover and validate installed Enscape settings."""
import System
import base64
import json
import math
import subprocess
from enscape_bridge import ACTIVE_PRESET_ID, embedded_presets
from enscape_profile import profile_bytes, profile_path
from enscape_version import check_version


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
