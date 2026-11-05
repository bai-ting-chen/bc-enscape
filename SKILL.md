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

Tested with Enscape 4.22.0.1361.

## Running Enscape
Renders Rhino named views with the Enscape window hidden. Ask for any missing input: named views,
sun azimuth and altitude in degrees, an output folder in the project. Save the Rhino model only
when asked: during a job it holds a changed sun, view and temporary Enscape presets.

Enscape's tasks only progress while Rhino's event loop runs, between Rook calls: start a task in
one call and poll it in later calls. A `.Wait()` or a loop inside one call hangs Rhino. Nothing
done after 120 s: read `enscape.status()` and the Enscape logs; never resubmit blindly.

1. **Bind** the intended Rhino (`rhino_set_active_instance`); record its camera and sun.
2. **Load and apply settings** through `rhino_execute` (Rook resets `sys.path`, so keep the path
   lines in every call):
   ```python
   import os, sys
   sys.path.append(os.path.expanduser(r"~\.claude\skills\bc-enscape\scripts"))
   import enscape_background as enscape
   enscape.check_version()
   enscape.apply_defaults({})   # or the request's {key: value}
   ```
   `pending_profile_restore` true: call `enscape.restore_settings()` first. Installed version
   differs from the line above: on a scratch scene (one box, one named view), run steps 3–4 and
   `enscape.version_test(new_folder)` until `complete`; pass → update the line, fail → render nothing.
3. **Renderer host:** the `Enscape.RendererHost` whose `ParentProcessId` is the bound Rhino PID
   (`Get-CimInstance Win32_Process`), looked up every job.
4. **Start:** `enscape.start_hidden(pid)`; poll `enscape.status()` for `running` true.
5. **Sun and capture:**
   ```python
   import scriptcontext as sc
   sc.doc.Lights.Sun.SetPosition(azimuth_deg, altitude_deg)
   result = enscape.capture_views([("view-a", r"<project>\renders\view-a.png"),
                                   ("view-b", r"<project>\renders\view-b.png")])
   ```
   Repeat the call in later Rook calls until `result["complete"]`. A failed view stops the job and
   is named; earlier images stay. A named view saved with clipping planes renders as a section.
   Output is PNG; with `ExportBackgroundMask` on, also `_alphaMask`, `_depth`, `_materialId` and
   `_objectId` PNGs on the same grid.
6. **Restore:** open the images; restore camera and sun; `enscape.restore_settings()`. If this job
   started the renderer, close only its window, then `enscape.stop_monitor()`.

## Settings
Every render starts from the user's sheet [render-defaults.md](render-defaults.md); a value named
in the request overrides it for that render only. Details, the schema and restore:
[settings.md](references/settings.md).

## Materials
List, create and edit Enscape materials, then call `enscape.refresh_materials()` before capturing:
Enscape keeps showing the old material otherwise. Calls, types and units:
[enscape-materials.md](references/enscape-materials.md).

## Image overlay
Stack an Enscape render, its channels and Rhino screenshots into one composite PNG and layered PSD
from a recipe JSON, on the host: [image-overlay.md](references/image-overlay.md).
