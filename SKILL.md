---
name: bc-enscape
description: >-
  Run Enscape renders from Rhino driven through Rook. Use when the user asks for an Enscape render
  of a Rhino view.
---
<!-- bc-enscape-skill-loaded-marker -->

# bc-enscape

Renders a Rhino named view in Enscape with the Enscape window hidden. Rook runs
[enscape_background.py](scripts/enscape_background.py) inside Rhino; settings start from the user's
sheet [render-defaults.md](render-defaults.md). Tested with Enscape 4.22.0.1361.

## Prerequisites
- Windows, a visible Rhino window with Rook, a licensed Enscape and a GPU.
- A Rhino named view; the sun position as azimuth and altitude in degrees.
- An output folder in the project; outputs never go in this skill.
- Machine lock: if the ai-work-modes skill is installed, take its lock before step 1 and release it
  after step 6.

## Workflow Steps
Each Rook call returns to Rhino's event loop: poll in later calls, never `.Wait()` or loop.

### Step 1: Bind
**Intent:** work on the intended Rhino. Ping Rook, list sessions, bind with
`rhino_set_active_instance`. Record its camera and sun.

### Step 2: Version and settings
**Intent:** load the helper, check the Enscape version, apply the sheet. Through `rhino_execute`:
```python
import os, sys
sys.path.append(os.path.expanduser(r"~\.claude\skills\bc-enscape\scripts"))
import enscape_background as enscape
enscape.check_version()
enscape.apply_defaults({})   # or the request's {key: value}, enum names
```
**Notes:**
- A different version: run the [version test](references/version-test.md) first.
- `pending_profile_restore` true: call `enscape.restore_settings()` before anything else.
- Restore the named view in the main viewport. Check the returned values.

### Step 3: Renderer host
**Intent:** find the renderer of the bound Rhino. With `Get-CimInstance Win32_Process`, take the
`Enscape.RendererHost` whose `ParentProcessId` is the Rhino PID. Look it up every job.

### Step 4: Start
**Intent:** start Enscape hidden. `enscape.start_hidden(pid)`; `enscape.status()` shows
`start_task` `RanToCompletion` and `running` true.

### Step 5: Sun and capture
**Intent:** render the view.
```python
import scriptcontext as sc
sc.doc.Lights.Sun.SetPosition(azimuth_deg, altitude_deg)   # turn the sun on if needed
enscape.prepare_capture(named_view)          # poll enscape.preparation_status()
enscape.request_capture(absolute_new_png_path)
enscape.collect_capture()                    # poll: files and pixel sizes
```

### Step 6: Restore
**Intent:** leave Rhino and Enscape as found. Open the image. Restore camera and sun.
`enscape.restore_settings()` restores presets and profile and raises one error naming every failed
readback. If this job started the renderer, close only its window, then `enscape.stop_monitor()`.

## Parameters to Expose
- `named_view`: the Rhino named view (no default; ask).
- `azimuth_deg`, `altitude_deg`: sun position (no default; ask).
- `output_folder`: in the project (no default; ask).
- `overrides`: `{key: value}` settings for this render only (default: `{}`; the sheet holds the
  rest). Names, types and bounds: [settings.md](references/settings.md).

## Gotchas
- Never save the Rhino model unless asked.
- Camera comes from the named view and sun from the Rhino sun, not from the sheet.
- Raytracing, DLSS and denoisers are user-wide and read at renderer start
  ([settings.md](references/settings.md)).
- Clipped views are refused for now.
- Rook resets `sys.path` between calls: keep the path lines in every call that imports. After a
  skill update in a running Rhino, `importlib.reload` the changed modules, then the facade.

## References
- [Settings](references/settings.md): the sheet, overrides, schema, user-wide settings, restore
- [Version test](references/version-test.md): after an Enscape update
- [Image overlay](references/image-overlay.md): stack a render, its channels and Rhino screenshots
- [Troubleshooting](references/troubleshooting.md): stalls, refusals, errors
- [Maintaining](references/skill-maintaining.md): Git, what goes in the skill

| Topic | Reference | Code |
|---|---|---|
| Render inside Rhino | this file | `scripts/enscape_background.py` (imports only), `enscape_bridge.py`, `enscape_renderer.py`, `enscape_capture.py` |
| Settings and sheet | `references/settings.md` | `render-defaults.md`, `scripts/enscape_sheet.py`, `enscape_settings.py`, `enscape_defaults.py`, `enscape_presets.py`, `enscape_general.py`, `enscape_profile.py` |
| Version test | `references/version-test.md` | `scripts/enscape_version.py`, `enscape_version_test.py` |
| Image overlay (host Python) | `references/image-overlay.md` | `scripts/image_overlay.py` |

## Example Output
A PNG at `absolute_new_png_path` at the sheet's resolution. With `ExportBackgroundMask` on, also
`_alphaMask`, `_depth`, `_materialId` and `_objectId` PNGs on the same grid. PNG only: the Rhino
capture route ignores `ScreenshotDefaultExtension`; no normal channel.
