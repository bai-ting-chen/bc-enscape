---
name: bc-enscape
description: >-
  Run Enscape renders from Rhino driven through Rook. Use when the user asks for an Enscape render
  of a Rhino view, and also whenever they mention Enscape together with Rhino: a sun or shadow
  study, a section or several views rendered in one go, a change to Enscape render settings or
  their defaults, creating or editing Enscape materials, depth, material or object ID channels,
  or overlaying an Enscape render or its channels on a Rhino screenshot, even if they do not say
  "render".
---
<!-- bc-enscape-skill-loaded-marker -->

# bc-enscape

Renders a Rhino named view in Enscape with the Enscape window hidden. Rook runs
[enscape_background.py](scripts/enscape_background.py) inside Rhino; settings start from the user's
sheet [render-defaults.md](render-defaults.md).

Tested with Enscape 4.22.0.1361.

## Prerequisites
- Windows, a visible Rhino window with Rook, a licensed Enscape and a GPU.
- A Rhino named view; the sun position as azimuth and altitude in degrees.
- An output folder in the project; outputs never go in this skill.
- Machine lock: if the ai-work-modes skill is installed, take its lock before step 1 and release it
  after step 6.

## Workflow Steps
Enscape's start, preparation and capture tasks only progress while Rhino's event loop runs, which
happens between Rook calls. So start a task in one call and poll for it in later calls: a `.Wait()`
or a loop inside one call holds the event loop, and Rhino hangs.

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
`Enscape.RendererHost` whose `ParentProcessId` is the Rhino PID. Look it up every job: other Rhino
windows have their own hosts, and a PID from an earlier job may now belong to another one.

### Step 4: Start
**Intent:** start Enscape hidden. `enscape.start_hidden(pid)`; `enscape.status()` shows
`start_task` `RanToCompletion` and `running` true.

### Step 5: Sun and capture
**Intent:** render the views, in order, with one renderer start and one restore.
```python
import scriptcontext as sc
sc.doc.Lights.Sun.SetPosition(azimuth_deg, altitude_deg)   # turn the sun on if needed
views = [("view-a", r"<project>\renders\view-a.png"),      # absolute new PNG paths
         ("view-b", r"<project>\renders\view-b.png")]
result = enscape.capture_views(views)
```
**Notes:**
- Call `capture_views` again with the same list in later Rook calls until `result["complete"]` is
  true; read `passed`, `failed_view` and `views` (files and pixel sizes per view).
- A failed view stops the job and is named; earlier images stay. The camera is restored at the end.
- A named view saved with clipping planes renders as a section: Enscape reads the planes from the
  view itself, so planes elsewhere in the document do not cut it.

### Step 6: Restore
**Intent:** leave Rhino and Enscape as found: the sheet's values are written into the document's
presets and the user's own Enscape profile, so without this step the next render, and the user's
own Enscape sessions, would inherit them. Open the image. Restore camera and sun.
`enscape.restore_settings()` restores presets and profile and raises one error naming every failed
readback. If this job started the renderer, close only its window, then `enscape.stop_monitor()`.

## Parameters to Expose
- `views`: Rhino named views and their output PNGs, in order (no default; ask).
- `azimuth_deg`, `altitude_deg`: sun position (no default; ask).
- `output_folder`: in the project (no default; ask).
- `overrides`: `{key: value}` settings for this render only (default: `{}`; the sheet holds the
  rest). Names, types and bounds: [settings.md](references/settings.md).

## Gotchas
- Save the Rhino model only when asked: it is the user's project file, and during a job it holds a
  changed sun, view and temporary Enscape presets.
- Camera comes from the named view and sun from the Rhino sun, not from the sheet.
- Raytracing, DLSS and denoisers are user-wide and read at renderer start
  ([settings.md](references/settings.md)).
- After creating, assigning or editing a material, call `enscape.refresh_materials()` before the
  capture: Enscape's live sync keeps the old material otherwise.
- `check_version()` finds the tested version only on its own line, as above.
- Rook resets `sys.path` between calls: keep the path lines in every call that imports. After a
  skill update in a running Rhino, `importlib.reload` the changed modules, then the facade.

## References
- [Settings](references/settings.md): the sheet, overrides, schema, user-wide settings, restore
- [Version test](references/version-test.md): after an Enscape update
- [Enscape materials](references/enscape-materials.md): list, create and edit materials before a render
- [Image overlay](references/image-overlay.md): stack a render, its channels and Rhino screenshots
- [Troubleshooting](references/troubleshooting.md): stalls, refusals, errors
- [Maintaining](references/skill-maintaining.md): Git, what goes in the skill

| Topic | Reference | Code |
|---|---|---|
| Render inside Rhino | this file | `scripts/enscape_background.py` (imports only), `enscape_bridge.py`, `enscape_renderer.py`, `enscape_capture.py`, `enscape_views.py` |
| Settings and sheet | `references/settings.md` | `render-defaults.md`, `scripts/enscape_sheet.py`, `enscape_settings.py`, `enscape_defaults.py`, `enscape_presets.py`, `enscape_general.py`, `enscape_profile.py` |
| Enscape materials | `references/enscape-materials.md` | `scripts/enscape_materials.py` |
| Version test | `references/version-test.md` | `scripts/enscape_version.py`, `enscape_version_test.py` |
| Image overlay (host Python) | `references/image-overlay.md` | `scripts/image_overlay.py` |

## Example Output
One PNG per view at the sheet's resolution. With `ExportBackgroundMask` on, also
`_alphaMask`, `_depth`, `_materialId` and `_objectId` PNGs on the same grid. PNG only: the Rhino
capture route ignores `ScreenshotDefaultExtension`; no normal channel.
