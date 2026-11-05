---
name: bc-enscape
description: >-
  Enscape renders from Rhino with the Enscape window hidden, driven through Rook, plus an optional
  sunlight overlay of an Enscape render on a Rhino screenshot. Use when the user asks for an Enscape
  render of a Rhino view, a sun or shadow study in Enscape, or a Rhino screenshot with an Enscape
  lighting overlay.
---
<!-- bc-enscape-skill-loaded-marker -->

# bc-enscape

Rook runs [enscape_background.py](scripts/enscape_background.py) inside Rhino;
[lighting_composite.py](scripts/lighting_composite.py) runs on the host for overlays. Codex and
Claude Code share this folder.

Needs Windows, a visible Rhino main window, a licensed Enscape and a GPU. The Enscape window is
hidden, not headless. The hooks are private Enscape plug-in internals, not the Chaos SDK, tested with
Enscape 4.22.0.1361: recheck them after an Enscape upgrade.

## Inputs from the request
Every value below comes from the user's request or the project; this skill fixes none of them.
Ask when one is missing.

| Input | Used in |
|---|---|
| Rhino document and named view | steps 1, 2 and 5 |
| Output size in pixels (width, height) | step 2, overlays only |
| Sun azimuth and altitude in degrees | step 5 |
| Output folder in the project | steps 5 and overlay |
| Overlay opacity, 0–1 | overlay |

## Workflow
Lock and bind (Rook) → view and preset (Rook) → find the renderer host (PowerShell) → start
(Rook) → sun and capture (Rook) → check and clean up → optional overlay (host Python).

Machine lock: if the ai-work-modes skill is installed, take its lock before step 1 and release it
after step 6. Without it, one agent works alone.

## Render
Each Rook call returns to Rhino's event loop. Poll in later calls; never block Rhino with `.Wait()`
or a loop. Limits and failures are in [Troubleshooting](#troubleshooting).

1. **Bind.** Ping Rook and list sessions. With several Rhino windows, bind the intended one with
   `rhino_set_active_instance`. Record its camera and sun. Never save the model unless asked.
2. **View and preset.** Restore the named view in the main viewport, not a floating one. Import the
   helper through `rhino_execute`:

   ```python
   import os, sys
   sys.path.append(os.path.expanduser(r"~\.claude\skills\bc-enscape\scripts"))
   import enscape_background as enscape
   ```

   For an overlay (orthographic view), call `enscape.configure_lighting(width_px, height_px)`. It
   selects a temporary white-mode, transparent preset at that size and keeps the previous presets
   to restore in step 6. For any other render, keep the user's preset and skip this call.
3. **Renderer host.** Find `Enscape.RendererHost` with `Get-CimInstance Win32_Process` and take
   the one whose `ParentProcessId` is the bound Rhino PID. Look it up each job; never reuse a PID.
4. **Start.** `enscape.start_hidden(renderer_process_id)`.
   Check: `enscape.status()` shows `start_task` `RanToCompletion` and `running` true.
5. **Sun and capture.** Set the sun with `sc.doc.Lights.Sun.SetPosition(azimuth_deg, altitude_deg)`
   after `import scriptcontext as sc`; turn on the sun or manual control if needed. Call
   `enscape.prepare_capture(named_view)`, which sends that view's camera and the Rhino sun to
   Enscape. Check: `enscape.preparation_status()` shows `preparation` `RanToCompletion`. Then call
   `enscape.request_capture(absolute_new_png_path)`.
   Check: `enscape.collect_capture()` returns the moved PNG and its pixel size.
6. **Check and clean up.** Open the image. Restore camera and sun. If step 2 set a preset, call
   `enscape.restore_preset()`; it raises if the readback differs. If this job started the renderer,
   close only that renderer's window, then call `enscape.stop_monitor()`. Outputs stay in the
   project, never in this skill.

## Sunlight overlay (optional)
In a new project output folder, prepare three images of the same size and projection, with the
names the script expects:
- `rhino-base.png`: the Rhino screenshot;
- `rhino-mask.png`: RGBA, geometry only, annotations hidden, restored afterwards;
- `enscape-orthographic.png`: the transparent white-mode render from step 5.

Run with a host Python that has `numpy`, `opencv-python`, `Pillow` and `psd-tools`:

```powershell
py -3.14 "$HOME\.claude\skills\bc-enscape\scripts\lighting_composite.py" "<project-output-folder>" --opacity <0-1>
```

It writes an overlay PNG, a composite PNG, a two-layer PSD, a comparison PNG and a validation JSON,
and refuses to overwrite existing outputs. Alignment allows only uniform scale and translation and
needs at least 99% overlap of the outer silhouettes. Openings enclosed by the silhouette, such as
glazing, are left out of that check and stay transparent in the overlay. Look at the composite
before reporting.

The overlay is a grayscale Multiply layer taken from white-mode shading: it only darkens the base
and is not a true light or shadow pass. Photoshop is not needed.

## Troubleshooting
| Symptom | Action |
|---|---|
| Start, preparation or capture not done after 120 s, or an error | Stop polling; read `enscape.status()` and the host's Enscape logs. Never resubmit blindly. |
| Startup stalls on a cached GPU-query timeout | Read the selected host's logs; restarting only that idle renderer host has recovered it. Never end Rhino or another session's host. |
| A window flashes at startup | Expected; the script hides it within 100 ms. |
| Internal export error at capture | The view reference is missing: run `prepare_capture` before `request_capture`. |
| `prepare_capture` refuses clipping planes | The helper handles unclipped views only; a clipped view needs its own capture context. |
| Annotations show in `rhino-mask.png` | Rhino keeps the current layer visible: make a non-annotation layer current before hiding. |
| Overlay alignment fails | Fix the camera so the Rhino and Enscape views match; never loosen the check. |

## Maintaining the skill
This folder is the Git repo `github.com/bai-ting-chen/bc-enscape` (private), shared by several
machines.
1. Before editing: `git pull --ff-only origin main`. If it refuses, stop and report; never force-push.
2. Identity, set once per clone: `git config user.name bai-ting-chen`,
   `git config user.email baitingchen.arch@gmail.com`.
3. After a change: commit (what changed and why), then `git push origin main`. Only method and code
   go in; never project files, renders or values from one scene (sizes, sun angles, view names).
