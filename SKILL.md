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
Renders Rhino named views, or the current viewport, with the Enscape window hidden. Ask for any
missing input: views, sun azimuth and altitude in degrees, an output folder in the project. Save
the Rhino model only when asked: during a job it holds a changed sun, view and temporary Enscape
presets. Every step is a call below; write no other Rhino, Windows or shell code.

Enscape's tasks only progress while Rhino's event loop runs, between Rook calls: start a task in
one call and poll it in later calls. A `.Wait()` or a loop inside one call hangs Rhino.

1. **Bind** the intended Rhino (`rhino_set_active_instance`).
2. **Load and apply settings** through `rhino_execute` (Rook resets `sys.path`, so keep the path
   lines in every call). `apply_defaults` also records the camera, sun and settings for step 5:
   ```python
   import os, sys
   sys.path.append(os.path.expanduser(r"~\.claude\skills\bc-enscape\scripts"))
   import enscape_background as enscape
   enscape.check_version()
   enscape.apply_defaults({})   # or the request's {key: value}
   ```
   `pending_profile_restore` true: call `enscape.restore_settings()` first. `match` false: tell
   the user the installed and tested versions and let them decide whether to run a check
   (`enscape.version_test(new_folder)` after step 3, called until `complete`) or render anyway.
3. **Start:** `enscape.start_hidden()` finds this Rhino's own renderer and starts it; poll
   `enscape.status()` until `running` is true.
4. **Sun and capture:**
   ```python
   enscape.set_sun(azimuth_deg, altitude_deg)
   result = enscape.capture_views([("view-a", r"<project>\renders\view-a.png"),
                                   (None, r"<project>\renders\current.png")])
   ```
   Repeat the call in later Rook calls until `result["complete"]`. A failed view stops the job and
   is named; earlier images stay.
   - **Projection:** a named view keeps its own projection and lens (parallel, perspective or
     two-point). `None` is the current viewport: it takes the sheet's projection and field of view.
     A projection the user names overrides both.
   - **Section:** a view renders cut only by a clipping plane linked to it and turned on.
   - **Output:** PNG; with `ExportBackgroundMask` on, also `_alphaMask`, `_depth`, `_materialId`
     and `_objectId` PNGs on the same grid.
5. **Restore:** open the images; poll `enscape.restore_job()` until `complete`. It restores the
   camera, sun, settings and profile, closes the renderer this job started, and raises one error
   naming every readback that differs.

## Settings
- **Sheet:** every render starts from the user's sheet
  [view-setting-defaults.md](references/view-setting-defaults.md). A value the user names overrides
  that setting for that render only. Change the sheet only when the user asks.
- **Schema:** `enscape.settings_schema()` lists every setting of the installed Enscape: key, type,
  bounds or enum names, Enscape default and current value. Use it to name a setting the user
  describes in words. Pass enum names, not numbers.
- **Sources:** camera, projection and lens from the Rhino named view (the sheet's for `None`), sun
  position from the Rhino sun, the rest from the sheet and the request.
- **User-wide group:** Rendering (raytracing, DLSS, denoisers) lives in the user's Enscape profile
  and is read when the renderer host starts. A running host keeps its old values, even through
  `start_hidden()`: after changing them, poll `enscape.restart_renderer_host()` until `complete`.
  It restarts only this Rhino's renderer.
- **Restore:** `restore_job()` puts back the document presets and the profile. The profile backup
  is in `%LOCALAPPDATA%\bc-enscape\` and survives a Rhino restart; while it exists,
  `check_version()` reports `pending_profile_restore` true and `enscape.restore_settings()` clears it.

## Materials
List, create and edit Enscape materials, then call `enscape.refresh_materials()` before capturing:
Enscape keeps showing the old material otherwise. Calls, types and units:
[enscape-materials.md](references/enscape-materials.md).

## Image overlay
Stack an Enscape render, its channels and Rhino screenshots into one composite PNG and layered PSD
from a recipe JSON, on the host: [image-overlay.md](references/image-overlay.md).

## Troubleshooting
| Symptom | Action |
|---|---|
| Start, preparation or capture not done after 120 s, or an error | Stop polling; read `enscape.status()` and the host's Enscape logs. Never resubmit blindly. |
| Startup stalls on a cached GPU-query timeout | Poll `enscape.restart_renderer_host()`. Never end Rhino or another session's host. |
| A window flashes at startup | Expected; the script hides it within 100 ms. |
| Every Rook call hangs | A dialog is open in Rhino; ask the user to close it. |
| `apply_defaults` names a sheet line or a missing key | The sheet was edited wrongly: report the line; render nothing until it is fixed. |
| `restore_job` refuses the profile backup | The profile changed outside the job; the backup is kept. Tell the user; never overwrite their change. |
| Raytracing, DLSS or denoiser change has no effect | These are read at renderer start: poll `enscape.restart_renderer_host()`. |
| `capture_views` names a `failed_view` | Read `enscape.status()`; earlier images are kept. Fix it, then run a new list starting at that view. |
| A section shows no cut | No clipping plane is linked to that view and turned on. |
| A material change does not show | Call `enscape.refresh_materials()` before capturing. |
| Overlay alignment fails | Match the Rhino and Enscape cameras; never loosen the check. |
