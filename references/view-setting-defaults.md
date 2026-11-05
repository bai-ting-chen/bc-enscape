# Render defaults

The user's default Enscape settings; how renders use them is in [settings.md](settings.md).
Edit any **Value**; keep the key names. The **Allowed** column is from Enscape 4.22's schema
(`enscape.settings_schema()` is the authority); the version test checks every value against it.

Starting values are Enscape 4.22's factory defaults (261105).

## Style
| Key | Value | Allowed |
|---|---|---|
| RenderingStyle | NONE | NONE, WHITE, POLYSTYROL, LIGHT_VIEW |
| ArtisticMode | NONE | NONE, SKETCH, WATERCOLOR |
| Palette | BLACK_AND_WHITE | BLACK_AND_WHITE, GRAYSCALE, COLOR |
| HatchedShadows | false | true, false |
| TransparentGlass | true | true, false |
| SurfaceDetail | 50 | 0–100 % |
| PolystyrolTransmission | 50 | 0–100 % |
| Jitter | 0 | 0–100 % |
| ExtendedLines | 0 | 0–100 % |
| ColorGradient | 0 | 0–100 % |
| BleedingColor | 0 | 0–100 % |
| PaperModelFilterType | Exclusive | Exclusive, Inclusive |
| PaperModelFilterCategories | None | None, Vegetation, People, Vehicles, Furniture |
| PaperModelFilterInstances | "" | text |

## Outline
| Key | Value | Allowed |
|---|---|---|
| ArtisticOutlines | NORMAL | NONE, THIN, NORMAL, THICK |
| OutlineThickness | 0 | 0–100 % |

## Camera
Camera position comes from the Rhino named view.

| Key | Value | Allowed |
|---|---|---|
| ProjectionMode | PERSPECTIVE | PERSPECTIVE, TWO_POINT, ORTHOGRAPHIC |
| FieldOfView | 90 | 10–140 ° |
| DofStrength | 0 | 0–100 % |
| DofAutoFocus | true | true, false |
| DofFocalPoint | 2 | 0.1–100 m |

## Exposure
| Key | Value | Allowed |
|---|---|---|
| AutoExposure | false | true, false |
| ExposureBrightness | 50 | 0–100 % |

## Quality
| Key | Value | Allowed |
|---|---|---|
| RenderQuality | ULTRA | DRAFT, MEDIUM, HIGH, ULTRA |

## Corrections
| Key | Value | Allowed |
|---|---|---|
| AutoContrast | false | true, false |
| TonemapHighlights | 0 | −100–100 % |
| TonemapShadows | 0 | −100–100 % |
| Saturation | 100 | 0–200 % |
| ColorTemperature | 6600 | 1500–15000 K |

## Effects
| Key | Value | Allowed |
|---|---|---|
| MotionBlur | 0 | 0–100 % |
| BloomIntensity | 0 | 0–100 % |
| LensflareIntensity | 0 | 0–100 % |
| VignetteIntensity | 0 | 0–100 % |
| ChromaticAberration | 0 | 0–100 % |

## Fog
| Key | Value | Allowed |
|---|---|---|
| FogHeight | 1 | 1–10000 m |
| GodraysDensity | 0 | 0–100 % |

## Illumination
Sun position comes from the Rhino sun.

| Key | Value | Allowed |
|---|---|---|
| SunPower | 80 | 0–500 % |
| StarsMoonPower | 0 | 0–300 % |
| LightBrightness | 100 | 0–200 % |
| AmbientAmount | 100 | 0–100 % |
| ShadowSharpness | 75 | 0–100 % |
| LightViewAuto | true | true, false |
| LightViewMinimum | 500 | 0.1–9999 lux |
| LightViewMaximum | 90000 | 100–120000 lux |

## Wind
| Key | Value | Allowed |
|---|---|---|
| WindIntensity | 0 | 0–100 % |
| WindDirectionAngle | 0 | 0–360 ° |

## Sky source
| Key | Value | Allowed |
|---|---|---|
| HorizonSource | whiteGround | Enscape horizon name |
| HorizonRotation | 0 | 0–360 ° |
| WhiteBackground | true | true, false |
| SkyboxFile | "" | file path |
| SkyboxUseSunSpot | false | true, false |
| SkyboxNormalize | true | true, false |
| SkyboxNormalizeBrightness | 2000 | 0.1–250000 lux |
| MoonSize | 100 | 100–1000 % |

## Clouds
| Key | Value | Allowed |
|---|---|---|
| CloudDensity | 0 | 0–100 % |
| CloudVariety | 0 | 0–100 % |
| CloudCirrusAmount | 0 | 0–100 % |
| CloudContrails | 0 | 0–30 |
| CloudX | 0 | 0–10000 m |
| CloudY | 0 | 0–10000 m |

## Output
Format is always PNG (Enscape's Rhino capture route). File names and folders come from the capture
call, so Enscape's own screenshot folder settings are not in this sheet.

| Key | Value | Allowed |
|---|---|---|
| ScreenshotResolution | ULTRA_HD | WINDOW, _1024, HD, FULL_HD, ULTRA_HD, CUSTOM |
| ScreenshotResolutionX | 3840 | 500–8192 px; used when CUSTOM |
| ScreenshotResolutionY | 2160 | 500–8192 px; used when CUSTOM |
| ScreenshotViewportAspectRatio | false | true, false |
| ApplyAlphaChannelDuringImageExport | false | true, false |
| ExportBackgroundMask | false | true, false; true also writes alpha mask, depth, material ID and object ID PNGs |
| ScreenshotDepthRange | 20 | 1–10000 m |
| ScreenshotPositionLayerFormat | NONE | NONE, LINEAR_DEPTH, WORLD_POSITION |
| ScreenshotEdgeDetection | false | true, false |
| PanoramaResolution | _4096 | _2048, _4096, _8192 |
| RecordBitrateMode | MAXIMUM | EMAIL, WEB, BLURAY, MAXIMUM, LOSSLESS |
| RecordFramesPerSecond | 30 | frames per second |

## Rendering (general, user-wide)
Stored in the user's Enscape profile, not the model ([settings.md](settings.md)).

| Key | Value | Allowed |
|---|---|---|
| RtxRaytracing | true | true, false |
| UseRaytracedSunShadows | false | true, false |
| UseRayTracedArtificialLights | false | true, false |
| UseNvidiaGlobalIlluminationDenoiser | true | true, false |
| UseNvidiaShadowDenoiser | true | true, false |
| UseDlss | true | true, false |
| AutoResolution | true | true, false |
| AutoUpscaleDuringCapturing | true | true, false |
| GrassRendering | true | true, false |
